"""A restricted public entrance; the poker backend remains private on loopback."""
from contextlib import asynccontextmanager
import hashlib
import hmac
import ipaddress
from http.cookies import CookieError, SimpleCookie
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

PREFIX = "/pokerbench"
COOKIE = "pokerbench_session"
ROOT = Path(__file__).resolve().parent
MAX_BODY = 32768


def create_app(*, upstream="http://127.0.0.1:8097", database=None, invite=None, secret=None,
               table_budget=None, transport=None):
    # Legacy invite/table_budget arguments are ignored. The backend owns invitations
    # and the atomic per-account allowance; this database only throttles requests.
    secret = secret or os.environ.get("PUBLIC_SESSION_SECRET") or secrets.token_hex(32)
    db = sqlite3.connect(database or ROOT / "public-access.sqlite", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS attempts(address TEXT, kind TEXT, count INTEGER, resets REAL,
            PRIMARY KEY(address,kind));
    """)
    # Backend provider limits govern active calls; streams must not exhaust a
    # shared connection cap and queue otherwise independent players/providers.
    client = httpx.AsyncClient(base_url=upstream, timeout=30, transport=transport,
                               limits=httpx.Limits(max_connections=None, max_keepalive_connections=20),
                               follow_redirects=False, trust_env=False)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await client.aclose()
            db.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db = db

    def error(message, status=403):
        return JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store"})

    def limited(request, kind, limit, seconds):
        address = request.client.host if request.client else "unknown"
        key = hmac.new(secret.encode(), address.encode(), hashlib.sha256).hexdigest()
        now = time.time()
        with db:
            db.execute("DELETE FROM attempts WHERE resets<=?", (now,))
            row = db.execute("SELECT count FROM attempts WHERE address=? AND kind=?", (key, kind)).fetchone()
            if row and row[0] >= limit:
                return True
            db.execute("""INSERT INTO attempts VALUES(?,?,1,?)
                ON CONFLICT(address,kind) DO UPDATE SET count=count+1""", (key, kind, now + seconds))
        return False

    def origin_ok(request):
        try:
            origin = urlsplit(request.headers.get("origin", ""))
            return (request.headers.get("sec-fetch-site") != "cross-site"
                    and origin.scheme in ("http", "https")
                    and origin.scheme == request.url.scheme
                    and origin.netloc == request.headers.get("host")
                    and not (origin.path or origin.query or origin.fragment))
        except ValueError:
            return False

    def upstream_headers(request, *, write=False):
        # Never forward admin bearer credentials, caller proxy headers or arbitrary cookies.
        names = ("content-type", "origin", "host", "x-player-token")
        if write:
            names += ("x-jev-key", "x-deepseek-key", "x-agent-keys")
        headers = {k: request.headers[k] for k in names if k in request.headers}
        session = request.cookies.get(COOKIE, "")
        if re.fullmatch(r"[A-Za-z0-9_-]+", session):
            headers["cookie"] = f"{COOKIE}={session}"
        # This public entrance is HTTPS-only; never copy the caller's value.
        headers["x-forwarded-proto"] = "https"
        # Overwrite caller headers with the peer resolved by the trusted ingress.
        # Uvicorn accepts these only from this loopback gateway, so backend auth
        # limits apply per visitor instead of to every user as one shared IP.
        if request.client:
            try:
                headers['x-forwarded-for'] = str(ipaddress.ip_address(request.client.host))
            except ValueError:
                pass
        return headers

    async def send(request, path, body=None, streaming=False, method=None):
        method = method or request.method
        # Leave headroom beyond 15s room calls and 30s advisor calls. Streams use
        # an idle-read timeout, not a total lifetime limit (backend sends heartbeats).
        read_timeout = 60 if streaming else 45 if path.startswith("/api/advisor/") else 30
        timeout = httpx.Timeout(connect=5, read=read_timeout, write=10, pool=5)
        headers = upstream_headers(request, write=method != "GET")
        query = request.url.query if method == request.method else ""
        outgoing = client.build_request(method, path + ("?" + query if query else ""),
                                        headers=headers, content=body, timeout=timeout)
        # AsyncClient remembers Set-Cookie responses. Never let its shared jar
        # authenticate a different visitor who has no session cookie.
        outgoing.headers.pop("cookie", None)
        if "cookie" in headers:
            outgoing.headers["cookie"] = headers["cookie"]
        return await client.send(outgoing, stream=streaming)

    def response_headers(upstream_response):
        headers = {k: v for k, v in upstream_response.headers.items()
                   if k.lower() == "content-type"}
        headers["cache-control"] = "no-store"
        return headers

    def session_cookies(response, upstream_response):
        for value in upstream_response.headers.get_list("set-cookie"):
            cookies = SimpleCookie()
            try:
                cookies.load(value)
            except CookieError:
                continue
            if COOKIE not in cookies:
                continue
            cookie = cookies[COOKIE]
            if not re.fullmatch(r"[A-Za-z0-9_-]*", cookie.value):
                continue
            cookie["path"] = PREFIX + "/"
            cookie["domain"] = ""
            cookie["secure"] = True
            cookie["httponly"] = True
            cookie["samesite"] = cookie["samesite"] or "lax"
            response.headers.append("set-cookie", cookie.OutputString())
        return response

    def result(upstream_response, body=None):
        content = upstream_response.content if body is None else body
        response = Response(content, upstream_response.status_code, headers=response_headers(upstream_response))
        return session_cookies(response, upstream_response)

    @app.middleware("http")
    async def security_headers(request, call_next):
        try:
            response = await call_next(request)
        except httpx.HTTPError:
            response = error("The poker service is temporarily unavailable. Please retry later.", 503)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.get("/access/status")
    async def access_status():
        return JSONResponse({"allowed": True}, headers={"Cache-Control": "no-store"})

    @app.post("/access/unlock")
    async def unlock():
        return error("Use /api/auth/invite for account invitations.", 410)

    @app.get("/access/boot.js")
    async def boot():
        return error("The legacy invitation prompt has been retired.", 410)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        path = "/" + path
        if request.method == "GET":
            allowed = path == "/" or bool(re.fullmatch(
                r"/assets/[A-Za-z0-9_.-]+|/api/(health|entries|defaults|series|runs|auth/(me|budget)|rooms/models|rooms/mine)|"
                r"/api/runs/[a-f0-9]{12}(/(hands(/\d+)?|stream))?|/api/rooms/[a-f0-9]{12}/state", path))
            if not allowed:
                return error("This endpoint is not available through the public entrance.", 404)
            response = await send(request, path, streaming=path.endswith("/stream"))
            if path.endswith("/stream"):
                if response.status_code != 200:
                    try:
                        await response.aread()
                        return result(response)
                    finally:
                        await response.aclose()

                async def events():
                    try:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                    except httpx.HTTPError:
                        # Headers have already been sent; close so the browser can reconnect.
                        return
                    finally:
                        await response.aclose()

                headers = response_headers(response)
                headers["X-Accel-Buffering"] = "no"
                return session_cookies(StreamingResponse(events(), status_code=response.status_code,
                    media_type="text/event-stream", headers=headers), response)
            content = response.content
            if path == "/" and response.status_code == 200:
                content = content.replace(b'"/assets/', b'"/pokerbench/assets/')
            elif path.startswith("/assets/") and path.endswith(".js"):
                # Adapt only root-relative app URLs, leaving the private build untouched.
                content = re.sub(rb'([\x22\x27\x60])/(api|assets)/', rb'\1/pokerbench/\2/', content)
                # The API helper also checks paths with anchored regex literals.
                content = content.replace(rb'/^\/api\/', rb'/^\/pokerbench\/api\/')
            return result(response, content)

        allowed = (request.method == "POST" and (path in (
            "/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/invite",
            "/api/advisor/preview", "/api/advisor/advise", "/api/rooms")
            or re.fullmatch(r"/api/rooms/[a-f0-9]{12}/(claim|visit|action|kick/[a-z0-9_-]{1,40})", path)
            or re.fullmatch(r"/api/runs/[a-f0-9]{12}/(start|pause)", path))
            or request.method == "DELETE" and re.fullmatch(r"/api/rooms/[a-f0-9]{12}", path))
        if not allowed:
            return error("Management and direct model calls are disabled on the public entrance.")
        if not origin_ok(request):
            return error("Please submit from this site.")
        if limited(request, "writes", 60, 60):
            return error("Too many actions. Please slow down.", 429)
        try:
            length = int(request.headers.get("content-length", "0"))
            if length < 0:
                raise ValueError()
        except ValueError:
            return error("Invalid content length.", 400)
        if length > MAX_BODY:
            return error("Request body is too large.", 413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY:
                return error("Request body is too large.", 413)
            body.extend(chunk)
        if path == "/api/auth/register" and limited(request, "registrations", 10, 86400):
            return error("Too many registrations from this network. Please contact the team.", 429)
        if path.startswith("/api/advisor/") and limited(request, path, 6, 60):
            return error("Too many advisor requests. Please wait a minute.", 429)

        match = re.fullmatch(r"/api/runs/([a-f0-9]{12})/(start|pause)", path)
        if match:
            # These URLs also control formal benchmarks. Check only room type;
            # old rooms need no gateway allocation and backend owns authorization/budget.
            run_response = await send(request, "/api/runs/" + match[1], method="GET")
            if run_response.status_code != 200:
                return result(run_response)
            try:
                run = run_response.json()
                is_room = isinstance(run, dict) and run.get("human_player_id") and not run.get("series_id")
            except ValueError:
                return error("Invalid room response from the poker service.", 502)
            if not is_room:
                return error("Only player rooms can be controlled through the public entrance.")
        return result(await send(request, path, bytes(body)))

    return app


def from_env():
    return create_app()

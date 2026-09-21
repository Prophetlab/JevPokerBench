"""Offline gateway contract tests. All upstream traffic uses httpx.MockTransport."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location(
    "public_release_gateway", Path(__file__).resolve().parents[2]/"deploy/public_gateway.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)

PREFIX = "/pokerbench"
ORIGIN = "https://public.test"
ROOM = "000000000001"
FORMAL = "ffffffffffff"
SESSION = "offline-test-session"
KEYS = {"X-Jev-Key": "offline-test-jev", "X-DeepSeek-Key": "offline-test-deepseek",
        "X-Agent-Keys": '{"custom-a":"offline-agent-a", "custom-b":"offline-agent-b"}'}
KEY_HEADERS = {name.lower() for name in KEYS}
WRITE_PATHS = [
    ("POST", "/api/auth/register"), ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"), ("POST", "/api/auth/invite"),
    ("POST", "/api/rooms"), ("POST", "/api/advisor/preview"),
    ("POST", "/api/advisor/advise"),
    *[("POST", f"/api/rooms/{ROOM}/{action}") for action in ("claim", "visit", "action", "kick/bot-1")],
    *[("POST", f"/api/runs/{ROOM}/{action}") for action in ("start", "pause")],
    ("DELETE", f"/api/rooms/{ROOM}"),
]
READ_PATHS = [
    "/", "/assets/test.js", "/assets/test.css", "/api/health", "/api/entries",
    "/api/defaults", "/api/series", "/api/runs", "/api/auth/me", "/api/auth/budget",
    "/api/rooms/models", "/api/rooms/mine", f"/api/rooms/{ROOM}/state",
    f"/api/runs/{ROOM}", f"/api/runs/{ROOM}/hands", f"/api/runs/{ROOM}/hands/1",
    f"/api/runs/{ROOM}/stream",
]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_INVITE_CODE", raising=False)
    monkeypatch.delenv("PUBLIC_SESSION_SECRET", raising=False)
    calls, overrides = [], {}

    def backend(request):
        calls.append(request)
        path = request.url.path
        override = overrides.get((request.method, path))
        if override is not None:
            return override(request)
        if path == "/":
            return httpx.Response(200, text='<html><head><script src="/assets/test.js"></script></head></html>',
                                  headers={"Content-Type": "text/html"})
        if path == "/assets/test.js":
            return httpx.Response(200, text='fetch("/api/runs");const x=`/api/rooms/${id}`;const y=\'/assets/test.css\';',
                                  headers={"Content-Type": "application/javascript"})
        if path in ("/api/auth/register", "/api/auth/login"):
            return httpx.Response(200, json={"user": {"id": "test-player"}}, headers=[
                ("Set-Cookie", f"pokerbench_session={SESSION}; Path=/; HttpOnly; Secure; SameSite=Lax"),
                ("Set-Cookie", "pb_public_access=retired; Path=/"),
                ("Set-Cookie", "admin=not-public; Path=/"),
            ])
        if path == "/api/auth/logout":
            return httpx.Response(200, json={"ok": True}, headers={
                "Set-Cookie": 'pokerbench_session=""; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax'})
        if path == "/api/auth/me":
            return httpx.Response(200, json={"user": "test-player" if request.headers.get("cookie") else None})
        if path == "/api/auth/budget":
            return httpx.Response(200, json={"limit_cny": 5, "remaining_cny": 5})
        if path == f"/api/runs/{ROOM}":
            return httpx.Response(200, json={"id": ROOM, "human_player_id": "human-player", "run_budget_cny": 50})
        if path == f"/api/runs/{FORMAL}":
            return httpx.Response(200, json={"id": FORMAL, "human_player_id": None})
        if path.endswith("/stream"):
            return httpx.Response(200, content=b": heartbeat\n\ndata: {}\n\n", headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={"ok": True})

    app = gateway.create_app(database=tmp_path / "gateway.sqlite", transport=httpx.MockTransport(backend))
    outer = FastAPI()
    outer.mount(PREFIX, app)
    # The mount reproduces nginx's public prefix and exercises real cookie paths.
    with TestClient(app), TestClient(outer, base_url=ORIGIN) as client:
        yield client, app, calls, overrides


def write(client, path, method="POST", headers=None, **kwargs):
    return client.request(method, PREFIX + path, headers={"Origin": ORIGIN, **(headers or {})}, **kwargs)


def test_site_opens_without_invite_and_keeps_subpath_rewrite(setup):
    client, _, calls, _ = setup
    page = client.get(PREFIX + "/")
    assert page.status_code == 200
    assert '"/pokerbench/assets/test.js"' in page.text
    assert "boot.js" not in page.text and "<script src=\"/pokerbench/access/" not in page.text
    script = client.get(PREFIX + "/assets/test.js").text
    assert '"/pokerbench/api/runs"' in script
    assert '`/pokerbench/api/rooms/' in script
    assert "'/pokerbench/assets/test.css'" in script
    calls.clear()
    assert client.get(PREFIX + "/access/status").json() == {"allowed": True}
    assert write(client, "/access/unlock", json={"code": "obsolete-test-value"}).status_code == 410
    assert client.get(PREFIX + "/access/boot.js").status_code == 410
    assert not calls


def test_registration_login_openplay_and_backend_five_budget_without_invite(setup):
    client, app, calls, _ = setup
    assert write(client, "/api/auth/register", json={"id": "test-player", "password": "offline-only"}).status_code == 200
    assert write(client, "/api/auth/login", json={"id": "test-player", "password": "offline-only"}).status_code == 200
    assert client.get(PREFIX + "/api/auth/me").json()["user"] == "test-player"
    assert client.get(PREFIX + "/api/auth/budget").json()["limit_cny"] == 5
    for model in ("jev", "local"):
        body = {"model_ids": [model], "run_budget_cny": 100}
        assert write(client, "/api/rooms", json=body).status_code == 200
        assert json.loads(calls[-1].content) == body
    assert write(client, "/api/auth/invite", json={"code": "offline-invite"}).status_code == 200
    assert len([r for r in calls if r.url.path == "/api/auth/budget"]) == 1
    assert not app.state.db.execute("SELECT name FROM sqlite_master WHERE name='allocation'").fetchall()
    assert all("pb_public_access" not in r.headers.get("cookie", "") for r in calls)


@pytest.mark.parametrize("path", READ_PATHS)
def test_allowed_reads_strip_personal_keys_and_authorization(setup, path):
    client, _, calls, _ = setup
    response = client.get(PREFIX + path, headers={**KEYS, "Authorization": "Bearer offline-admin"})
    assert response.status_code == 200
    assert calls[-1].method == "GET" and calls[-1].url.path == path
    assert not ({"authorization"} | KEY_HEADERS) & set(calls[-1].headers)


@pytest.mark.parametrize("method,path", WRITE_PATHS)
def test_only_allowed_writes_receive_personal_keys(setup, method, path):
    client, _, calls, _ = setup
    response = write(client, path, method, headers=KEYS, json={})
    assert response.status_code == 200
    assert calls[-1].method == method and calls[-1].url.path == path
    for name, value in KEYS.items():
        assert calls[-1].headers[name] == value
    for request in calls[:-1]:
        assert request.method == "GET"
        assert not KEY_HEADERS & set(request.headers)


def test_custom_agents_and_key_mapping_pass_through_unchanged(setup):
    client, _, calls, _ = setup
    # Backend owns the custom-agent schema and endpoint/model validation.
    body = b'{"custom_agents":[{"id":"custom-a", "endpoint":"https://agent.invalid/v1", "model":"offline-model"}]}'
    response = write(client, "/api/rooms", headers={**KEYS, "Content-Type": "application/json"}, content=body)
    assert response.status_code == 200
    assert len(calls) == 1 and calls[0].content == body
    assert calls[0].headers["x-agent-keys"] == KEYS["X-Agent-Keys"]
    # A later visitor without keys must not inherit any previous key headers.
    assert write(client, "/api/rooms", json={}).status_code == 200
    assert not KEY_HEADERS & set(calls[-1].headers)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/auth/directory"), ("GET", f"/api/runs/{ROOM}/export"),
    ("GET", f"/api/runs/{FORMAL}/export"), ("GET", "/v1/models"),
    ("GET", "/api/admin"), ("GET", "/openapi.json"),
    ("POST", "/api/runs"), ("POST", "/api/series"),
    ("POST", f"/api/series/{FORMAL}/start"), ("POST", f"/api/series/{FORMAL}/pause"),
    ("PATCH", f"/api/runs/{ROOM}/limit"), ("DELETE", f"/api/runs/{ROOM}"),
    ("PUT", "/api/entries"), ("POST", "/api/entries"), ("DELETE", "/api/entries"),
    ("PUT", "/api/defaults"), ("PATCH", "/api/defaults"), ("POST", "/api/defaults"),
    ("POST", "/v1/systemone"), ("POST", "/v1/chat/completions"),
    ("POST", "/api/rooms/models"), ("PUT", "/api/rooms"),
    ("GET", "/api/auth/invite"), ("POST", "/api/auth/budget"),
    ("POST", f"/api/rooms/{ROOM}/action/extra"),
])
def test_management_directory_export_and_unknown_routes_never_forward(setup, method, path):
    client, _, calls, _ = setup
    response = write(client, path, method, headers={**KEYS, "Authorization": "Bearer offline-admin"})
    assert response.status_code in (403, 404, 405)
    assert not calls


@pytest.mark.parametrize("action", ["start", "pause"])
def test_formal_benchmark_controls_are_blocked_even_with_spoofed_admin(setup, action):
    client, _, calls, _ = setup
    response = write(client, f"/api/runs/{FORMAL}/{action}", headers={**KEYS, "Authorization": "Bearer offline-admin"}, json={})
    assert response.status_code == 403
    assert [(r.method, r.url.path) for r in calls] == [("GET", f"/api/runs/{FORMAL}")]
    assert not ({"authorization"} | KEY_HEADERS) & set(calls[0].headers)


def test_old_rooms_continue_without_allocation_or_budget_checks(setup):
    client, app, calls, _ = setup
    # Existing allocation records must have no influence on continued play.
    app.state.db.execute("CREATE TABLE allocation(run_id TEXT, budget REAL, status TEXT)")
    app.state.db.execute("INSERT INTO allocation VALUES(?,0,'rejected')", (ROOM,))
    for path in (f"/api/runs/{ROOM}/start", f"/api/runs/{ROOM}/pause", f"/api/rooms/{ROOM}/action"):
        assert write(client, path, headers={"X-Player-Token": "offline-seat"}, json={}).status_code == 200
    assert all(r.url.path != "/api/auth/budget" for r in calls)
    assert len(calls) == 5
    assert all(r.headers["x-player-token"] == "offline-seat" for r in calls)


@pytest.mark.parametrize("origin", ["https://evil.test", "http://public.test", "null", "", "https://[", "https://public.test/path"])
def test_cross_origin_writes_are_rejected_before_upstream(setup, origin):
    client, _, calls, _ = setup
    response = write(client, "/api/rooms", headers={"Origin": origin, **KEYS}, json={})
    assert response.status_code == 403
    assert not calls


def test_fetch_site_and_proxy_headers_cannot_override_origin(setup):
    client, _, calls, _ = setup
    assert write(client, "/api/advisor/advise", headers={"Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403
    assert write(client, "/api/rooms", headers={"Origin": "https://evil.test", "X-Forwarded-Host": "evil.test"}, json={}).status_code == 403
    assert client.post(PREFIX + "/api/auth/login", json={}).status_code == 403
    assert not calls


def test_safe_headers_and_session_cookie_only(setup):
    client, _, calls, _ = setup
    spoofed = {
        "Authorization": "Bearer offline-admin", "Proxy-Authorization": "Basic offline",
        "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "private.test",
        "X-Forwarded-Proto": "http", "Forwarded": "for=127.0.0.1",
        "X-Real-IP": "127.0.0.1", "X-User-ID": "admin", "X-Admin-Token": "offline",
        "X-HTTP-Method-Override": "PUT", "Sec-Fetch-Site": "same-origin",
    }
    response = write(client, "/api/advisor/advise", headers={
        **spoofed, **KEYS, "X-Player-Token": "offline-seat", "User-Agent": "visitor-controlled",
        "Cookie": f"pokerbench_session={SESSION}; pb_public_access=forged; admin=yes; other=value",
    }, json={"samples": 200})
    assert response.status_code == 200
    sent = calls[-1]
    assert not ({k.lower() for k in spoofed} - {"x-forwarded-proto"}) & set(sent.headers)
    assert sent.headers.get_list("x-forwarded-proto") == ["https"]
    assert sent.headers["cookie"] == f"pokerbench_session={SESSION}"
    assert sent.headers["host"] == "public.test"
    assert sent.headers["origin"] == ORIGIN
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["x-player-token"] == "offline-seat"
    assert all(sent.headers[name] == value for name, value in KEYS.items())
    assert sent.headers.get("user-agent") != "visitor-controlled"


def test_trusted_https_proto_is_constant_for_reads_writes_and_preflight(setup):
    client, _, calls, _ = setup
    for spoofed in (None, "http", "https, http", "caller-controlled"):
        headers = {} if spoofed is None else {"X-Forwarded-Proto": spoofed}
        assert client.get(PREFIX + "/api/auth/me", headers=headers).status_code == 200
        assert client.get(PREFIX + f"/api/runs/{ROOM}/stream", headers=headers).status_code == 200
        assert write(client, f"/api/runs/{ROOM}/start", headers=headers, json={}).status_code == 200
    assert len(calls) == 16
    assert all(request.headers.get_list("x-forwarded-proto") == ["https"] for request in calls)


def test_js_rewrite_keeps_api_helper_path_checks_in_sync(setup):
    client, _, _, overrides = setup
    source = r'''
        const authRoute=/^\/api\/auth\/(login|register|logout)$/.test(path);
        const playerRoute=/^\/api\/(rooms|runs)\/[^/]+\//.test(path);
        const keyRoute=path==='/api/rooms'||path==='/api/advisor/advise'||
            /^\/api\/rooms\/[^/]+\/(action|start)$/.test(path)||/^\/api\/runs\/[^/]+\/start$/.test(path);
        const api=path=>fetch(path,{credentials:'same-origin'});
        api('/api/auth/me');api("/api/auth/invite");api(`/api/rooms/${id}/action`);
        const external='https://other.test/api/path';const prefixed='/pokerbench/api/auth/me';
    '''
    overrides["GET", "/assets/helper.js"] = lambda request: httpx.Response(
        200, text=source, headers={"Content-Type": "application/javascript"})
    response = client.get(PREFIX + "/assets/helper.js")
    assert response.status_code == 200
    rewritten = response.text
    assert rewritten.count(r'/^\/pokerbench\/api\/') == 4
    assert r'/^\/api\/' not in rewritten
    assert "path==='/pokerbench/api/rooms'" in rewritten
    assert "path==='/pokerbench/api/advisor/advise'" in rewritten
    assert "api('/pokerbench/api/auth/me')" in rewritten
    assert 'api("/pokerbench/api/auth/invite")' in rewritten
    assert 'api(`/pokerbench/api/rooms/${id}/action`)' in rewritten
    assert "const api=path=>fetch(path," in rewritten
    assert "'https://other.test/api/path'" in rewritten
    assert "'/pokerbench/pokerbench/" not in rewritten


@pytest.mark.parametrize("cookie", ["pb_public_access=forged; admin=yes", 'pokerbench_session="token; admin=yes"'])
def test_forged_or_injected_cookies_do_not_become_backend_sessions(setup, cookie):
    client, _, calls, _ = setup
    response = client.get(PREFIX + "/api/auth/me", headers={"Cookie": cookie, "Authorization": "Bearer offline-admin"})
    assert response.json()["user"] is None
    assert "cookie" not in calls[-1].headers and "authorization" not in calls[-1].headers


def test_cookie_flags_path_logout_and_shared_client_isolation(setup):
    client, _, calls, _ = setup
    response = write(client, "/api/auth/login", json={})
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert all(part in cookies[0].lower() for part in ("pokerbench_session=", "path=/pokerbench/", "secure", "httponly", "samesite=lax"))
    assert "domain=" not in cookies[0].lower()
    assert client.get(PREFIX + "/api/auth/me").json()["user"] == "test-player"
    client.cookies.clear()  # Another visitor reaches the same shared upstream client.
    assert client.get(PREFIX + "/api/auth/me").json()["user"] is None
    assert "cookie" not in calls[-1].headers
    client.cookies.set("pokerbench_session", "second-visitor", path=PREFIX + "/")
    client.get(PREFIX + "/api/auth/me")
    assert calls[-1].headers["cookie"] == "pokerbench_session=second-visitor"
    client.cookies.clear()
    write(client, "/api/auth/login", json={})
    response = write(client, "/api/auth/logout", json={})
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get(PREFIX + "/api/auth/me").json()["user"] is None


@pytest.mark.parametrize("method,path,status", [
    ("GET", "/api/rooms/mine", 401), ("GET", f"/api/rooms/{ROOM}/state", 403),
    ("GET", "/api/auth/budget", 401), ("POST", "/api/rooms", 429),
    ("POST", "/api/auth/invite", 403), ("POST", "/api/advisor/preview", 401),
    ("POST", "/api/advisor/advise", 403), ("POST", f"/api/rooms/{ROOM}/action", 429),
])
def test_backend_account_owner_and_atomic_budget_decisions_are_preserved(setup, method, path, status):
    client, _, calls, overrides = setup
    overrides[method, path] = lambda request: httpx.Response(status, json={"detail": "backend decision"})
    response = write(client, path, method, headers={"Authorization": "Bearer offline-admin"})
    assert response.status_code == status and response.json() == {"detail": "backend decision"}
    assert len(calls) == 1 and "authorization" not in calls[0].headers


@pytest.mark.parametrize("payload,status", [(b"bad json", 502), (b"[]", 403),
    (b'{"human_player_id":"human-player","series_id":"formal-series"}', 403)])
def test_ambiguous_run_metadata_fails_closed(setup, payload, status):
    client, _, calls, overrides = setup
    overrides["GET", f"/api/runs/{ROOM}"] = lambda request: httpx.Response(200, content=payload)
    assert write(client, f"/api/runs/{ROOM}/start", json={}).status_code == status
    assert len(calls) == 1 and calls[0].method == "GET"


def test_body_size_boundary_and_invalid_content_length(setup):
    client, _, calls, _ = setup
    assert write(client, "/api/rooms", content=b"x" * gateway.MAX_BODY).status_code == 200
    assert len(calls[-1].content) == gateway.MAX_BODY
    calls.clear()
    assert write(client, "/api/rooms", content=b"x" * (gateway.MAX_BODY + 1)).status_code == 413
    for length in ("-1", "invalid"):
        assert write(client, "/api/rooms", headers={"Content-Length": length}, content=b"{}").status_code == 400
    assert not calls


def test_chunked_body_is_limited_before_remaining_chunks_are_read(setup):
    _, app, calls, _ = setup

    async def run():
        async def chunks():
            yield b"x" * gateway.MAX_BODY
            yield b"x"
            raise AssertionError("Oversized request must not be fully buffered")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            response = await client.post("/api/rooms", headers={"Origin": ORIGIN}, content=chunks())
            assert response.status_code == 413

    asyncio.run(run())
    assert not calls


def test_write_rate_limit_cannot_be_evaded_with_forwarded_ip_and_expires(setup):
    client, app, calls, _ = setup
    for i in range(60):
        assert write(client, f"/api/rooms/{ROOM}/action", headers={"X-Forwarded-For": f"198.51.100.{i}"}, json={}).status_code == 200
    assert write(client, f"/api/rooms/{ROOM}/action", headers={"X-Forwarded-For": "another-client"}, json={}).status_code == 429
    assert len(calls) == 60
    with app.state.db:
        app.state.db.execute("UPDATE attempts SET resets=0")
    assert write(client, f"/api/rooms/{ROOM}/action", json={}).status_code == 200


@pytest.mark.parametrize("path,limit", [("/api/auth/register", 10), ("/api/advisor/preview", 6), ("/api/advisor/advise", 6)])
def test_registration_and_advisor_rate_limits(setup, path, limit):
    client, _, calls, _ = setup
    for _ in range(limit):
        assert write(client, path, json={}).status_code == 200
    assert write(client, path, json={}).status_code == 429
    assert len(calls) == limit


def test_room_starts_actions_and_advisor_calls_are_not_globally_serialized(setup):
    _, app, _, overrides = setup

    async def run():
        paths = [f"/api/runs/{ROOM}/start", f"/api/runs/{ROOM}/start",
                 f"/api/rooms/{ROOM}/action", "/api/advisor/advise"]
        entered = []
        all_entered = asyncio.Event()

        async def concurrent_backend(request):
            entered.append(request)
            if len(entered) == len(paths):
                all_entered.set()
            # Every request must reach upstream before any response is released.
            await asyncio.wait_for(all_entered.wait(), timeout=3)
            return httpx.Response(200, json={"ok": True})

        for path in paths:
            overrides["POST", path] = concurrent_backend
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            responses = await asyncio.gather(*[
                client.post(path, headers={"Origin": ORIGIN,
                    "Cookie": f"pokerbench_session=offline-player-{index}", **KEYS}, json={})
                for index, path in enumerate(paths)
            ])
        assert all(response.status_code == 200 for response in responses)
        assert len({request.headers["cookie"] for request in entered}) == len(paths)
        assert all(request.headers["x-deepseek-key"] == KEYS["X-DeepSeek-Key"] for request in entered)
        assert all(request.headers["x-agent-keys"] == KEYS["X-Agent-Keys"] for request in entered)

    asyncio.run(run())


def test_provider_traffic_can_enter_backend_concurrently_without_a_gateway_queue(setup):
    _, app, _, overrides = setup

    async def run():
        # Exercise backend-sized Jev/local cohorts with independent DS/GPT traffic.
        providers = ["jev"] * 128 + ["local"] * 128 + ["deepseek"] * 2 + ["gpt"] * 2
        all_entered = asyncio.Event()
        entered = []

        async def backend(request):
            entered.append(request)
            if len(entered) == len(providers):
                all_entered.set()
            await asyncio.wait_for(all_entered.wait(), timeout=5)
            return httpx.Response(200, json={"ok": True})

        path = f"/api/rooms/{ROOM}/action"
        overrides["POST", path] = backend

        async def visitor(index, provider):
            # Distinct client addresses exercise concurrency without bypassing per-IP limits.
            transport = httpx.ASGITransport(app=app, client=(f"192.0.{index // 250}.{index % 250 + 1}", 12345))
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
                return await client.post(path, headers={"Origin": ORIGIN,
                    "Cookie": f"pokerbench_session=offline-player-{index}",
                    "X-Agent-Keys": json.dumps({f"custom-{index}": f"offline-key-{index}"})},
                    json={"provider": provider, "visitor": index})

        responses = await asyncio.gather(*(visitor(index, provider) for index, provider in enumerate(providers)))
        assert len(entered) == 260
        assert all(response.status_code == 200 for response in responses)
        for request in entered:
            index = json.loads(request.content)["visitor"]
            assert request.headers["cookie"] == f"pokerbench_session=offline-player-{index}"
            assert json.loads(request.headers["x-agent-keys"]) == {f"custom-{index}": f"offline-key-{index}"}

    asyncio.run(run())


@pytest.mark.parametrize("method,path,read_timeout", [
    ("POST", "/api/rooms", 30), ("POST", f"/api/rooms/{ROOM}/action", 30),
    ("POST", "/api/advisor/preview", 45), ("POST", "/api/advisor/advise", 45),
    ("GET", f"/api/runs/{ROOM}/stream", 60),
])
def test_upstream_timeouts_allow_backend_calls_and_stream_heartbeats(setup, method, path, read_timeout):
    client, _, calls, _ = setup
    assert write(client, path, method).status_code == 200
    assert calls[-1].extensions["timeout"] == {"connect": 5, "read": read_timeout, "write": 10, "pool": 5}


@pytest.mark.parametrize("method,path", [("POST", "/api/rooms"), ("POST", "/api/advisor/advise"), ("GET", f"/api/runs/{ROOM}/stream")])
def test_upstream_timeout_returns_503_without_retry_or_exception_details(setup, method, path):
    client, _, calls, overrides = setup

    def timeout(request):
        raise httpx.ReadTimeout("internal upstream details must stay private", request=request)

    overrides[method, path] = timeout
    response = write(client, path, method, json={})
    assert response.status_code == 503
    assert "internal upstream details" not in response.text
    assert len(calls) == 1


class EventStream(httpx.AsyncByteStream):
    def __init__(self, fail=False):
        self.fail, self.closed = fail, False

    async def __aiter__(self):
        yield b": heartbeat\n\n"
        yield b"data: {}\n\n"
        if self.fail:
            raise httpx.ReadTimeout("idle stream")

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("fail", [False, True])
def test_stream_forwards_events_and_closes_even_on_read_timeout(setup, fail):
    client, _, calls, overrides = setup
    stream = EventStream(fail)
    overrides["GET", f"/api/runs/{ROOM}/stream"] = lambda request: httpx.Response(
        200, stream=stream, headers={"Content-Type": "text/event-stream"})
    response = client.get(PREFIX + f"/api/runs/{ROOM}/stream", headers=KEYS)
    assert response.status_code == 200 and response.content == b": heartbeat\n\ndata: {}\n\n"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-store"
    assert stream.closed and len(calls) == 1


def test_stream_errors_keep_backend_status_and_json(setup):
    client, _, _, overrides = setup
    overrides["GET", f"/api/runs/{ROOM}/stream"] = lambda request: httpx.Response(403, json={"detail": "owner required"})
    response = client.get(PREFIX + f"/api/runs/{ROOM}/stream")
    assert response.status_code == 403 and response.json() == {"detail": "owner required"}
    assert response.headers["content-type"] == "application/json"


def test_query_strings_and_security_headers_are_preserved(setup):
    client, _, calls, _ = setup
    response = client.get(PREFIX + f"/api/runs/{ROOM}/hands?offset=2&limit=10")
    assert calls[-1].url.query == b"offset=2&limit=10"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_gateway_overwrites_forwarded_address_with_its_verified_visitor(setup):
    _,app,calls,_=setup
    for address in ['198.51.100.10','198.51.100.11']:
        client=TestClient(app,base_url=ORIGIN,client=(address,1234))
        assert client.get('/api/auth/me',headers={'X-Forwarded-For':'127.0.0.1','X-Real-IP':'127.0.0.1'}).status_code==200
        assert calls[-1].headers['x-forwarded-for']==address
        assert 'x-real-ip' not in calls[-1].headers
        client.close()

"""Password accounts and revocable browser sessions for personal tables."""
import asyncio
import hashlib
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr
from .player_budget import record_login

COOKIE = "pokerbench_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
ITERATIONS = 600_000


def same_origin(request):
    origin = request.headers.get("origin")
    if (origin and (urlparse(origin).netloc != request.headers.get("host") or urlparse(origin).scheme != request.url.scheme)) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从当前站点提交请求")


def private_json(body):
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def normalize_id(value):
    display = unicodedata.normalize("NFKC", value).strip()
    if not re.fullmatch(r"[\w.-]{1,40}", display):
        raise HTTPException(422, "ID 需为 1–40 位字母、数字、中文、下划线、点或短横线")
    return display, display.casefold()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


class Credentials(BaseModel):
    id: str
    password: SecretStr


class Accounts:
    def __init__(self, store):
        self.store = store
        self.hash_slots = asyncio.Semaphore(2)
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, id_key TEXT UNIQUE NOT NULL,
                display_id TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created REAL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL);
            CREATE TABLE IF NOT EXISTS room_owners(run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, visited REAL);
            CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT PRIMARY KEY, count INTEGER NOT NULL, resets REAL);
        """)
        store.db.commit()

    def user(self, request, required=False):
        token = request.cookies.get(COOKIE, "")
        row = self.store.db.execute("""SELECT users.uid, users.display_id FROM sessions
            JOIN users ON users.uid=sessions.user_id WHERE token_hash=? AND expires>?""",
            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone() if token else None
        if row is None and required:
            raise HTTPException(401, "请先登录玩家账号")
        return dict(row) if row else None

    def owner(self, run_id):
        row = self.store.db.execute("SELECT user_id FROM room_owners WHERE run_id=?", (run_id,)).fetchone()
        return row[0] if row else None

    def throttle(self, request, id_key):
        now = time.time()
        # Only the trusted connection address is used; client-supplied forwarded headers are ignored.
        address = request.client.host if request.client else "unknown"
        keys = [("ip:" + address, 60), ("id:" + id_key, 10)]
        with self.store.db:
            self.store.db.execute("DELETE FROM auth_attempts WHERE resets<=?", (now,))
            for key, limit in keys:
                key = hashlib.sha256(key.encode()).hexdigest()
                row = self.store.db.execute("SELECT count FROM auth_attempts WHERE key=?", (key,)).fetchone()
                if row and row[0] >= limit:
                    raise HTTPException(429, "尝试过于频繁，请 10 分钟后再试", headers={"Retry-After": "600"})
                self.store.db.execute("""INSERT INTO auth_attempts VALUES(?,1,?)
                    ON CONFLICT(key) DO UPDATE SET count=count+1""", (key, now + 600))

    def session(self, user, request):
        record_login(self.store.db,user['uid'])
        token = secrets.token_urlsafe(32)
        old = request.cookies.get(COOKIE, "")
        with self.store.db:
            self.store.db.execute("DELETE FROM sessions WHERE expires<=? OR token_hash=?",
                                  (time.time(), hashlib.sha256(old.encode()).hexdigest()))
            self.store.db.execute("INSERT INTO sessions VALUES(?,?,?)",
                                  (hashlib.sha256(token.encode()).hexdigest(), user["uid"], time.time() + SESSION_SECONDS))
            self.store.db.execute("DELETE FROM auth_attempts WHERE key=?",
                                  (hashlib.sha256(("id:" + user["id_key"]).encode()).hexdigest(),))
        response = private_json({"user": {"id": user["display_id"]}})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=request.url.scheme == "https", samesite="lax", path="/")
        return response

    def routes(self, app):
        @app.post("/api/auth/register")
        async def register(body: Credentials, request: Request):
            same_origin(request)
            display, key = normalize_id(body.id)
            password = body.password.get_secret_value()
            if not 8 <= len(password) <= 128:
                raise HTTPException(422, "密码需要 8–128 个字符")
            self.throttle(request, key)
            salt = secrets.token_hex(16)
            async with self.hash_slots:
                hashed = await asyncio.to_thread(password_hash, password, salt)
            user = {"uid": uuid.uuid4().hex, "display_id": display, "id_key": key}
            try:
                with self.store.db:
                    self.store.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",
                        (user["uid"], key, display, salt, hashed, time.time()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "这个 ID 已被注册，请登录或换一个 ID")
            return self.session(user, request)

        @app.post("/api/auth/login")
        async def login(body: Credentials, request: Request):
            same_origin(request)
            _, key = normalize_id(body.id)
            password = body.password.get_secret_value()
            if len(password) > 128:
                raise HTTPException(401, "ID 或密码不正确")
            self.throttle(request, key)
            row = self.store.db.execute("SELECT * FROM users WHERE id_key=?", (key,)).fetchone()
            async with self.hash_slots:
                hashed = await asyncio.to_thread(password_hash, password, row["salt"] if row else "00" * 16)
            if not row or not secrets.compare_digest(row["password_hash"], hashed):
                raise HTTPException(401, "ID 或密码不正确")
            return self.session(dict(row), request)

        @app.get("/api/auth/me")
        async def me(request: Request):
            user = self.user(request)
            return private_json({"user": {"id": user["display_id"]} if user else None})

        @app.post("/api/auth/logout")
        async def logout(request: Request):
            same_origin(request)
            token = request.cookies.get(COOKIE, "")
            with self.store.db:
                self.store.db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            response = private_json({"ok": True})
            response.delete_cookie(COOKIE, path="/", httponly=True, secure=request.url.scheme == "https", samesite="lax")
            return response

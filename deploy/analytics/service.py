"""Read-only analytics sidecar. Never imports or writes the game runtime."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
import gzip
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

TZ = ZoneInfo('Asia/Shanghai')
LINE = re.compile(r'^(\S+) \S+ \S+ \[([^]]+)\] "(\S+) ([^ ]+) HTTP/[^" ]+" (\d{3}) \S+ "(?:[^"\\]|\\.)*" "((?:[^"\\]|\\.)*)"')
BOTS = re.compile(r'bot|crawler|spider|headless|playwright|lighthouse|uptime|monitor|curl|wget|python|httpx', re.I)
ROOT = Path(__file__).resolve().parent


def page_visit(line, secret):
    match = LINE.match(line)
    if not match:
        return None
    address, stamp, method, url, status, agent = match.groups()
    if method != 'GET' or url.split('?', 1)[0] != '/pokerbench/' or status not in ('200', '304'):
        return None
    if not agent.startswith('Mozilla/') or BOTS.search(agent):
        return None
    try:
        day = datetime.strptime(stamp, '%d/%b/%Y:%H:%M:%S %z').astimezone(TZ).date().isoformat()
    except ValueError:
        return None
    # A different pseudonym every day; no raw IP, UA, URL, headers or keys persist.
    visitor = hmac.new(secret.encode(), f'{day}\0{address}\0{agent}'.encode(), hashlib.sha256).hexdigest()
    return day, visitor


class Collector:
    def __init__(self, directory, game_db, access_log, secret):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = directory / 'analytics.sqlite'
        self.game_db, self.access_log, self.secret = Path(game_db), Path(access_log), secret
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cursors(fingerprint TEXT PRIMARY KEY, offset INTEGER);
                CREATE TABLE IF NOT EXISTS visits(day TEXT PRIMARY KEY, views INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS visitors(day TEXT, visitor TEXT, PRIMARY KEY(day,visitor));
            ''')
        self.snapshot = {'updated': None, 'days': [], 'traffic_error': None, 'accounts_error': None}
        self.accounts = {}
        self.finished_archives = set()
        self.account_total = None
        self.room_total = None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=1)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, max_bytes=8*1024*1024):
        # Fingerprint the first complete line so rename and gzip rotation keep a cursor.
        # Never discard a cursor: reappearing rotated files must not double-count visits.
        paths = sorted(self.access_log.parent.glob(self.access_log.name + '.*'))
        if self.access_log.exists():
            paths.append(self.access_log)
        if not paths:
            raise FileNotFoundError('No access logs')
        consumed = 0
        finished = []
        with self.connect() as db:
            for path in paths:
                if consumed >= max_bytes:
                    break
                stat = path.stat()
                signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if path.suffix == '.gz' and signature in self.finished_archives:
                    continue
                opener = gzip.open if path.suffix == '.gz' else open
                with opener(path, 'rb') as log:
                    first = log.readline(65537)
                    if not first.endswith(b'\n'):
                        continue
                    fingerprint = hashlib.sha256(first).hexdigest()
                    row = db.execute('SELECT offset FROM cursors WHERE fingerprint=?', (fingerprint,)).fetchone()
                    offset = row[0] if row else 0
                    log.seek(offset)
                    while consumed < max_bytes:
                        line = log.readline(65537)
                        if not line:
                            if path.suffix == '.gz':
                                finished.append(signature)
                            break
                        if not line.endswith(b'\n'):
                            break
                        consumed += len(line)
                        offset = log.tell()
                        visit = page_visit(line.decode('utf-8', errors='replace'), self.secret)
                        if visit:
                            day, visitor = visit
                            db.execute('INSERT INTO visits VALUES(?,1) ON CONFLICT(day) DO UPDATE SET views=views+1', (day,))
                            db.execute('INSERT OR IGNORE INTO visitors VALUES(?,?)', (day, visitor))
                    db.execute('INSERT INTO cursors VALUES(?,?) ON CONFLICT(fingerprint) DO UPDATE SET offset=excluded.offset', (fingerprint, offset))
        self.finished_archives.update(finished)
        return consumed

    def account_counts(self):
        # Mode=ro and query_only prevent writes. Short reads release WAL snapshots;
        # a strict query deadline avoids competing with benchmark state updates.
        started = time.monotonic()
        db = sqlite3.connect(self.game_db.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA cache_size=-1024')
            db.set_progress_handler(lambda: int(time.monotonic()-started > .25), 500)
            registrations = db.execute("SELECT date(created,'unixepoch','+8 hours'),count(*) FROM users GROUP BY 1").fetchall()
            rooms = db.execute('''SELECT date(json_extract(r.body,'$.created'),'unixepoch','+8 hours'),
                count(*),count(DISTINCT o.user_id) FROM room_owners o JOIN runs r ON r.id=o.run_id GROUP BY 1''').fetchall()
            account_total = sum(count for _, count in registrations)
            room_total = sum(count for _, count, _ in rooms)
            days = {day: {'registrations': count} for day, count in registrations if day}
            for day, count, players in rooms:
                if day:
                    days.setdefault(day, {}).update(rooms=count, players=players)
            self.accounts, self.account_total, self.room_total = days, account_total, room_total
        finally:
            db.close()

    def refresh(self):
        traffic_error = accounts_error = None
        try:
            consumed = self.ingest()
        except (OSError, sqlite3.Error, EOFError):
            consumed = 0
            traffic_error = 'Access logs unavailable; showing previously collected visits.'
        try:
            self.account_counts()
        except (OSError, sqlite3.Error):
            accounts_error = 'Account counts temporarily unavailable; showing the last successful snapshot.'
        today = datetime.now(TZ).date()
        with self.connect() as db:
            views = dict(db.execute('SELECT day,views FROM visits'))
            visitors = dict(db.execute('SELECT day,count(*) FROM visitors GROUP BY day'))
        rows = []
        for offset in range(30):
            day = (today-timedelta(days=offset)).isoformat()
            rows.append({'day': day, 'views': views.get(day, 0), 'visitors': visitors.get(day, 0),
                         'registrations': self.accounts.get(day, {}).get('registrations', 0 if self.account_total is not None else None),
                         'rooms': self.accounts.get(day, {}).get('rooms', 0 if self.room_total is not None else None),
                         'players': self.accounts.get(day, {}).get('players', 0 if self.room_total is not None else None)})
        self.snapshot = {'updated': time.time(), 'timezone': 'Asia/Shanghai', 'days': rows,
                         'first_visit_day': min(views) if views else None,
                         'account_total': self.account_total, 'room_total': self.room_total,
                         'traffic_error': traffic_error, 'accounts_error': accounts_error,
                         'catching_up': consumed >= 8*1024*1024}


def create_app(collector, username, password, *, collect=True):
    if not username or len(password) < 24:
        raise ValueError('Configure independent strong analytics credentials')
    security = HTTPBasic()

    def admin(credentials: HTTPBasicCredentials = Depends(security)):
        valid_user = secrets.compare_digest(credentials.username.encode(), username.encode())
        valid_password = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(401, 'Administrator authentication required', headers={'WWW-Authenticate': 'Basic realm="ProphetLab Analytics"'})

    @asynccontextmanager
    async def lifespan(app):
        async def worker():
            while True:
                try:
                    await asyncio.to_thread(collector.refresh)
                except Exception:
                    # A collector failure cannot affect the game or expose raw log lines.
                    collector.snapshot = {**collector.snapshot, 'traffic_error': 'Collection paused; retrying shortly.'}
                await asyncio.sleep(5 if collector.snapshot.get('catching_up') else 60)
        task = asyncio.create_task(worker()) if collect else None
        yield
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan, dependencies=[Depends(admin)])

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    @app.get('/')
    def page():
        return FileResponse(ROOT / 'index.html')

    @app.get('/app.js')
    def script():
        return FileResponse(ROOT / 'app.js', media_type='application/javascript')

    @app.get('/style.css')
    def style():
        return FileResponse(ROOT / 'style.css', media_type='text/css')

    @app.get('/api/summary')
    def summary():
        return collector.snapshot

    return app


def from_env():
    return create_app(Collector(os.environ['PB_STATS_DATA_DIR'], os.environ['PB_STATS_GAME_DB'],
        os.environ['PB_STATS_ACCESS_LOG'], os.environ['PB_STATS_HASH_SECRET']),
        os.environ.get('PB_STATS_USER', 'prophetlab'), os.environ['PB_STATS_PASSWORD'])

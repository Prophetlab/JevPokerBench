"""Analytics runs separately; these tests use only synthetic logs and databases."""
from datetime import datetime
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('site_analytics', Path(__file__).resolve().parents[2]/'deploy/analytics/service.py')
analytics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analytics)
PASSWORD = 'test-only-stats-password-0123456789'


def line(ip='203.0.113.9', path='/pokerbench/', status=200, ua='Mozilla/5.0 browser', method='GET', stamp='22/Sep/2026:12:00:00 +0000'):
    return f'{ip} - - [{stamp}] "{method} {path} HTTP/1.1" {status} 123 "-" "{ua}"\n'


@pytest.fixture
def setup(tmp_path):
    game = tmp_path/'game.sqlite'
    with sqlite3.connect(game) as db:
        db.executescript('''CREATE TABLE users(uid TEXT,created REAL);
            CREATE TABLE runs(id TEXT,body TEXT);
            CREATE TABLE room_owners(run_id TEXT,user_id TEXT);
            CREATE TABLE deleted_rooms(run_id TEXT);''')
        now = datetime.now(analytics.TZ).timestamp()
        db.executemany('INSERT INTO users VALUES(?,?)', [('one',now),('two',now)])
        db.executemany('INSERT INTO runs VALUES(?,?)', [(rid,json.dumps({'created':now,'private':'never-return-this'})) for rid in ['r1','r2','r3','benchmark','unclaimed']])
        db.executemany('INSERT INTO room_owners VALUES(?,?)', [('r1','one'),('r2','one'),('r3','two')])
        db.execute("INSERT INTO deleted_rooms VALUES('r1')")
    log = tmp_path/'access.log'
    log.write_text(line())
    collector = analytics.Collector(tmp_path/'statistics', game, log, 'a-private-hashing-key')
    return collector, log, game


def test_page_loads_daily_visitors_and_private_fields(setup):
    c,log,_=setup
    log.write_text(line()+line(path='/pokerbench/?apikey=do-not-save')+line(ip='203.0.113.10'))
    assert c.ingest()>0
    assert c.ingest()==0
    with c.connect() as db:
        assert db.execute('SELECT views FROM visits').fetchone()[0]==3
        assert db.execute('SELECT count(*) FROM visitors').fetchone()[0]==2
        stored='\n'.join(db.iterdump())
        assert all(s not in stored for s in ['203.0.113','Mozilla','apikey','do-not-save'])
    before=analytics.page_visit(line(),c.secret)
    after=analytics.page_visit(line(stamp='23/Sep/2026:12:00:00 +0000'),c.secret)
    assert before[1]!=after[1]


@pytest.mark.parametrize('kwargs',[
    {'path':'/pokerbench/api/runs'}, {'path':'/pokerbench/api/runs/123/stream'},
    {'path':'/pokerbench/assets/app.js'}, {'path':'/another-service/'},
    {'method':'POST'}, {'method':'HEAD'}, {'status':301}, {'status':404}, {'status':500},
    {'ua':'Mozilla/5.0 Googlebot'}, {'ua':'Mozilla/5.0 HeadlessChrome'},
    {'ua':'curl/1'}, {'ua':''}, {'stamp':'malformed'},
])
def test_polling_assets_errors_and_known_bots_are_not_pageviews(kwargs):
    assert analytics.page_visit(line(**kwargs),'secret') is None


def test_dates_use_beijing_time_and_304_counts():
    assert analytics.page_visit(line(stamp='21/Sep/2026:23:30:00 +0000',status=304),'key')[0]=='2026-09-22'
    assert analytics.page_visit('malformed','key') is None


def test_incremental_partial_line_restart_and_gzip_rotation(setup):
    c,log,_=setup
    first=line();second=line(ip='203.0.113.10')
    log.write_text(first+second[:-5]);c.ingest()
    with c.connect() as db:assert db.execute('SELECT sum(views) FROM visits').fetchone()[0]==1
    with log.open('a') as f:f.write(second[-5:])
    c.ingest()
    rotated=log.with_name('access.log.1');log.rename(rotated)
    log.write_text(line(stamp='22/Sep/2026:12:30:00 +0000'))
    c.ingest()
    gz=log.with_name('access.log.2.gz')
    with gzip.open(gz,'wb') as f:f.write(rotated.read_bytes())
    rotated.unlink()
    c.ingest();c.ingest()
    restarted=analytics.Collector(c.database.parent,c.game_db,c.access_log,c.secret)
    restarted.ingest()
    with c.connect() as db:
        assert db.execute('SELECT sum(views) FROM visits').fetchone()[0]==3
        assert db.execute('SELECT count(*) FROM visitors').fetchone()[0]==2


def test_bounded_backfill_resumes_without_duplicates(setup):
    c,log,_=setup
    log.write_text(''.join(line(ip=f'203.0.113.{i}') for i in range(20)))
    for _ in range(100):
        if not c.ingest(max_bytes=100):break
    else:pytest.fail('Collector did not finish')
    with c.connect() as db:assert db.execute('SELECT sum(views) FROM visits').fetchone()[0]==20


def test_business_counts_are_readonly_aggregate_and_exclude_benchmarks(setup):
    c,_,game=setup;before=game.read_bytes()
    c.refresh();assert c.snapshot['account_total']==2 and c.snapshot['room_total']==3
    today=c.snapshot['days'][0];assert today['registrations']==2 and today['rooms']==3 and today['players']==2
    assert game.read_bytes()==before
    assert 'never-return-this' not in json.dumps(c.snapshot)
    assert '"one"' not in json.dumps(c.snapshot)


def test_unavailable_source_preserves_successful_counts(setup):
    c,log,game=setup;c.refresh();before=c.snapshot
    log.unlink();game.rename(game.with_suffix('.missing'))
    c.refresh()
    assert c.snapshot['days']==before['days']
    assert c.snapshot['account_total']==2
    assert c.snapshot['traffic_error'] and c.snapshot['accounts_error']
    assert not game.exists()


def test_missing_initial_database_does_not_prevent_visit_collection(setup):
    c,_,game=setup;game.unlink();c.refresh()
    assert c.snapshot['updated'] and c.snapshot['accounts_error']
    assert c.snapshot['days'][0]['registrations'] is None
    with c.connect() as db:assert db.execute('SELECT sum(views) FROM visits').fetchone()[0]==1


def test_admin_authentication_no_cache_and_no_unprotected_assets(setup):
    c,_,_=setup;c.refresh()
    with TestClient(analytics.create_app(c,'prophetlab',PASSWORD,collect=False)) as client:
        for path in ['/','/api/summary','/app.js','/style.css']:
            assert client.get(path).status_code==401
            assert client.get(path,auth=('prophetlab','wrong')).status_code==401
            response=client.get(path,auth=('prophetlab',PASSWORD))
            assert response.status_code==200
            assert response.headers['cache-control']=='no-store'
            assert response.headers['x-frame-options']=='DENY'
        assert client.post('/api/summary',auth=('prophetlab',PASSWORD),json={}).status_code==405
        assert client.get('/.env',auth=('prophetlab',PASSWORD)).status_code==404
        assert client.get('/openapi.json').status_code==404
    with pytest.raises(ValueError):analytics.create_app(c,'prophetlab','short')

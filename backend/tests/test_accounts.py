import hashlib
import time

from fastapi.testclient import TestClient

from pokerbench.accounts import COOKIE, Accounts
from pokerbench.config import Settings
from test_rooms import client, new_room, wait_state, OfflineModels

PASSWORD='test-password-123'


def register(c, name='Alice'):
    result=c.post('/api/auth/register',json={'id':name,'password':PASSWORD})
    assert result.status_code==200,result.text
    return result


def test_registration_hashes_passwords_unique_ids_and_private_cookies(client):
    c,app,_=client
    assert c.post('/api/rooms',json={'model_ids':['bot']}).status_code==401
    response=register(c)
    assert response.json()=={'user':{'id':'Alice'}}
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
    assert response.headers['cache-control']=='no-store'
    row=app.state.store.db.execute('SELECT * FROM users').fetchone()
    assert PASSWORD not in str(dict(row)) and len(row['salt'])==32
    session=app.state.store.db.execute('SELECT token_hash FROM sessions').fetchone()[0]
    assert session==hashlib.sha256(c.cookies.get(COOKIE).encode()).hexdigest()
    assert c.post('/api/auth/register',json={'id':'ＡＬＩＣＥ','password':PASSWORD}).status_code==409
    assert c.get('/api/auth/me').json()=={'user':{'id':'Alice'}}
    result=c.post('/api/auth/register',json={'id':'Bob','password':'short'})
    assert result.status_code==422 and 'short' not in result.text
    invalid=c.post('/api/auth/register',json={'password':PASSWORD})
    assert invalid.status_code==422 and PASSWORD not in invalid.text


def test_sessions_login_logout_expiry_and_restart(client):
    c,app,_=client;register(c)
    old=c.cookies.get(COOKIE)
    c.post('/api/auth/logout')
    assert c.get('/api/auth/me').json()['user'] is None
    other=TestClient(app);other.cookies.set(COOKIE,old)
    assert other.get('/api/auth/me').json()['user'] is None
    errors=[]
    for name in ['Alice','missing']:
        r=c.post('/api/auth/login',json={'id':name,'password':'incorrect'})
        assert r.status_code==401;errors.append(r.json())
    assert errors[0]==errors[1]
    assert c.post('/api/auth/login',json={'id':'alice','password':PASSWORD}).status_code==200
    assert c.cookies.get(COOKIE)!=old
    Accounts(app.state.store)
    assert c.get('/api/auth/me').json()['user']['id']=='Alice'
    with app.state.store.db:app.state.store.db.execute('UPDATE sessions SET expires=?',(time.time()-1,))
    assert c.get('/api/auth/me').json()['user'] is None


def test_cross_origin_registration_login_and_logout_rejected(client):
    c,app,_=client
    for path in ['register','login','logout']:
        assert c.post('/api/auth/'+path,json={'id':'Alice','password':PASSWORD},headers={'Origin':'https://other.test'}).status_code==403
    assert app.state.store.db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
    register(c)
    assert c.post('/api/auth/logout',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert c.get('/api/auth/me').json()['user']


def test_secure_cookie_on_https(client):
    _,app,_=client;secure=TestClient(app,base_url='https://testserver')
    assert 'Secure' in register(secure).headers['set-cookie']
    assert secure.get('/api/auth/me').json()['user']['id']=='Alice'


def test_login_throttles_password_guessing(client):
    c,app,_=client;register(c);c.post('/api/auth/logout')
    for _ in range(10):assert c.post('/api/auth/login',json={'id':'alice','password':'wrong'}).status_code==401
    result=c.post('/api/auth/login',json={'id':'Alice','password':PASSWORD})
    assert result.status_code==429 and result.headers['retry-after']=='600'


def test_trusted_gateway_visitor_ips_do_not_share_one_login_limit(client,monkeypatch):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    import pokerbench.accounts as accounts
    _,app,_=client
    monkeypatch.setattr(accounts,'password_hash',lambda *args:'test-hash')
    proxy=ProxyHeadersMiddleware(app,trusted_hosts=['127.0.0.1'])
    c=TestClient(proxy,client=('127.0.0.1',1234))
    def login(address,name):
        return c.post('/api/auth/login',json={'id':name,'password':'wrong'},headers={'X-Forwarded-For':address})
    for i in range(60):assert login('198.51.100.10',f'missing-{i}').status_code==401
    assert login('198.51.100.10','blocked').status_code==429
    assert login('198.51.100.11','another-user').status_code==401
    direct=TestClient(proxy,client=('198.51.100.10',1234))
    # An untrusted direct client cannot override its own rate-limit identity.
    assert direct.post('/api/auth/login',json={'id':'direct','password':'wrong'},headers={'X-Forwarded-For':'198.51.100.12'}).status_code==429
    c.close();direct.close()


def test_account_rooms_restore_on_another_browser_without_seat_tokens(client):
    c,app,_=client;register(c)
    created=c.post('/api/rooms',json={'model_ids':['bot'],'player_name':'Spoofed'}).json()
    assert 'player_token' not in created
    rid=created['run']['id'];run=app.state.store.run(rid)
    assert run['config']['entries'][0]['name']=='Alice'
    run['seats'].sort(key=lambda s:s['id']!='human-player');run['button']=0;app.state.store.save_run(run)
    assert c.post(f'/api/runs/{rid}/start').status_code==200
    assert wait_state(c,rid,{})['can_act']
    second=TestClient(app)
    assert second.post('/api/auth/login',json={'id':'Alice','password':PASSWORD}).status_code==200
    assert second.get('/api/rooms/mine').json()[0]['id']==rid
    state=second.get(f'/api/rooms/{rid}/state').json()
    seats=state['record']['events'][-1]['snapshot']['seats']
    assert seats[0]['cards']!=['??','??'] and seats[1]['cards']==['??','??']
    assert second.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'fold'}).status_code==200
    assert wait_state(second,rid,{})['run']['hands_played']==1


def test_other_account_cannot_read_act_export_or_claim(client):
    c,app,_=client;register(c)
    rid=c.post('/api/rooms',json={'model_ids':['bot']}).json()['run']['id']
    bob=TestClient(app);register(bob,'Bob')
    assert bob.get('/api/rooms/mine').json()==[]
    assert bob.get(f'/api/rooms/{rid}/state').status_code==403
    assert bob.get(f'/api/runs/{rid}/export').status_code==403
    for path in [f'/api/runs/{rid}/start',f'/api/runs/{rid}/pause',f'/api/rooms/{rid}/claim',f'/api/rooms/{rid}/visit']:
        assert bob.post(path).status_code==403
    assert bob.patch(f'/api/runs/{rid}/limit',json={'max_hands':6000}).status_code==403
    assert bob.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'fold'}).status_code==403
    for path in [f'/api/runs/{rid}/start',f'/api/rooms/{rid}/action']:
        assert c.post(path,json={'hand_number':1,'action_count':0,'action_id':'fold'},headers={'Origin':'https://other.test'}).status_code==403


def test_all_tables_remain_available_in_recent_order(client):
    c,app,_=client;register(c)
    ids=[c.post('/api/rooms',json={'name':str(i),'model_ids':['bot']}).json()['run']['id'] for i in range(4)]
    assert [r['id'] for r in c.get('/api/rooms/mine').json()]==ids[::-1]
    c.post(f'/api/rooms/{ids[0]}/visit')
    assert [r['id'] for r in c.get('/api/rooms/mine').json()]==[ids[0],ids[3],ids[2],ids[1]]
    assert c.get(f'/api/rooms/{ids[1]}/state').status_code==200


def test_legacy_claim_preserves_progress_and_revokes_token_only_access(client):
    c,app,_=client;rid,headers=new_room(client)
    c.post(f'/api/runs/{rid}/start',headers=headers);wait_state(c,rid,headers)
    before=app.state.store.run(rid);hand=app.state.store.hand(rid,1)
    assert c.post(f'/api/rooms/{rid}/claim',headers=headers).status_code==401
    register(c)
    assert c.post(f'/api/rooms/{rid}/claim').status_code==403
    assert c.post(f'/api/rooms/{rid}/claim',headers=headers).status_code==200
    assert c.post(f'/api/rooms/{rid}/claim').status_code==200
    assert app.state.store.run(rid)==before and app.state.store.hand(rid,1)==hand
    old=TestClient(app)
    assert old.get(f'/api/rooms/{rid}/state',headers=headers).status_code==401
    register(old,'Bob')
    assert old.post(f'/api/rooms/{rid}/claim',headers=headers).status_code==403
    assert old.get(f'/api/rooms/{rid}/state',headers=headers).status_code==403
    assert c.get(f'/api/rooms/{rid}/state').status_code==200


def test_player_account_is_not_benchmark_admin(client,tmp_path):
    import pokerbench.api as module
    app=module.create_app(Settings(_env_file=None,pokerbench_data_dir=str(tmp_path/'guarded'),pokerbench_admin_token='admin-only',deepseek_api_key='test-only'))
    app.state.runner.provider=OfflineModels()
    with TestClient(app) as c:
        register(c)
        created=c.post('/api/rooms',json={'model_ids':['bot']})
        assert created.status_code==200
        rid=created.json()['run']['id']
        assert c.post(f'/api/runs/{rid}/start').status_code==200
        assert c.put('/api/entries',json=[]).status_code==401
        assert c.post('/api/runs',json={'entries':c.get('/api/entries').json()[:2]}).status_code==401
        assert c.post('/api/series/missing/start').status_code==401


def test_delete_owned_room_hides_it_and_preserves_billing(client):
    from pokerbench.player_budget import usage
    c,app,_=client;register(c)
    rid=c.post('/api/rooms',json={'model_ids':['bot']}).json()['run']['id']
    store=app.state.store
    uid=store.db.execute('SELECT user_id FROM room_owners WHERE run_id=?',(rid,)).fetchone()[0]
    with store.db:store.db.execute('INSERT INTO player_invites VALUES(?,?)',(uid,0))
    call=store.reserve(rid,2,100,50,provider='deepseek');store.finish_call(call,2,{})
    bob=TestClient(app);register(bob,'Bob')
    assert bob.delete(f'/api/rooms/{rid}').status_code==403
    assert c.delete(f'/api/rooms/{rid}',headers={'Origin':'https://evil.test'}).status_code==403
    run=store.run(rid);run['status']='running';store.save_run(run)
    assert c.delete(f'/api/rooms/{rid}').status_code==409
    run['status']='paused';store.save_run(run)
    assert c.delete(f'/api/rooms/{rid}').status_code==200
    assert c.delete(f'/api/rooms/{rid}').status_code==200
    assert c.get('/api/rooms/mine').json()==[]
    assert all(r['id']!=rid for r in c.get('/api/runs').json())
    assert c.get(f'/api/rooms/{rid}/state').status_code==404
    assert c.post(f'/api/runs/{rid}/start').status_code==404
    assert c.post(f'/api/rooms/{rid}/visit').status_code==404
    assert usage(store.db,uid)==2 and store.spend()==2
    assert store.db.execute('SELECT COUNT(*) FROM runs WHERE id=?',(rid,)).fetchone()[0]==1

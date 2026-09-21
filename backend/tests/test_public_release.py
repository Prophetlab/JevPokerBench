"""Public-release security contracts; all providers and state are test-local."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pokerbench.accounts import COOKIE
from pokerbench.advisor import AdvisorInput
from pokerbench.config import Entry, RunConfig, Settings
from pokerbench.engine import Hand
from pokerbench.player_budget import usage
from pokerbench.provider import Provider
from pokerbench.runner import Runner
from pokerbench.store import Store
from test_accounts import PASSWORD, register
from test_rooms import wait_state

INVITE='private-test-invite'
ADMIN={'Authorization':'Bearer private-test-admin'}
PERSONAL={'deepseek':'visitor-deepseek-key','jev':'visitor-jev-key'}
PRIVATE_FIELDS={'base_url','key_env','input_cny_per_million','output_cny_per_million',
                'cost_cny_estimate','spent_cny_estimate','run_budget_cny','budget_cny',
                'request','raw_response','request_hash','seed','player_token_hash'}


@pytest.fixture
def public_client(tmp_path,monkeypatch):
    import pokerbench.api as api
    import pokerbench.provider as provider_module
    registry=[Entry(id='bot',name='Local',provider='systemone',key_env='',base_url='http://private-local.test',input_cny_per_million=0,output_cny_per_million=0),
              Entry(id='deepseek',name='DeepSeek',base_url='https://private-ds-proxy.test'),
              Entry(id='jev',name='Jev',provider='jev',model='jev',key_env='JEV_API_KEY',base_url='https://private-jev-proxy.test')]
    config=tmp_path/'config';config.mkdir()
    (config/'entries.json').write_text(json.dumps([e.model_dump() for e in registry]))
    monkeypatch.setattr(api,'ROOT',tmp_path)
    monkeypatch.setattr(provider_module,'ROOT',tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY','host-deepseek-key')
    monkeypatch.setenv('JEV_API_KEY','host-jev-key')
    app=api.create_app(Settings(_env_file=None,pokerbench_data_dir=str(tmp_path/'data'),
        pokerbench_invite_code=INVITE,pokerbench_admin_token='private-test-admin',
        deepseek_api_key='host-deepseek-key',jev_api_key='host-jev-key'))
    with TestClient(app) as c:yield c,app,registry


@pytest.fixture
def model_http(monkeypatch):
    sent=[]
    async def post(client,url,**kwargs):
        sent.append((url,deepcopy(kwargs)))
        payload=kwargs['json']
        if 'questions' in payload:
            choices=list(payload['questions']['action']['criteria'])
        else:
            document=json.loads(payload['messages'][1]['content'].removeprefix('<document>\n').removesuffix('\n</document>'))
            choices=[a['id'] for a in document['legal_actions']]
        choice='check' if 'check' in choices else 'call' if 'call' in choices else choices[0]
        probabilities={k:float(k==choice) for k in choices}
        if 'questions' in payload:
            raw={'model':payload['model'],'usage':{'input_tokens':100,'output_tokens':50},
                 'answers':{'action':{'type':'choice','choice':choice,'probabilities':probabilities,'confidence':1}}}
        else:
            raw={'model':payload['model'],'usage':{'prompt_tokens':100,'completion_tokens':50},
                 'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':{'action':probabilities}})}}]}
        return httpx.Response(200,json=raw)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    return sent


def advisor_body(**kwargs):
    return {'seats':[{'id':'a'},{'id':'b'}],'button':0,'hero':'a','hole_cards':['As','Ks'],'samples':200,**kwargs}


def create_personal(c,app,provider='deepseek'):
    result=c.post('/api/rooms',json={'model_ids':[provider],'billing_mode':'personal'},
                  headers={f'X-{provider}-Key':PERSONAL[provider]})
    assert result.status_code==200,result.text
    rid=result.json()['run']['id'];run=app.state.store.run(rid)
    run['seats'].sort(key=lambda s:s['id']!='human-player');run['button']=0
    app.state.store.save_run(run)
    return rid


def assert_private_fields_absent(value):
    if isinstance(value,dict):
        assert not PRIVATE_FIELDS.intersection(value),PRIVATE_FIELDS.intersection(value)
        for child in value.values():assert_private_fields_absent(child)
    elif isinstance(value,list):
        for child in value:assert_private_fields_absent(child)


def assert_keys_not_persisted(store,*keys):
    # Include all SQL tables, not only run JSON: sessions, calls and decisions too.
    dump='\n'.join(store.db.iterdump())
    for key in keys:assert key not in dump
    store.db.execute('PRAGMA wal_checkpoint(FULL)')
    from pathlib import Path
    path=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    for file in path.parent.glob(path.name+'*'):
        raw=file.read_bytes()
        for key in keys:assert key.encode() not in raw


def test_invite_auth_validation_and_once_per_account(public_client):
    c,app,_=public_client
    assert c.post('/api/auth/invite',json={'code':INVITE}).status_code==401
    register(c)
    initial=c.get('/api/auth/budget')
    assert initial.headers['cache-control']=='no-store'
    assert initial.json()=={'invited':False,'limit_cny':0,'used_cny':0,'remaining_cny':0,'exhausted':False}
    assert c.post('/api/auth/invite',json={'code':INVITE},headers={'Origin':'https://other.test'}).status_code==403
    assert c.post('/api/auth/invite',json={'code':'wrong-invite'}).status_code==403
    invalid=c.post('/api/auth/invite',json={'code':'secret-invalid-code'*20})
    assert invalid.status_code==422 and 'secret-invalid-code' not in invalid.text
    activated=c.post('/api/auth/invite',json={'code':INVITE})
    assert activated.status_code==200 and activated.json()['remaining_cny']==5
    uid=app.state.store.db.execute('SELECT user_id FROM player_invites').fetchone()[0]
    before=tuple(app.state.store.db.execute('SELECT * FROM player_invites').fetchone())
    call=app.state.store.reserve('advisor/'+uid,5,10000,20,provider='deepseek')
    app.state.store.finish_call(call,5,{})
    c.post('/api/auth/logout');c.post('/api/auth/login',json={'id':'Alice','password':PASSWORD})
    again=c.post('/api/auth/invite',json={'code':INVITE}).json()
    assert again=={'invited':True,'limit_cny':5,'used_cny':5,'remaining_cny':0,'exhausted':True}
    assert tuple(app.state.store.db.execute('SELECT * FROM player_invites').fetchone())==before
    second=TestClient(app);register(second,'Bob')
    assert second.get('/api/auth/budget').json()['remaining_cny']==0
    assert second.post('/api/auth/invite',json={'code':INVITE}).json()['remaining_cny']==5
    assert_keys_not_persisted(app.state.store,INVITE)


@pytest.mark.parametrize('provider',['bot','jev'])
def test_uninvited_accounts_can_open_local_and_jev_rooms(public_client,provider):
    c,app,_=public_client;register(c)
    assert c.post('/api/rooms',json={'model_ids':[provider]}).status_code==200
    assert c.post('/api/rooms',json={'model_ids':['deepseek']}).status_code==403
    assert c.post('/api/rooms',json={'model_ids':['bot','deepseek']}).status_code==403
    assert len(app.state.store.runs())==1


@pytest.mark.parametrize('provider',['deepseek','jev'])
def test_personal_room_missing_key_cannot_mutate_or_fall_back(public_client,model_http,provider):
    c,app,_=public_client;register(c)
    assert c.post('/api/rooms',json={'model_ids':[provider],'billing_mode':'personal'}).status_code==422
    rid=create_personal(c,app,provider)
    before=app.state.store.run(rid)
    assert c.post(f'/api/runs/{rid}/start').status_code==422
    assert app.state.store.run(rid)==before and model_http==[]
    headers={f'X-{provider}-Key':PERSONAL[provider]}
    assert c.post(f'/api/runs/{rid}/start',headers=headers).status_code==200
    data=wait_state(c,rid,{})
    assert data['can_act'] and rid not in app.state.runner.personal_keys
    run_before=app.state.store.run(rid);hand_before=app.state.store.hand(rid,1)
    body={'hand_number':1,'action_count':0,'action_id':'call'}
    assert c.post(f'/api/rooms/{rid}/action',json=body).status_code==422
    assert app.state.store.run(rid)==run_before and app.state.store.hand(rid,1)==hand_before
    assert model_http==[]
    assert c.post(f'/api/rooms/{rid}/action',json=body,headers=headers).status_code==200
    wait_state(c,rid,{})
    assert model_http and rid not in app.state.runner.personal_keys
    for url,kwargs in model_http:
        expected='https://api.deepseek.com/chat/completions' if provider=='deepseek' else 'https://api.typesafe.ai/v1/systemone'
        assert url==expected
        assert kwargs['headers']['Authorization']=='Bearer '+PERSONAL[provider]
    assert app.state.store.spend()==0
    assert c.get('/api/auth/budget').json()['used_cny']==0
    assert_keys_not_persisted(app.state.store,*PERSONAL.values())
    # Recreating the runner may recover game state, but cannot recover credentials.
    restored=Runner(app.state.store,app.state.provider)
    with pytest.raises(ValueError):restored.start(rid)
    assert restored.personal_keys=={}


@pytest.mark.parametrize('provider',['deepseek','jev'])
def test_personal_provider_failure_never_retries_using_server_key(public_client,monkeypatch,provider):
    c,app,_=public_client;register(c)
    sent=[]
    async def denied(client,url,**kwargs):
        sent.append((url,kwargs['headers']))
        return httpx.Response(401,text=PERSONAL[provider])
    monkeypatch.setattr(httpx.AsyncClient,'post',denied)
    result=c.post('/api/advisor/advise',json=advisor_body(model_entry_id=provider,billing_mode='personal'),
                  headers={f'X-{provider}-Key':PERSONAL[provider]})
    assert result.status_code==502 and PERSONAL[provider] not in result.text
    assert len(sent)==1 and sent[0][1]['Authorization']=='Bearer '+PERSONAL[provider]
    assert app.state.store.spend()==0
    assert app.state.store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]==0
    assert_keys_not_persisted(app.state.store,*PERSONAL.values())


@pytest.mark.parametrize('provider',['deepseek','jev'])
def test_personal_calls_bypass_exhausted_host_budget_and_invite_credit(public_client,model_http,provider):
    c,app,_=public_client;register(c)
    assert c.post('/api/auth/invite',json={'code':INVITE}).status_code==200
    uid=app.state.store.db.execute('SELECT user_id FROM player_invites').fetchone()[0]
    app.state.provider.settings.pokerbench_budget_cny=5
    call=app.state.store.reserve('advisor/'+uid,5,5,20,provider='deepseek')
    app.state.store.finish_call(call,5,{})
    rid=create_personal(c,app,provider)
    headers={f'X-{provider}-Key':PERSONAL[provider]}
    assert c.post(f'/api/runs/{rid}/start',headers=headers).status_code==200
    wait_state(c,rid,{})
    assert c.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'call'},headers=headers).status_code==200
    wait_state(c,rid,{})
    assert model_http and app.state.store.spend(rid)>0
    assert app.state.store.spend()==5 and usage(app.state.store.db,uid)==5
    assert all(json.loads(call['body'])['self_funded'] for call in app.state.store.calls(rid))


def test_advisor_defaults_to_jev_and_requires_authentication(public_client,model_http):
    c,app,_=public_client
    assert AdvisorInput.model_validate(advisor_body()).model_entry_id=='jev'
    assert c.post('/api/advisor/advise',json=advisor_body()).status_code==401
    assert model_http==[] and app.state.store.calls()==[]
    register(c)
    result=c.post('/api/advisor/advise',json=advisor_body())
    assert result.status_code==200,result.text
    assert result.json()['decision']['provider']=='jev'
    assert_private_fields_absent(result.json())
    before=len(model_http)
    assert c.post('/api/advisor/advise',json=advisor_body(model_entry_id='deepseek')).status_code==403
    assert len(model_http)==before
    assert c.post('/api/advisor/advise',json=advisor_body(),headers={'Origin':'https://other.test'}).status_code==403
    assert len(model_http)==before


def test_pending_room_reservation_blocks_sponsored_advisor_but_not_personal(public_client,model_http):
    c,app,_=public_client;register(c)
    c.post('/api/auth/invite',json={'code':INVITE})
    rid=c.post('/api/rooms',json={'model_ids':['deepseek']}).json()['run']['id']
    call=app.state.store.reserve(rid,5,10000,20,provider='deepseek')
    blocked=c.post('/api/advisor/advise',json=advisor_body(model_entry_id='deepseek'))
    assert blocked.status_code==402 and model_http==[]
    personal=c.post('/api/advisor/advise',json=advisor_body(model_entry_id='deepseek',billing_mode='personal'),headers={'X-DeepSeek-Key':PERSONAL['deepseek']})
    assert personal.status_code==200,personal.text
    assert app.state.store.spend()==5
    app.state.store.finish_call(call,4,{})
    assert c.post('/api/advisor/advise',json=advisor_body(model_entry_id='deepseek')).status_code==200
    budget=c.get('/api/auth/budget').json()
    assert 4<budget['used_cny']<5
    assert len(model_http)==2


@pytest.mark.parametrize('key',['bad key','x'*1025])
def test_invalid_personal_key_is_not_echoed_or_persisted(public_client,key):
    c,app,_=public_client;register(c)
    result=c.post('/api/rooms',json={'model_ids':['deepseek'],'billing_mode':'personal'},headers={'X-DeepSeek-Key':key})
    assert result.status_code==422 and key not in result.text
    assert app.state.store.runs()==[] and app.state.store.calls()==[]
    assert_keys_not_persisted(app.state.store,key)


def test_public_views_recursively_hide_endpoints_keys_and_costs(public_client):
    c,app,registry=public_client
    run=app.state.runner.create(RunConfig(entries=registry[:2]))
    hand=Hand(run['seats'],run['button'],(50,100,0),0)
    while hand.state.status:
        hand.apply(hand.legal()[0].id,{'cost_cny_estimate':123,'raw_response':{'secret':'provider-private'},'request':{'private':'payload'}})
    app.state.store.save_run(run,hand.record())
    rid=run['id']
    for path in ['/api/entries','/api/rooms/models','/api/defaults','/api/runs',f'/api/runs/{rid}',f'/api/runs/{rid}/hands',f'/api/runs/{rid}/hands/1']:
        result=c.get(path)
        assert result.status_code==200,result.text
        assert_private_fields_absent(result.json())
        assert 'private-ds-proxy' not in result.text and 'private-local.test' not in result.text
    assert c.get('/api/admin/entries').status_code==401
    assert 'base_url' in c.get('/api/admin/entries',headers=ADMIN).json()[0]


@pytest.mark.parametrize('reader',['anonymous','other','admin'])
def test_human_runs_hidden_and_every_read_and_stream_requires_owner(public_client,reader):
    c,app,_=public_client;register(c)
    rid=c.post('/api/rooms',json={'model_ids':['bot']}).json()['run']['id']
    run=app.state.store.run(rid)
    hand=Hand(run['seats'],run['button'],(50,100,0),0)
    app.state.store.save_run(run,hand.record())
    outsider=TestClient(app)
    if reader=='other':register(outsider,'Bob')
    headers=ADMIN if reader=='admin' else {}
    expected=403 if reader=='other' else 401
    for client in (c,outsider):
        assert rid not in [r['id'] for r in client.get('/api/runs').json()]
    for suffix in ('','/hands','/hands/1','/stream'):
        response=outsider.get(f'/api/runs/{rid}'+suffix,headers=headers)
        assert response.status_code==expected,(suffix,response.text)
    assert outsider.get(f'/api/rooms/{rid}/state',headers=headers).status_code==expected
    for suffix in ('','/hands','/hands/1'):
        assert c.get(f'/api/runs/{rid}'+suffix).status_code==200
    assert c.get(f'/api/runs/{rid}/export').status_code==401
    assert c.get(f'/api/runs/{rid}/export',headers=ADMIN).status_code==200


@pytest.mark.asyncio
async def test_owned_human_stream_sends_sanitized_state(public_client):
    c,app,_=public_client;register(c)
    rid=c.post('/api/rooms',json={'model_ids':['bot']}).json()['run']['id']
    disconnected=asyncio.Event();messages=[]
    async def receive():
        await disconnected.wait()
        return {'type':'http.disconnect'}
    async def send(message):
        messages.append(message)
        if message['type']=='http.response.body' and message.get('body'):
            disconnected.set()
    scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
           'method':'GET','scheme':'http','path':f'/api/runs/{rid}/stream','raw_path':f'/api/runs/{rid}/stream'.encode(),
           'root_path':'','query_string':b'','server':('testserver',80),'client':('testclient',123),
           'headers':[(b'host',b'testserver'),(b'cookie',f'{COOKIE}={c.cookies.get(COOKIE)}'.encode())]}
    await asyncio.wait_for(app(scope,receive,send),2)
    assert messages[0]['status']==200
    first=next(m['body'] for m in messages if m['type']=='http.response.body' and m.get('body'))
    payload=json.loads(first.decode().removeprefix('data: ').strip())
    assert payload['id']==rid
    assert_private_fields_absent(payload)


@pytest.mark.asyncio
async def test_local_and_jev_have_separate_128_slots_while_cloud_calls_are_independent(tmp_path,monkeypatch):
    provider=Provider(Settings(_env_file=None,deepseek_api_key='test-only',jev_api_key='test-only'),Store(str(tmp_path/'capacity.db')))
    provider.env_keys={}
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.setenv('JEV_API_KEY','test-only')
    kinds={'local':'systemone','jev':'jev','deepseek':'deepseek','gpt':'openai_compatible'}
    entries={name:Entry(id=name,name=name,provider=kind,base_url=f'https://{name}.test',
                       key_env='JEV_API_KEY' if name=='jev' else 'DEEPSEEK_API_KEY',
                       input_cny_per_million=0,output_cny_per_million=0) for name,kind in kinds.items()}
    counts={name:0 for name in kinds}
    limits={'local':126,'jev':126,'deepseek':129,'gpt':129}
    full={name:asyncio.Event() for name in kinds}
    release={name:asyncio.Event() for name in kinds}
    req={'state':{},'questions':{'action':{'type':'choice','criteria':{'fold':{},'call':{}}}}}

    class OfflineHTTP:
        # Avoid hundreds of real HTTP/TLS client constructors: hold requests at
        # the transport boundary while retaining Provider.call and its limiter.
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            name=url.split('/')[2].removesuffix('.test')
            counts[name]+=1
            if counts[name]==limits[name]:full[name].set()
            await release[name].wait()
            if name in ('local','jev'):
                return httpx.Response(200,json={'answers':{'action':{'type':'choice','choice':'call',
                    'probabilities':{'call':1,'fold':0},'confidence':1}}})
            return httpx.Response(200,json={'model':'deepseek-flash','usage':{'prompt_tokens':1,'completion_tokens':1},
                'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':{'action':{'fold':0,'call':1}}})}}]})

    monkeypatch.setattr(httpx,'AsyncClient',OfflineHTTP)
    tasks={}
    try:
        for names in (('local','jev'),('deepseek','gpt')):
            for name in names:
                tasks[name]=[asyncio.create_task(provider.call(entries[name],req,decision_id=f'{name}-{i}',run_id=name)) for i in range(129)]
            try:
                await asyncio.wait_for(asyncio.gather(*(full[name].wait() for name in names)),5)
            except TimeoutError:
                pytest.fail(f'Provider pools did not independently reach their required capacities: {counts}')
        assert counts==limits
        assert not any(task.done() for group in tasks.values() for task in group)
        # Both cloud providers can exceed 128 in flight even while both bounded
        # pools are full; finishing cloud calls must not release a bounded slot.
        for name in ('deepseek','gpt'):
            release[name].set()
            results=await asyncio.wait_for(asyncio.gather(*tasks[name]),5)
            assert len(results)==129 and all(r['answers']['action']['choice']=='call' for r in results)
        assert counts['local']==counts['jev']==126
        release['local'].set()
        await asyncio.wait_for(asyncio.gather(*tasks['local']),5)
        assert counts['local']==129 and counts['jev']==126
        assert not any(task.done() for task in tasks['jev'])
        release['jev'].set()
        await asyncio.wait_for(asyncio.gather(*tasks['jev']),5)
        assert counts=={name:129 for name in kinds}
    finally:
        for event in release.values():event.set()
        pending=[task for group in tasks.values() for task in group]
        for task in pending:
            if not task.done():task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        provider.store.db.close()


@pytest.mark.parametrize('provider',['deepseek','jev'])
def test_personal_advisor_missing_or_wrong_provider_key_never_uses_host(public_client,model_http,provider):
    c,app,_=public_client;register(c)
    c.post('/api/auth/invite',json={'code':INVITE})
    other='jev' if provider=='deepseek' else 'deepseek'
    body=advisor_body(model_entry_id=provider,billing_mode='personal')
    for headers in ({},{f'X-{other}-Key':PERSONAL[other]},{f'X-{provider}-Key':' '}):
        result=c.post('/api/advisor/advise',json=body,headers=headers)
        assert result.status_code==422,result.text
    assert model_http==[] and app.state.store.calls()==[]


def test_personal_keys_are_cleared_when_human_fold_finishes_hand(public_client,model_http):
    c,app,_=public_client;register(c)
    rid=create_personal(c,app)
    headers={'X-DeepSeek-Key':PERSONAL['deepseek']}
    c.post(f'/api/runs/{rid}/start',headers=headers);wait_state(c,rid,{})
    result=c.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'fold'},headers=headers)
    assert result.status_code==200,result.text
    assert app.state.store.hand(rid,1)['complete']
    assert app.state.store.run(rid)['status']=='waiting_next_hand'
    assert rid not in app.state.runner.personal_keys
    assert model_http==[]
    assert_keys_not_persisted(app.state.store,*PERSONAL.values())


def test_personal_keys_required_for_every_active_provider_not_retired_seats(public_client,model_http):
    c,app,_=public_client;register(c)
    body={'model_ids':['deepseek','jev','bot'],'billing_mode':'personal'}
    ds={'X-DeepSeek-Key':PERSONAL['deepseek']};jev={'X-Jev-Key':PERSONAL['jev']}
    assert c.post('/api/rooms',json=body,headers=ds).status_code==422
    result=c.post('/api/rooms',json=body,headers={**ds,**jev})
    assert result.status_code==200,result.text
    rid=result.json()['run']['id']
    before=app.state.store.run(rid)
    assert c.post(f'/api/runs/{rid}/start',headers=ds).status_code==422
    assert app.state.store.run(rid)==before and model_http==[]
    assert c.post(f'/api/rooms/{rid}/kick/opponent-1').status_code==200
    assert c.post(f'/api/runs/{rid}/start',headers=jev).status_code==200
    state=wait_state(c,rid,{})
    assert state['can_act'] and rid not in app.state.runner.personal_keys
    assert all('deepseek' not in url for url,_ in model_http)


def test_completed_benchmark_raw_export_requires_admin(public_client):
    c,app,registry=public_client
    run=app.state.runner.create(RunConfig(entries=registry[:2]))
    run['status']='complete';app.state.store.save_run(run)
    url=f"/api/runs/{run['id']}/export"
    assert c.get(url).status_code==401
    register(c)
    assert c.get(url).status_code==401
    assert c.get(url,headers=ADMIN).status_code==200


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_kind',['deepseek','jev','openai_compatible'])
@pytest.mark.parametrize('corrective_retry',[False,True])
async def test_reflected_personal_key_never_reaches_audit_decision_or_database(tmp_path,monkeypatch,provider_kind,corrective_retry):
    import pokerbench.provider as provider_module
    secret='visitor-reflected-key-7391'
    store=Store(str(tmp_path/'reflection.db'))
    provider=Provider(Settings(_env_file=None,deepseek_api_key='host-only',jev_api_key='host-only'),store)
    entry=Entry(id='reflector',name='Reflector',provider=provider_kind,
                key_env='JEV_API_KEY' if provider_kind=='jev' else 'DEEPSEEK_API_KEY',
                credential_id='custom-reflector' if provider_kind=='openai_compatible' else None,
                base_url='https://reflection.example/v1')
    monkeypatch.delenv('POKERBENCH_CUSTOM_PROXY_URL',raising=False)
    req={'state':{},'questions':{'action':{'type':'choice','criteria':{'fold':{},'call':{}}}}}
    reflected={f'field-{secret}':[{'nested':['Bearer '+secret,{'value':secret}]}]}
    sent=[]
    async def no_sleep(_):pass
    async def post(client,url,**kwargs):
        assert kwargs['headers']['Authorization']=='Bearer '+secret
        sent.append(deepcopy(kwargs['json']))
        raw={'model':'served-'+secret,'metadata':reflected}
        if provider_kind=='jev':
            raw.update(usage={'input_tokens':10,'output_tokens':5},
                answers={'action':{'type':'choice','choice':'call','probabilities':{'fold':0,'call':1},
                                   'confidence':1,'untrusted_detail':reflected}})
            if corrective_retry and len(sent)==1:raw['answers']={}
        else:
            content={'answers':{'action':{'fold':0,'call':1}}}
            raw.update(usage={'prompt_tokens':10,'completion_tokens':5},choices=[{'finish_reason':'stop',
                'message':{'content':json.dumps(content),'untrusted_detail':reflected}}])
            if corrective_retry and len(sent)==1:raw['choices'][0]['message']['content']='malformed '+secret
        return httpx.Response(200,json=raw)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    monkeypatch.setattr(provider_module.asyncio,'sleep',no_sleep)
    try:
        result=await provider.call(entry,req,decision_id='reflected',run_id='personal',key_override=secret)
        assert len(sent)==(2 if corrective_retry else 1)
        assert result['answers']['action']['choice']=='call'
        assert secret not in json.dumps(result)
        assert store.decision('reflected')==result
        assert len(store.calls())==len(sent)
        for call in store.calls():
            audit=json.loads(call['body'])
            assert audit['self_funded'] and '[redacted]' in json.dumps(audit)
            assert secret not in json.dumps(audit)
        assert store.spend()==0
        assert_keys_not_persisted(store,secret)
    finally:store.db.close()

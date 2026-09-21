"""Custom-agent API contracts; endpoint transport security has its own tests."""
from copy import deepcopy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_accounts import register
from test_public_release import public_client, model_http, assert_keys_not_persisted, assert_private_fields_absent
from test_rooms import wait_state

CUSTOM_KEY='visitor-own-gpt-key'
CUSTOM_HEADERS={'X-Agent-Keys':json.dumps({'custom-gpt':CUSTOM_KEY})}


def custom_body(**updates):
    return {'model_ids':['custom-gpt'],'billing_mode':'personal',
            'custom_agents':[{'id':'custom-gpt','name':'My GPT','model':'gpt-5.6-luna',
                              'endpoint':'https://user-agent.example/v1/chat/completions'}],**updates}


def make_custom(c,app):
    response=c.post('/api/rooms',json=custom_body(),headers=CUSTOM_HEADERS)
    assert response.status_code==200,response.text
    rid=response.json()['run']['id'];run=app.state.store.run(rid)
    run['seats'].sort(key=lambda s:s['id']!='human-player');run['button']=0
    app.state.store.save_run(run)
    return rid,response


def test_own_gpt_uses_credential_id_and_keeps_key_endpoint_and_room_private(public_client,model_http):
    c,app,_=public_client;register(c)
    rid,created=make_custom(c,app)
    public=created.json()['run']
    bot=next(e for e in public['entries'] if e['id']=='opponent-1')
    assert bot['credential_id']=='custom-gpt' and bot['model']=='gpt-5.6-luna'
    assert_private_fields_absent(public)
    assert 'user-agent.example' not in created.text and CUSTOM_KEY not in created.text
    headers={**CUSTOM_HEADERS,'X-DeepSeek-Key':'wrong-provider-key','X-Jev-Key':'wrong-jev-key'}
    before=app.state.store.run(rid)
    assert c.post(f'/api/runs/{rid}/start').status_code==422
    assert app.state.store.run(rid)==before and model_http==[]
    assert c.post(f'/api/runs/{rid}/start',headers=headers).status_code==200
    assert wait_state(c,rid,{})['can_act']
    hand_before=deepcopy(app.state.store.hand(rid,1));run_before=app.state.store.run(rid)
    action={'hand_number':1,'action_count':0,'action_id':'call'}
    assert c.post(f'/api/rooms/{rid}/action',json=action).status_code==422
    assert app.state.store.hand(rid,1)==hand_before and app.state.store.run(rid)==run_before
    assert c.post(f'/api/rooms/{rid}/action',json=action,headers=headers).status_code==200
    wait_state(c,rid,{})
    assert model_http and rid not in app.state.runner.personal_keys
    for url,kwargs in model_http:
        assert url=='https://user-agent.example/v1/chat/completions'
        assert kwargs['headers']['Authorization']=='Bearer '+CUSTOM_KEY
        assert kwargs['json']['model']=='gpt-5.6-luna'
    assert all(json.loads(row['body'])['self_funded'] for row in app.state.store.calls(rid))
    assert app.state.store.spend()==0 and c.get('/api/auth/budget').json()['used_cny']==0
    for path in [f'/api/runs/{rid}',f'/api/runs/{rid}/hands/1',f'/api/rooms/{rid}/state','/api/rooms/mine']:
        result=c.get(path)
        assert result.status_code==200,result.text
        assert_private_fields_absent(result.json())
        assert 'user-agent.example' not in result.text and CUSTOM_KEY not in result.text
    for path in ['/api/entries','/api/rooms/models','/api/defaults','/api/runs']:
        assert 'custom-gpt' not in c.get(path).text
    assert_keys_not_persisted(app.state.store,CUSTOM_KEY,'wrong-provider-key','wrong-jev-key')
    for reader,status in [('anonymous',401),('other',403)]:
        outsider=TestClient(app)
        if reader=='other':register(outsider,'Bob')
        for suffix in ('','/hands','/hands/1','/stream'):
            assert outsider.get(f'/api/runs/{rid}'+suffix,headers=CUSTOM_HEADERS).status_code==status
        assert outsider.get(f'/api/rooms/{rid}/state',headers=CUSTOM_HEADERS).status_code==status
        assert outsider.get('/api/rooms/mine').status_code==(401 if reader=='anonymous' else 200)
        if reader=='other':assert outsider.get('/api/rooms/mine').json()==[]


def test_custom_agent_cannot_use_hosted_money_or_another_credential(public_client,model_http):
    c,app,_=public_client;register(c)
    assert c.post('/api/rooms',json=custom_body(billing_mode='hosted'),headers=CUSTOM_HEADERS).status_code==422
    for headers in ({},{'X-DeepSeek-Key':CUSTOM_KEY},{'X-Agent-Keys':json.dumps({'custom-other':CUSTOM_KEY})}):
        assert c.post('/api/rooms',json=custom_body(),headers=headers).status_code==422
    assert app.state.store.runs()==[] and app.state.store.calls()==[] and model_http==[]
    assert_keys_not_persisted(app.state.store,CUSTOM_KEY)


def test_custom_key_auth_failure_never_falls_back_to_host_provider(public_client,monkeypatch):
    c,app,_=public_client;register(c)
    rid,_=make_custom(c,app);sent=[]
    async def rejected(client,url,**kwargs):
        sent.append((url,kwargs['headers']))
        return httpx.Response(401,text='echo '+CUSTOM_KEY)
    monkeypatch.setattr(httpx.AsyncClient,'post',rejected)
    assert c.post(f'/api/runs/{rid}/start',headers=CUSTOM_HEADERS).status_code==200
    wait_state(c,rid,{})
    assert c.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'call'},headers=CUSTOM_HEADERS).status_code==200
    state=wait_state(c,rid,{})
    assert sent and all(url=='https://user-agent.example/v1/chat/completions' and headers['Authorization']=='Bearer '+CUSTOM_KEY for url,headers in sent)
    assert any(e['type']=='model_error' for e in state['run']['events'])
    assert app.state.store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]==0
    assert app.state.store.spend()==0 and rid not in app.state.runner.personal_keys
    assert CUSTOM_KEY not in json.dumps(state)
    assert_keys_not_persisted(app.state.store,CUSTOM_KEY)


@pytest.mark.parametrize('encoded',['not-json','[]','{"openai_compatible":"key"}','{"custom-gpt":7}','{"custom-gpt":"bad key"}'])
def test_invalid_custom_key_map_is_rejected_without_echo_or_persistence(public_client,encoded):
    c,app,_=public_client;register(c)
    result=c.post('/api/rooms',json=custom_body(),headers={'X-Agent-Keys':encoded})
    assert result.status_code==422 and encoded not in result.text
    assert app.state.store.runs()==[] and app.state.store.calls()==[]


def test_distinct_custom_agents_require_their_own_credential_ids(public_client,model_http):
    c,app,_=public_client;register(c)
    body=custom_body()
    second={**body['custom_agents'][0],'id':'custom-second','name':'Another GPT','endpoint':'https://second-agent.example/v1'}
    body['custom_agents'].append(second);body['model_ids'].append('custom-second')
    assert c.post('/api/rooms',json=body,headers=CUSTOM_HEADERS).status_code==422
    both={'X-Agent-Keys':json.dumps({'custom-gpt':CUSTOM_KEY,'custom-second':'second-own-key'})}
    result=c.post('/api/rooms',json=body,headers=both)
    assert result.status_code==200,result.text
    rid=result.json()['run']['id'];before=app.state.store.run(rid)
    # Both custom seats act before the human, so routing assertions cannot pass
    # without exercising each endpoint and its own credential.
    before['seats'].sort(key=lambda s:s['id']);before['button']=1
    app.state.store.save_run(before)
    assert {e['credential_id'] for e in result.json()['run']['entries'] if e['provider']!='human'}=={'custom-gpt','custom-second'}
    assert c.post(f'/api/runs/{rid}/start',headers=CUSTOM_HEADERS).status_code==422
    assert app.state.store.run(rid)==before and model_http==[]
    assert c.post(f'/api/runs/{rid}/start',headers=both).status_code==200
    wait_state(c,rid,{})
    assert {url for url,_ in model_http}=={'https://user-agent.example/v1/chat/completions','https://second-agent.example/v1/chat/completions'}
    for url,kwargs in model_http:
        expected=CUSTOM_KEY if 'user-agent.example' in url else 'second-own-key'
        assert kwargs['headers']['Authorization']=='Bearer '+expected
    assert rid not in app.state.runner.personal_keys
    assert_keys_not_persisted(app.state.store,CUSTOM_KEY,'second-own-key')

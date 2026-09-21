import json

import httpx
import pytest
from pydantic import ValidationError

from pokerbench.config import Settings
from pokerbench.custom_protocol import agent_endpoint, chat_response
from pokerbench.provider import Provider
from pokerbench.rooms import CustomAgent
from pokerbench.store import Store
from test_accounts import register
from test_custom_agents import custom_body, CUSTOM_HEADERS
from test_public_release import public_client, assert_keys_not_persisted


@pytest.mark.parametrize('url,kind,base',[
    ('https://api.openai.com','chat_completions','https://api.openai.com/v1'),
    ('https://api.openai.com/v1/chat/completions/','chat_completions','https://api.openai.com/v1'),
    ('https://api.openai.com/v1/responses','responses','https://api.openai.com/v1'),
    ('https://api.anthropic.com','anthropic','https://api.anthropic.com/v1'),
    ('https://api.anthropic.com/v1/messages/','anthropic','https://api.anthropic.com/v1'),
    ('https://gateway.example/proxy/claude/v1/messages','anthropic','https://gateway.example/proxy/claude/v1'),
    ('https://resource.openai.azure.com/openai/v1/responses','responses','https://resource.openai.azure.com/openai/v1'),
])
def test_agent_endpoint_preserves_cloud_prefixes_and_full_routes(url,kind,base):
    assert agent_endpoint(url,kind)==base


@pytest.mark.parametrize('kind,path', [('anthropic','chat/completions'),('chat_completions','responses'),('responses','messages')])
def test_wrong_api_format_fails_before_sending_credentials(kind,path):
    with pytest.raises(ValidationError,match='does not match'):
        CustomAgent(id='custom-one',name='One',model='one',api_format=kind,endpoint='https://gateway.example/v1/'+path)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,host', [('chat_completions','api.openai.com'),('responses','api.openai.com'),('anthropic','api.anthropic.com')])
async def test_native_formats_use_official_adapter_and_only_matching_user_key(tmp_path,monkeypatch,kind,host):
    store=Store(str(tmp_path/'db'));provider=Provider(Settings(_env_file=None),store)
    entry=CustomAgent(id='custom-one',name='One',model='my-model',endpoint='https://'+host,api_format=kind).entry()
    key='personal-test-key';sent=[]
    content=json.dumps({'answers':{'action':{'fold':.25,'call':.75}}})
    async def post(client,url,**kw):
        sent.append((url,kw));body=kw['json'];headers=kw['headers']
        assert body['model']=='my-model' and key not in json.dumps(body)
        if kind=='anthropic':
            assert url=='https://api.anthropic.com/v1/messages'
            assert headers=={'x-api-key':key,'anthropic-version':'2023-06-01'}
            assert body['system'] and all(m['role']!='system' for m in body['messages'])
            assert 'response_format' not in body
            raw={'model':'my-model','content':[{'type':'text','text':content}], 'stop_reason':'end_turn','usage':{'input_tokens':10,'output_tokens':5}}
        elif kind=='responses':
            assert url=='https://api.openai.com/v1/responses' and headers=={'Authorization':'Bearer '+key}
            assert body['store'] is False and body['input'] and body['text']['format']=={'type':'json_object'}
            assert 'max_output_tokens' in body and 'messages' not in body
            raw={'model':'my-model','status':'completed','output':[{'type':'reasoning','summary':[]},{'type':'message','content':[{'type':'output_text','text':content}]}],'usage':{'input_tokens':10,'output_tokens':5}}
        else:
            assert url=='https://api.openai.com/v1/chat/completions' and headers=={'Authorization':'Bearer '+key}
            assert body['max_completion_tokens']==8192 and body['store'] is False and 'max_tokens' not in body
            raw={'model':'my-model','choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
        return httpx.Response(200,json=raw)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    request={'state':{'hand':1},'questions':{'action':{'type':'choice','criteria':{'fold':{},'call':{}}}}}
    result=await provider.call(entry,request,decision_id='one',run_id='room',key_override=key)
    assert result['answers']['action']['probabilities']=={'fold':.25,'call':.75}
    assert result['usage']=={'input_tokens':10,'output_tokens':5} and len(sent)==1
    assert store.spend()==0
    assert_keys_not_persisted(store,key)
    store.db.close()


@pytest.mark.parametrize('raw,kind',[
    ({'stop_reason':'max_tokens','content':[{'type':'text','text':'{}'}]},'anthropic'),
    ({'stop_reason':'end_turn','content':[{'type':'tool_use','name':'not-a-decision'}]},'anthropic'),
    ({'status':'incomplete','output':[{'type':'message','content':[{'type':'output_text','text':'{}'}]}]},'responses'),
    ({'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'No'}]}]},'responses'),
    ({'status':'completed','output':[{'type':'function_call','name':'tool'},{'type':'message','content':[{'type':'output_text','text':'{}'}]}]},'responses'),
])
def test_truncated_refused_or_tool_outputs_are_not_accepted(raw,kind):
    assert chat_response(raw,kind)['choices'][0]['finish_reason']!='stop'


@pytest.mark.parametrize('endpoint',['https://person:secret-in-url@api.openai.com/v1','https://api.openai.com/v1?key=secret-in-url'])
def test_validation_never_echoes_endpoint_credentials(public_client,endpoint):
    c,app,_=public_client;register(c)
    body=custom_body();body['custom_agents'][0]['endpoint']=endpoint
    r=c.post('/api/rooms',json=body,headers=CUSTOM_HEADERS)
    assert r.status_code==422 and 'secret-in-url' not in r.text and 'input' not in r.text
    assert r.headers['cache-control']=='no-store'
    assert app.state.store.runs()==[]

import json
import httpx
import pytest
from pokerbench.config import Settings,Entry,RunConfig
from pokerbench.provider import Provider,ProviderError
from pokerbench.store import Store
from pokerbench.runner import Runner
from pokerbench.advisor import AdvisorInput,reconstruct

REQ={'model':'test','state':{},'questions':{'action':{'type':'choice','criteria':{'fold':{},'call':{}}}}}
GOOD={'answers':{'action':{'fold':.2,'call':.8}}}


def response(content=None,usage=True):
    return httpx.Response(200,json={'model':'verified-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(GOOD) if content is None else content}}],**({'usage':{'prompt_tokens':100,'completion_tokens':50}} if usage else {})})


@pytest.fixture
def provider(tmp_path,monkeypatch):
    from pokerbench import provider as module
    async def no_sleep(_):pass
    monkeypatch.setattr(module.asyncio,'sleep',no_sleep)
    store=Store(str(tmp_path/'test.db'))
    return Provider(Settings(_env_file=None,deepseek_api_key='test-only'),store)


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',['json','missing','negative','raw_json','raw_list','usage_list','none_choice'])
async def test_invalid_then_success_retries_without_changing_action_state(provider,monkeypatch,invalid):
    n=0;sent=[]
    async def post(client,url,**kw):
        nonlocal n
        n+=1;sent.append(json.dumps(kw['json'],sort_keys=True))
        if n>1:return response()
        if invalid=='json':return response('{bad')
        if invalid=='raw_json':return httpx.Response(200,text='broken')
        if invalid=='raw_list':return httpx.Response(200,json=[])
        if invalid=='usage_list':return httpx.Response(200,json={'usage':[], 'choices':[]})
        if invalid=='none_choice':return httpx.Response(200,json={'choices':[None]})
        bad=json.loads(json.dumps(GOOD))
        if invalid=='missing':bad['answers']={}
        if invalid=='sum':bad['answers']['action']={'fold':.1,'call':.1}
        if invalid=='negative':bad['answers']['action']['fold']=-.1
        return response(json.dumps(bad))
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    out=await provider.call(Entry(id='d',name='d'),REQ,decision_id='d1',run_id='r')
    assert n==2
    if invalid in ('json','missing','negative'):
        assert len(json.loads(sent[1])['messages'])==4
        assert 'did not match' in json.loads(sent[1])['messages'][-1]['content']
    else:assert len(set(sent))==1
    assert out['answers']['action']['choice']=='call' and out['retry_count']==1
    assert [a['outcome'] for a in out['attempts']]==['rejected','accepted']
    assert provider.store.spend()==pytest.approx(out['cost_cny_estimate'])
    assert json.loads(provider.store.calls()[0]['body'])['reason']
    assert await provider.call(Entry(id='d',name='d'),REQ,decision_id='d1',run_id='r')==out
    assert n==2


@pytest.mark.asyncio
async def test_exhaustion_bounded_and_no_fabricated_decision(provider,monkeypatch):
    async def post(*args,**kwargs):return response('null')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(ProviderError,match='已尝试 3 次'):
        await provider.call(Entry(id='d',name='d'),REQ,decision_id='bad',run_id='r')
    assert len(provider.store.calls())==3 and provider.store.decision('bad') is None


@pytest.mark.asyncio
async def test_auth_error_not_retried(provider,monkeypatch):
    async def post(*args,**kwargs):return httpx.Response(401,text='secret-error-body')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(ProviderError,match='HTTP 401') as e:
        await provider.call(Entry(id='d',name='d'),REQ,decision_id='auth',run_id='r')
    assert 'secret-error-body' not in str(e.value) and len(provider.store.calls())==1


@pytest.mark.asyncio
async def test_retry_cannot_exceed_budget(provider,monkeypatch):
    async def post(*args,**kwargs):return httpx.Response(200,text='bad')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(ValueError,match='预算不足'):
        await provider.call(Entry(id='d',name='d'),REQ,decision_id='budget',run_id='r',run_budget=.1)
    assert len(provider.store.calls())==1


@pytest.mark.asyncio
async def test_markdown_wrapper_repair_needs_no_extra_call(provider,monkeypatch):
    async def post(*args,**kwargs):return response('```json\n'+json.dumps(GOOD)+'\n```')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    out=await provider.call(Entry(id='d',name='d'),REQ,decision_id='fence',run_id='r')
    assert out['retry_count']==0 and out['answers']['action']['choice']=='call'
    assert out['adapter']['name']=='system-one-adapter'


@pytest.mark.asyncio
async def test_benchmark_finishes_after_format_failure(provider,monkeypatch):
    counter=0
    async def post(client,url,**kw):
        nonlocal counter
        counter+=1
        if counter==1:return response('{broken')
        req=json.loads(kw['json']['messages'][1]['content'].removeprefix('<document>\n').removesuffix('\n</document>'))
        choices=[a['id'] for a in req['legal_actions']];choice='fold' if 'fold' in choices else 'check' if 'check' in choices else 'call'
        answer={'answers':{'action':{k:float(k==choice) for k in choices}}}
        return response(json.dumps(answer))
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    r=Runner(provider.store,provider);run=r.create(RunConfig(max_hands=1))
    r.start(run['id']);await r.tasks[run['id']]
    done=r.store.run(run['id'])
    assert done['status']=='complete' and done['hands_played']==1
    actions=r.store.hand(run['id'],1)['actions']
    assert actions[0]['decision']['retry_count']==1
    assert len(actions)==9 and counter==10


def test_assumed_stack_normalized_with_explicit_caveat():
    data=AdvisorInput(seats=[{'id':'p0'},{'id':'p1','stack':321}],hero='p0',button=0,hole_cards=['As','Ks'],stack_mode='assumed',big_blind=200)
    assert [s.stack for s in data.seats]==[20000,20000]
    h=reconstruct(data)
    assert '100BB' in h.stack_assumption and h.snapshot()['seats'][0]['stack']==19950
    exact=AdvisorInput(seats=[{'id':'p0','stack':1000},{'id':'p1','stack':3000}],hero='p0',button=0,hole_cards=['As','Ks'])
    assert [s.stack for s in exact.seats]==[1000,3000] and reconstruct(exact).stack_assumption is None


@pytest.mark.asyncio
async def test_pause_prevents_another_retry(provider,monkeypatch):
    import asyncio
    stopped=False
    async def post(*args,**kwargs):
        nonlocal stopped
        stopped=True
        return response('{bad')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(asyncio.CancelledError):
        await provider.call(Entry(id='d',name='d'),REQ,decision_id='pause',run_id='r',should_stop=lambda:stopped)
    assert len(provider.store.calls())==1 and provider.store.decision('pause') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['rate_limit','timeout'])
async def test_transient_transport_recovery(provider,monkeypatch,failure):
    n=0
    async def post(*args,**kwargs):
        nonlocal n
        n+=1
        if n==1:
            if failure=='timeout':raise httpx.TimeoutException('private transport details')
            return httpx.Response(429)
        return response()
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    out=await provider.call(Entry(id='d',name='d'),REQ,decision_id='transport',run_id='r')
    assert n==2 and out['retry_count']==1
    assert 'private transport details' not in json.dumps(out)


@pytest.mark.asyncio
@pytest.mark.parametrize('last_valid',[True,False])
async def test_network_and_corrective_retries_share_three_request_cap(provider,monkeypatch,last_valid):
    sent=[]
    async def post(client,url,**kw):
        sent.append(kw['json'])
        if len(sent)==1:return httpx.Response(503)
        if len(sent)==2 or not last_valid:return response('{broken')
        return response()
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    call=provider.call(Entry(id='d',name='d'),REQ,decision_id='mixed',run_id='r')
    if last_valid:
        out=await call
        assert out['retry_count']==2 and out['answers']['action']['confidence']==pytest.approx(.6)
    else:
        with pytest.raises(ProviderError,match='已尝试 3 次'):await call
        assert provider.store.decision('mixed') is None
    assert len(sent)==3 and len(sent[2]['messages'])==4
    assert len(provider.store.calls())==3


@pytest.mark.asyncio
async def test_zero_distribution_never_becomes_uniform(provider,monkeypatch):
    async def post(*args,**kwargs):return response(json.dumps({'answers':{'action':{'call':0,'fold':0}}}))
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(ProviderError,match='概率之和'):
        await provider.call(Entry(id='d',name='d'),REQ,decision_id='zero',run_id='r')
    assert provider.store.decision('zero') is None and len(provider.store.calls())==3


@pytest.mark.asyncio
async def test_truncated_valid_json_is_rejected_and_all_tokens_count(provider,monkeypatch):
    n=0
    async def post(*args,**kwargs):
        nonlocal n
        n+=1
        raw=json.loads(response().content)
        if n==1:raw['choices'][0]['finish_reason']='length'
        return httpx.Response(200,json=raw)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    out=await provider.call(Entry(id='d',name='d'),REQ,decision_id='truncated',run_id='r')
    assert n==2 and out['usage']=={'input_tokens':200,'output_tokens':100}
    assert out['cost_cny_estimate']==pytest.approx(provider.store.spend())
    audit=json.loads(provider.store.calls()[0]['body'])
    assert audit['reason']=='模型输出未完整结束'
    assert 'request' in audit and 'request' not in out['attempts'][0]


@pytest.mark.asyncio
async def test_openai_compatible_uses_official_schema_without_thinking_override(provider,monkeypatch):
    sent=[]
    async def post(client,url,**kw):sent.append((url,kw['json']));return response()
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    entry=Entry(id='other',name='other',provider='openai_compatible',base_url='https://example.test/v1',structured_outputs=True)
    out=await provider.call(entry,REQ,decision_id='compatible',run_id='r',max_tokens=2048)
    url,payload=sent[0]
    assert url=='https://example.test/v1/chat/completions'
    assert payload['response_format']['type']=='json_schema' and payload['max_tokens']==2048
    assert not {'temperature','thinking','reasoning_effort'} & payload.keys()
    assert out['adapter']['version']=='0.2.0' and out['adapter']['structured_outputs']
    assert out['answers']['action']['choice']=='call' and out['answers']['action']['confidence']==pytest.approx(.6)


@pytest.mark.asyncio
async def test_explicit_low_effort_is_audited_and_not_reused_as_default(provider,monkeypatch):
    sent=[]
    async def post(client,url,**kw):sent.append(kw['json']);return response()
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    entry=Entry(id='d',name='d',reasoning_effort='low')
    out=await provider.call(entry,REQ,decision_id='low',run_id='r')
    assert sent[0]['reasoning_effort']=='low' and out['thinking']=='low'
    assert 'temperature' not in sent[0] and out['adapter']['version']=='0.2.0'
    assert json.loads(provider.store.calls()[0]['body'])['request']['reasoning_effort']=='low'
    with pytest.raises(ProviderError,match='思考设置不一致'):
        await provider.call(Entry(id='d',name='d'),REQ,decision_id='low',run_id='r')
    assert len(sent)==1


@pytest.mark.asyncio
async def test_commonstack_low_uses_nested_reasoning_parameter(provider,monkeypatch):
    sent=[]
    async def post(client,url,**kw):sent.append(kw['json']);return response()
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    entry=Entry(id='luna',name='Luna',provider='openai_compatible',reasoning_effort='low',reasoning_parameter='reasoning',expected_model='verified-model')
    out=await provider.call(entry,REQ,decision_id='luna-low',run_id='r')
    assert sent[0]['reasoning']=={'effort':'low'} and 'reasoning_effort' not in sent[0]
    assert out['thinking']=='low' and out['model']=='verified-model'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_kind',['systemone','openai_compatible'])
async def test_wrong_actual_model_stops_without_retry_or_action(provider,monkeypatch,provider_kind):
    sent=[]
    async def post(client,url,**kw):
        sent.append(kw['json'])
        return response()
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    entry=Entry(id='wrong',name='Wrong',provider=provider_kind,expected_model='required-model')
    with pytest.raises(ProviderError,match='模型身份不符'):
        await provider.call(entry,REQ,decision_id='wrong',run_id='r')
    assert len(sent)==1 and provider.store.decision('wrong') is None
    assert provider.store.spend()>0


@pytest.mark.asyncio
async def test_native_revision_change_is_rejected_before_action(provider,monkeypatch):
    async def post(client,url,**kw):
        return httpx.Response(200,json={'model':'native','revision':'changed','usage':{'input_tokens':1,'output_tokens':1}})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(ProviderError,match='权重版本不符'):
        await provider.call(Entry(id='n',name='N',provider='systemone',expected_model='native',expected_revision='pinned'),REQ,decision_id='revision',run_id='r')
    assert len(provider.store.calls())==1 and provider.store.decision('revision') is None


@pytest.mark.asyncio
async def test_jev_keeps_native_protocol(provider,monkeypatch):
    sent=[]
    native={'answers':{'action':{'type':'choice','choice':'call','probabilities':{'fold':.2,'call':.8},'confidence':.6}}}
    async def post(client,url,**kw):sent.append((url,kw['json']));return httpx.Response(200,json=native)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    out=await provider.call(Entry(id='j',name='j',provider='jev',base_url='https://api.typesafe.ai'),REQ,decision_id='jev',run_id='r')
    assert sent[0][0].endswith('/v1/systemone') and set(sent[0][1])=={'model','state','questions'}
    assert 'adapter' not in out and out['probability_source']=='endpoint'


@pytest.mark.asyncio
async def test_deepseek_nonthinking_explicitly_disabled_and_cache_isolated(provider,monkeypatch):
    sent=[]
    async def post(client,url,**kw):
        sent.append(kw['json'])
        return httpx.Response(200,json={'model':'deepseek-flash','usage':{'prompt_tokens':10,'completion_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':{'action':{'fold':.6,'call':.4}}})}}]})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    req={'model':'x','state':{},'questions':{'action':{'type':'choice','criteria':{'fold':{},'call':{}}}}}
    entry=Entry(id='d',name='DeepSeek',reasoning_effort='non-thinking')
    result=await provider.call(entry,req,decision_id='nonthinking',run_id='room')
    assert sent[0]['thinking']=={'type':'disabled'} and 'reasoning_effort' not in sent[0]
    assert result['thinking']=='non-thinking'
    with pytest.raises(ProviderError,match='思考设置'):
        await provider.call(entry.model_copy(update={'reasoning_effort':'low'}),req,decision_id='nonthinking',run_id='room')


@pytest.mark.asyncio
async def test_luna_version_suffix_and_nonunit_probabilities(provider,monkeypatch):
    async def post(*args,**kwargs):
        raw=json.loads(response(json.dumps({'answers':{'action':{'fold':.2,'call':.6}}})).content)
        raw['model']='gpt-5.6-luna-2026-07-09'
        return httpx.Response(200,json=raw)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    entry=Entry(id='luna',name='gpt-5.6-luna',provider='openai_compatible',expected_model='gpt-5.6-luna',key_env='')
    out=await provider.call(entry,REQ,decision_id='luna-version',run_id='r')
    assert len(provider.store.calls())==1
    assert out['model']=='gpt-5.6-luna-2026-07-09'
    assert out['answers']['action']['probabilities']==pytest.approx({'fold':.25,'call':.75})
    assert out['answers']['action']['normalization_original_sum']==pytest.approx(.8)
    assert await provider.call(entry,REQ,decision_id='luna-version',run_id='r')==out

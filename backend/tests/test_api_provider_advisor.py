import json
from copy import deepcopy
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pokerbench.advisor import AdvisorInput,reconstruct,equity
from pokerbench.api import create_app
from pokerbench.config import Settings,Entry,RunConfig
from pokerbench.engine import Hand
from pokerbench.provider import Provider,ProviderError,validate_answer
from pokerbench.store import Store


def advisor(**kw):
    return AdvisorInput(seats=[{'id':f'p{i}','stack':2000} for i in range(2)],button=0,hero='p0',hole_cards=['As','Ks'],samples=200,**kw)


def answer(request):
    return {'answers':{qid:{'type':'choice','choice':next(iter(q['criteria'])),'probabilities':{k:1 if i==0 else 0 for i,k in enumerate(q['criteria'])},'confidence':.5} for qid,q in request['questions'].items()}}


def test_advisor_invalid_insert_and_duplicate_card():
    with pytest.raises(ValidationError):advisor(events=[{'kind':'board','cards':['As','Kd','2c']}])
    with pytest.raises(ValueError,match='第 1 条事件'):
        reconstruct(advisor(events=[{'id':'bad','kind':'call','player':'p1'}]))
    with pytest.raises(ValueError,match='当前不是发公共牌'):
        reconstruct(advisor(events=[{'kind':'board','cards':['Ac','Kd','2c']}]))


def test_advisor_hero_pot_odds_only_on_hero_turn():
    h=reconstruct(advisor())
    assert equity(h,'p0',200)['pot_odds']==.25
    h.apply('call')
    assert equity(h,'p0',200)['pot_odds'] is None


def test_advisor_shared_royal_board_is_split():
    data=advisor(events=[{'kind':'call','player':'p0'},{'kind':'check','player':'p1'},
                         {'kind':'board','cards':['Ah','Kh','Qh']},
                         {'kind':'check','player':'p1'},{'kind':'check','player':'p0'},
                         {'kind':'board','cards':['Jh']},
                         {'kind':'check','player':'p1'},{'kind':'check','player':'p0'},
                         {'kind':'board','cards':['Th']}])
    e=equity(reconstruct(data),'p0',200)
    assert e['win']==0 and e['tie']==1 and e['equity']==.5


@pytest.mark.parametrize('case',['missing','extra','negative','nan','choice','confidence','type'])
def test_reject_invalid_probability_contract(case):
    req={'questions':{'action':{'criteria':{'call':{},'fold':{}}}}};raw=answer(req);a=raw['answers']['action']
    if case=='missing':a['probabilities'].pop('fold')
    if case=='extra':raw['answers']['extra']={}
    if case=='negative':a['probabilities']['fold']=-.1
    if case=='nan':a['probabilities']['fold']=float('nan')
    if case=='sum':a['probabilities']['call']=.8
    if case=='choice':a['choice']='fold'
    if case=='confidence':a['confidence']=True
    if case=='type':raw['answers']['action']=None
    with pytest.raises(ProviderError):validate_answer(raw,req)


def test_probability_rounding_recorded():
    req={'questions':{'action':{'criteria':{'call':{},'fold':{}}}}};raw=answer(req)
    raw['answers']['action']['probabilities']={'call':.7,'fold':.2999}
    a=validate_answer(raw,req)['answers']['action']
    assert a['normalization_original_sum']==.9999 and sum(a['probabilities'].values())==pytest.approx(1)


def test_global_budget_reservation_and_uncertain_charge(tmp_path):
    s=Store(str(tmp_path/'s.db'));cid=s.reserve('r',.7,1,1)
    with pytest.raises(ValueError):s.reserve('r',.4,1,1)
    s.finish_call(cid,None,{'error':'timeout'})
    assert s.spend()==.7
    s.reserve('q',.2,1,1)
    assert s.spend()==pytest.approx(.9)


@pytest.mark.asyncio
async def test_default_thinking_idempotency_and_model_usage(tmp_path,monkeypatch):
    s=Store(str(tmp_path/'s.db'));p=Provider(Settings(_env_file=None,deepseek_api_key='test-only'),s)
    req={'model':'placeholder','state':{},'questions':{'action':{'type':'choice','criteria':{'call':{},'fold':{}}}}}
    sent=[]
    async def post(client,url,**kw):
        sent.append(kw['json'])
        return httpx.Response(200,json={'model':'actual-version','usage':{'prompt_tokens':100,'completion_tokens':100},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':{'action':{'call':.8,'fold':.2}}})}}]})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    kw={'decision_id':'unique','run_id':'r'};e=Entry(id='x',name='x')
    a=await p.call(e,req,**kw);b=await p.call(e,req,**kw)
    assert a==b and len(sent)==1 and a['model']=='actual-version'
    assert not {'temperature','thinking','reasoning_effort'} & set(sent[0])
    assert s.spend()>0
    changed=deepcopy(req);changed['state']={'different':True}
    with pytest.raises(ProviderError):await p.call(e,changed,**kw)


def test_api_admin_and_private_views(tmp_path):
    app=create_app(Settings(_env_file=None,pokerbench_data_dir=str(tmp_path),pokerbench_admin_token='local-test'))
    with TestClient(app) as c:
        assert c.post('/api/runs',json={}).status_code==401
        headers={'Authorization':'Bearer local-test'}
        assert c.post('/api/runs',json={},headers={**headers,'Origin':'https://untrusted.example'}).status_code==403
        r=c.post('/api/runs',json={},headers=headers).json();rid=r['id']
        assert 'seed' not in r and 'base_url' not in r['entries'][0]
        store=app.state.store;run=store.run(rid);cfg=RunConfig.model_validate(run['config'])
        h=Hand(run['seats'],run['button'],cfg.blinds(0),4);run['active_hand']=1;store.save_run(run,h.record())
        data=c.get(f'/api/runs/{rid}/hands/1?view=omniscient&hero='+h.actor).json()
        assert 'spec' not in data
        assert all(s['cards']==['??','??'] for s in data['events'][0]['snapshot']['seats'])
        assert c.get(f'/api/runs/{rid}/export').status_code==401
        assert c.get(f'/api/runs/{rid}/export',headers=headers).status_code==409
        assert c.post('/api/advisor/preview',json=advisor().model_dump(),headers=headers).status_code==200


def test_jev_two_decimal_rounding_only_with_recorded_normalization():
    req={'questions':{'action':{'criteria':{'call':{},'fold':{},'raise':{}}}}}
    raw={'answers':{'action':{'type':'choice','choice':'call','probabilities':{'call':.7,'fold':.2,'raise':.09},'confidence':.5}}}
    assert sum(validate_answer(deepcopy(raw),req)["answers"]["action"]["probabilities"].values())==pytest.approx(1)
    a=validate_answer(raw,req,quantized=True)['answers']['action']
    assert a['normalization_original_sum']==pytest.approx(.99)
    assert sum(a['probabilities'].values())==pytest.approx(1)

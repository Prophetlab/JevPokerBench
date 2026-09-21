import asyncio
from contextlib import AsyncExitStack

import httpx
import pytest

from pokerbench.config import Entry, RunConfig, Settings
from pokerbench.priority import PriorityPool
from pokerbench.provider import Provider
from pokerbench.runner import Runner
from pokerbench.store import Store
from test_accounts import register
from test_public_release import public_client, model_http
from test_rooms import OfflineModels, wait_state


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['systemone','jev'])
async def test_two_formal_calls_dispatch_while_all_visitor_slots_are_occupied(tmp_path,monkeypatch,kind):
    provider=Provider(Settings(_env_file=None),Store(str(tmp_path/'slots.db')))
    entry=Entry(id='model',name='Model',provider=kind,key_env='',base_url='https://model.example',
                input_cny_per_million=0,output_cny_per_million=0)
    monkeypatch.setattr(provider,'available',lambda e:True)
    request={'state':{},'questions':{'action':{'type':'choice','criteria':{'fold':{},'call':{}}}}}
    sent=[]
    async def post(client,url,**kwargs):
        sent.append(kwargs['json']['state']['source'])
        return httpx.Response(200,json={'answers':{'action':{'type':'choice','choice':'call',
            'probabilities':{'fold':0,'call':1},'confidence':1}}})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    async with AsyncExitStack() as leases:
        for _ in range(126):await leases.enter_async_context(provider.capacity(entry))
        waiting=asyncio.create_task(provider.call(entry,{**request,'state':{'source':'visitor'}},
            decision_id='visitor',run_id='visitor'))
        await asyncio.sleep(0)
        assert not waiting.done() and not sent
        try:
            await asyncio.wait_for(asyncio.gather(*(provider.call(entry,{**request,'state':{'source':'formal'}},
                decision_id=f'formal-{i}',run_id='formal',benchmark=True) for i in range(2))),2)
            assert sent==['formal','formal'] and not waiting.done()
        finally:
            waiting.cancel()
            await asyncio.gather(waiting,return_exceptions=True)
    assert provider.local_pool.active==provider.jev_pool.active==0
    provider.store.db.close()


@pytest.mark.asyncio
async def test_waiting_formal_request_overtakes_earlier_visitor_and_cancellation_frees_slots():
    pool=PriorityPool(3,1)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(pool.slot())
        release=pool.slot();await release.__aenter__()
        formal=pool.slot(priority=True);await formal.__aenter__()
        order=[];hold=asyncio.Event()
        async def enter(name,priority):
            async with pool.slot(priority=priority):
                order.append(name);await hold.wait()
        visitor=asyncio.create_task(enter('visitor',False))
        bench=asyncio.create_task(enter('benchmark',True))
        await asyncio.sleep(0)
        await release.__aexit__(None,None,None)
        await asyncio.sleep(0)
        assert order==['benchmark']
        bench.cancel();await asyncio.gather(bench,return_exceptions=True)
        await asyncio.sleep(0)
        assert order==['benchmark','visitor']
        visitor.cancel();await asyncio.gather(visitor,return_exceptions=True)
        await formal.__aexit__(None,None,None)
    assert pool.active==pool.regular==pool.waiting_priority==0
    async with pool.slot():pass


@pytest.mark.asyncio
async def test_cancelled_waiting_benchmark_does_not_block_visitors():
    pool=PriorityPool(2,1)
    hold=pool.slot(priority=True);await hold.__aenter__()
    second=pool.slot(priority=True);await second.__aenter__()
    async def enter():
        async with pool.slot(priority=True):pass
    pending=asyncio.create_task(enter());await asyncio.sleep(0)
    assert pool.waiting_priority==1
    pending.cancel();await asyncio.gather(pending,return_exceptions=True)
    assert pool.waiting_priority==0
    await second.__aexit__(None,None,None)
    async with pool.slot():assert pool.active==2
    await hold.__aexit__(None,None,None)
    assert pool.active==0


@pytest.mark.asyncio
async def test_runner_marks_only_formal_matches_as_priority(tmp_path):
    class Models(OfflineModels):
        flags=[]
        async def call(self,*args,**kwargs):
            self.flags.append(kwargs['benchmark'])
            return await super().call(*args,**kwargs)
    store=Store(str(tmp_path/'runner.db'));models=Models();runner=Runner(store,models)
    entries=[Entry(id=f'p{i}',name=f'Player {i}') for i in range(2)]
    run=runner.create(RunConfig(entries=entries,max_hands=1))
    await runner.play(run['id'])
    assert store.run(run['id'])['hands_played']==1
    assert models.flags and all(models.flags)
    store.db.close()


def test_web_room_cannot_promote_itself_to_benchmark(public_client,model_http,monkeypatch):
    c,app,_=public_client;register(c)
    result=c.post('/api/rooms',json={'model_ids':['bot'],'benchmark':True,'priority':'benchmark'})
    assert result.status_code==200
    rid=result.json()['run']['id'];run=app.state.store.run(rid)
    assert not {'benchmark','priority'}&run['config'].keys()
    flags=[];original=app.state.provider.call
    async def observed(*args,**kwargs):
        flags.append(kwargs.get('benchmark',False));return await original(*args,**kwargs)
    monkeypatch.setattr(app.state.provider,'call',observed)
    run['seats'].sort(key=lambda s:s['id']!='human-player');run['button']=0;app.state.store.save_run(run)
    c.post(f'/api/runs/{rid}/start');wait_state(c,rid,{})
    c.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'call'})
    wait_state(c,rid,{})
    assert flags and not any(flags)


def test_spectators_share_rendering_but_run_updates_are_immediately_visible(public_client,monkeypatch):
    import pokerbench.api as api
    c,app,registry=public_client
    run=app.state.runner.create(RunConfig(entries=registry[:2]))
    renders=[];original=api.public_run
    def observed(*args,**kwargs):
        renders.append(args[0]['id']);return original(*args,**kwargs)
    monkeypatch.setattr(api,'public_run',observed)
    for _ in range(20):
        assert c.get('/api/runs').status_code==200
        assert c.get(f'/api/runs/{run["id"]}').status_code==200
    assert renders==[run['id']]
    run['message']='Updated match';app.state.store.save_run(run)
    assert c.get(f'/api/runs/{run["id"]}').json()['message']=='Updated match'
    assert renders==[run['id'],run['id']]

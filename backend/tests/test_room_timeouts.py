"""Exercise actual hand settlement, never wall-clock model deadlines or APIs."""
from collections import Counter

import pytest

from pokerbench.config import Entry
from pokerbench.engine import Hand
from pokerbench.provider import ProviderError
from pokerbench.rooms import RoomInput, create_room
from pokerbench.runner import Runner
from pokerbench.store import Store
from test_rooms import OfflineModels


class ScriptedModels(OfflineModels):
    def __init__(self,fail):
        super().__init__();self.fail=fail;self.counts=Counter()
    async def call(self,entry,request,**kwargs):
        self.counts[entry.id]+=1
        error=self.fail(entry,self.counts[entry.id])
        if error:raise error
        return await super().call(entry,request,**kwargs)


def setup_room(tmp_path,provider,models,mode='cash'):
    runner=Runner(Store(str(tmp_path/'timeouts.db')),provider)
    registry=[Entry(id=f'model-{i}',name=f'Model {i}',provider=kind,key_env='') for i,kind in enumerate(models)]
    run,_=create_room(runner,RoomInput(model_ids=[e.id for e in registry],max_hands=20,mode=mode),registry)
    run['seats'].sort(key=lambda s:s['id']);run['button']=0
    run['config']['shuffle_each_orbit']=False
    runner.store.save_run(run)
    return runner,run['id']


async def complete_hand(runner,rid,observe=None):
    target=runner.store.run(rid)['hands_played']+1
    runner.start(rid);await runner.tasks[rid]
    for _ in range(80):
        run=runner.store.run(rid)
        if observe:observe(run)
        if run['hands_played']==target:
            assert runner.store.hand(rid,target)['complete']
            return run
        assert run['status']=='waiting_human',run['message']
        hand=Hand.restore(runner.store.hand(rid,run['active_hand']))
        choices={a.id for a in hand.legal()}
        runner.human_action(rid,hand.number,len(hand.actions),'check' if 'check' in choices else 'call')
        await runner.tasks[rid]
    pytest.fail('Offline hand did not settle')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['cash','sng'])
async def test_deepseek_third_timeout_stops_calls_but_retires_only_after_settlement(tmp_path,mode):
    provider=ScriptedModels(lambda entry,n:TimeoutError())
    runner,rid=setup_room(tmp_path,provider,['deepseek'],mode)
    before=runner.store.run(rid);initial=sum(s['stack']+s['reserve'] for s in before['seats'])
    observed_third=[]
    def observe(run):
        if run['hands_played']==0 and provider.counts['opponent-1']==3:
            assert run['active_hand']==1 and run['status']=='waiting_human'
            assert not any(s.get('retired') for s in run['seats'])
            observed_third.append(True)
    run=await complete_hand(runner,rid,observe)
    assert observed_third and provider.counts['opponent-1']==3
    assert len([e for e in run['events'] if e['type']=='model_timeout'])==3
    seat=next(s for s in run['seats'] if s['id']=='opponent-1')
    assert seat['retired_reason']=='timeout_limit' and seat['stack']==0
    assert run['status']=='complete'
    assert sum(s['stack']+s['reserve'] for s in run['seats'])==initial
    assert all(a['id'] in ('check','fold') for a in runner.store.hand(rid,1)['actions'] if a['player']=='opponent-1')


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_kind',['systemone','jev'])
async def test_other_models_keep_playing_after_three_timeouts_until_host_kick(tmp_path,provider_kind):
    provider=ScriptedModels(lambda entry,n:TimeoutError())
    runner,rid=setup_room(tmp_path,provider,[provider_kind])
    run=await complete_hand(runner,rid)
    assert provider.counts['opponent-1']>=4
    assert not any(s.get('retired') for s in run['seats'])
    assert run['status']=='waiting_next_hand'
    runner.kick(rid,'opponent-1')
    after=runner.store.run(rid)
    assert next(s for s in after['seats'] if s['id']=='opponent-1')['retired_reason']=='host_kick'
    assert after['status']=='complete'


@pytest.mark.asyncio
async def test_deepseek_timeouts_are_cumulative_across_hands_successes_and_restart(tmp_path):
    provider=ScriptedModels(lambda entry,n:TimeoutError() if n in (1,5,6) else None)
    runner,rid=setup_room(tmp_path,provider,['deepseek'])
    first=await complete_hand(runner,rid)
    assert provider.counts['opponent-1']==4
    assert sum(e['type']=='model_timeout' for e in first['events'])==1
    assert not any(s.get('retired') for s in first['seats'])
    runner=Runner(runner.store,provider)
    second=await complete_hand(runner,rid)
    assert sum(e['type']=='model_timeout' for e in second['events'])==2
    assert not any(s.get('retired') for s in second['seats'])
    third=await complete_hand(runner,rid)
    assert provider.counts['opponent-1']==6
    assert sum(e['type']=='model_timeout' for e in third['events'])==3
    assert next(s for s in third['seats'] if s['id']=='opponent-1')['retired_reason']=='timeout_limit'


@pytest.mark.asyncio
async def test_duplicate_deepseek_timeout_counts_are_per_seat(tmp_path):
    provider=ScriptedModels(lambda entry,n:TimeoutError() if entry.id=='opponent-1' else None)
    runner,rid=setup_room(tmp_path,provider,['deepseek','deepseek'])
    for _ in range(5):
        run=await complete_hand(runner,rid)
        target=next(s for s in run['seats'] if s['id']=='opponent-1')
        if target.get('retired'):break
    else:pytest.fail('DeepSeek did not reach its per-seat timeout limit')
    assert target['retired_reason']=='timeout_limit'
    assert provider.counts['opponent-1']==3
    assert not next(s for s in run['seats'] if s['id']=='opponent-2').get('retired')
    calls=provider.counts['opponent-2']
    run=await complete_hand(runner,rid)
    assert provider.counts['opponent-1']==3 and provider.counts['opponent-2']>calls
    assert not next(s for s in run['seats'] if s['id']=='opponent-2').get('retired')


@pytest.mark.asyncio
async def test_deepseek_provider_errors_do_not_count_as_timeouts(tmp_path):
    provider=ScriptedModels(lambda entry,n:ProviderError('offline invalid response'))
    runner,rid=setup_room(tmp_path,provider,['deepseek'])
    run=await complete_hand(runner,rid)
    assert provider.counts['opponent-1']>=4
    assert sum(e['type']=='model_error' for e in run['events'])>=4
    assert not any(e['type']=='model_timeout' for e in run['events'])
    assert not any(s.get('retired') for s in run['seats'])

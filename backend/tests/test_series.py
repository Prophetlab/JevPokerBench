import asyncio
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from pokerbench.config import Entry, RunConfig, Settings
from pokerbench.runner import Runner
from pokerbench.series import SeriesRunner, public_series
from pokerbench.store import Store


class TournamentRunner(Runner):
    """Deterministic completed tournaments, with no provider calls."""
    async def play(self, run_id):
        await asyncio.sleep(0)
        run=self.store.run(run_id)
        run['status']='complete'
        run['champion']=run['seats'][0]['id']
        run['hands_played']=1
        for index,seat in enumerate(run['seats']):
            seat['place']=index+1
        self.store.save_run(run)


@pytest.fixture
def series(tmp_path):
    runner=TournamentRunner(Store(str(tmp_path/'series.sqlite')),None)
    return SeriesRunner(runner)


def make(series,target=50):
    cfg=RunConfig(mode='sng',max_hands=10000,
                  entries=[Entry(id='a',name='A'),Entry(id='b',name='B')])
    run=series.runner.create(cfg)
    result=series.create(run['id'],target)
    return result,series.store.run(run['id'])


@pytest.mark.asyncio
async def test_fifty_tournaments_once_and_frozen_config(series):
    s,first=make(series)
    assert series.create(first['id'],50)==s
    with pytest.raises(ValueError):series.create(first['id'],51)
    series.start(s['id']);task=series.tasks[s['id']]
    series.start(s['id']);assert series.tasks[s['id']] is task
    await task
    result=series.store.series(s['id'])
    assert result['status']=='complete' and len(result['run_ids'])==50
    assert result['run_ids'][0]==first['id']
    runs=[series.store.run(rid) for rid in result['run_ids']]
    assert len({r['config']['seed'] for r in runs})==50
    for index,run in enumerate(runs):
        assert run['series_number']==index+1
        assert run['series_id']==s['id']
        assert all(v==first['config'][k] for k,v in run['config'].items() if k not in ('name','seed'))
        assert run['llm_adapter']==first['llm_adapter']
    report=public_series(result,series.store)
    assert report['completed']==50
    assert sum(row['wins'] for row in report['standings'])==50
    with pytest.raises(ValueError):series.start(s['id'])
    assert len(series.store.runs())==50


@pytest.mark.asyncio
async def test_completed_first_tournament_is_not_replayed(series):
    s,run=make(series,2)
    await series.runner.play(run['id'])
    before=deepcopy(series.store.run(run['id']))
    series.start(s['id']);await series.tasks[s['id']]
    assert series.store.run(run['id'])==before
    assert public_series(series.store.series(s['id']),series.store)['completed']==2


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['error','hand_limit','paused'])
async def test_incomplete_tournament_stops_without_spawning_and_can_resume(series,status):
    s,run=make(series,2)
    normal=series.runner.play
    async def fail(rid):
        r=series.store.run(rid);r['status']=status;r['message']='budget or provider failure'
        series.store.save_run(r)
    series.runner.play=fail
    series.start(s['id']);await series.tasks[s['id']]
    result=series.store.series(s['id'])
    assert result['status']=='paused' and result['run_ids']==[run['id']]
    assert result['message']=='budget or provider failure'
    assert public_series(result,series.store)['completed']==0
    series.runner.play=normal
    series.start(s['id']);await series.tasks[s['id']]
    assert len(series.store.series(s['id'])['run_ids'])==2


@pytest.mark.asyncio
async def test_pause_during_paid_work_does_not_start_next(series):
    s,run=make(series,2)
    started=asyncio.Event();release=asyncio.Event();normal=series.runner.play
    async def waiting(rid):
        started.set();await release.wait();await normal(rid)
    series.runner.play=waiting
    series.start(s['id']);await started.wait()
    series.pause(s['id'])
    with pytest.raises(ValueError,match='正在暂停'):series.start(s['id'])
    release.set();await series.tasks[s['id']]
    result=series.store.series(s['id'])
    assert result['status']=='paused' and len(result['run_ids'])==1
    series.runner.play=normal
    series.start(s['id']);await series.tasks[s['id']]
    assert series.store.series(s['id'])['status']=='complete'


def test_restart_never_autostarts_and_ready_membership_survives(series):
    s,run=make(series,2)
    s['status']='running';run['status']='running'
    series.store.save_series(s,run)
    new=SeriesRunner(TournamentRunner(series.store,None))
    assert not new.tasks and not new.runner.tasks
    assert new.store.series(s['id'])['status']=='paused'
    assert new.store.run(run['id'])['status']=='paused'
    assert new.store.run(run['id'])['series_number']==1


def test_only_finished_ranks_and_ties_count(series):
    s,run=make(series,2)
    report=public_series(s,series.store)
    assert all(r['rank'] is None and r['mean_place'] is None for r in report['standings'])
    run['status']='complete';run['champion']='a'
    for seat in run['seats']:seat['place']=2
    series.store.save_run(run)
    report=public_series(s,series.store)
    assert report['completed']==1 and [r['rank'] for r in report['standings']]==[1,1]
    run['seats'][0]['place']=None;series.store.save_run(run)
    assert public_series(s,series.store)['completed']==0


def test_cannot_attach_cash_or_running_sng(series):
    cash=series.runner.create(RunConfig())
    with pytest.raises(ValueError):series.create(cash['id'])
    run=series.runner.create(RunConfig(mode='sng'));run['status']='running';series.store.save_run(run)
    with pytest.raises(ValueError):series.create(run['id'])
    assert not series.store.all_series()


def test_series_api_and_member_controls(tmp_path):
    from pokerbench.api import create_app
    app=create_app(Settings(_env_file=None,pokerbench_data_dir=str(tmp_path)))
    with TestClient(app) as client:
        cfg=RunConfig(mode='sng',max_hands=1).model_dump()
        run=client.post('/api/runs',json=cfg).json()
        result=client.post('/api/series',json={'run_id':run['id'],'target':50})
        assert result.status_code==200
        sid=result.json()['id']
        assert client.get('/api/series').json()[0]['target']==50
        assert client.get(f"/api/runs/{run['id']}").json()['series_id']==sid
        assert client.post('/api/series',json={'run_id':run['id'],'target':0}).status_code==422
        # Starting/pausing the member must delegate to the series, not charge independently.
        calls=[]
        app.state.series_runner.start=lambda x:calls.append(('start',x))
        app.state.series_runner.pause=lambda x:calls.append(('pause',x))
        client.post(f"/api/runs/{run['id']}/start")
        client.post(f"/api/runs/{run['id']}/pause")
        assert calls==[('start',sid),('pause',sid)]


@pytest.mark.asyncio
async def test_global_budget_remains_shared_across_tournaments(series):
    s,run=make(series,50)
    normal=series.runner.play
    async def budgeted(rid):
        try:
            call=series.store.reserve(rid,1,global_budget=2,run_budget=2)
            series.store.finish_call(call,1,{})
        except ValueError as exc:
            r=series.store.run(rid);r['status']='error';r['message']=str(exc)
            series.store.save_run(r)
            return
        await normal(rid)
    series.runner.play=budgeted
    series.start(s['id']);await series.tasks[s['id']]
    result=series.store.series(s['id']);report=public_series(result,series.store)
    assert result['status']=='paused' and '预算不足' in result['message']
    assert report['completed']==2 and len(result['run_ids'])==3
    assert series.store.spend()==2
    # Retrying cannot skip the failed member or reset the global budget.
    series.start(s['id']);await series.tasks[s['id']]
    assert series.store.series(s['id'])['run_ids']==result['run_ids']
    assert series.store.spend()==2


@pytest.mark.asyncio
async def test_real_engine_series_resets_stacks_and_saves_replays(tmp_path):
    class AllInProvider:
        async def call(self,entry,request,**kwargs):
            choices=request['questions']['action']['criteria']
            raises=[c for c in choices if c.startswith('raise_to_')]
            action=max(raises,key=lambda c:int(c.rsplit('_',1)[-1])) if raises else 'call' if 'call' in choices else 'check'
            return {'answers':{'action':{'choice':action}}}
    store=Store(str(tmp_path/'real-series.sqlite'))
    runner=Runner(store,AllInProvider());series=SeriesRunner(runner)
    first=runner.create(RunConfig(mode='sng',max_hands=30,sng_starting_stack=200,
                        entries=[Entry(id='a',name='A'),Entry(id='b',name='B')]))
    s=series.create(first['id'],2);series.start(s['id']);await series.tasks[s['id']]
    report=public_series(store.series(s['id']),store)
    assert report['completed']==2 and report['status']=='complete'
    for rid in report['run_ids']:
        run=store.run(rid)
        assert set(run['history'][0]['values'].values())=={200}
        assert sorted(s['place'] for s in run['seats'])==[1,2]
        assert store.hands(rid) and all(h['complete'] for h in store.hands(rid))


def test_series_membership_commit_is_atomic(series):
    s,run=make(series)
    changed=deepcopy(s);changed['run_ids'].append('missing')
    with pytest.raises(KeyError):series.store.save_series(changed,{})
    assert series.store.series(s['id'])['run_ids']==[run['id']]
    with pytest.raises(KeyError):series.store.run('missing')

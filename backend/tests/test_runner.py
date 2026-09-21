from copy import deepcopy
import pytest
from pokerbench.config import Entry,RunConfig,Settings,BLINDS
from pokerbench.runner import Runner,rankings
from pokerbench.store import Store
from pokerbench.provider import Provider
from pokerbench.engine import Hand

@pytest.fixture
def runner(tmp_path):
    s=Store(str(tmp_path/'test.sqlite'))
    return Runner(s,Provider(Settings(_env_file=None),s))


def test_every_dealer_once_then_shuffle(runner):
    c=RunConfig();r=runner.create(c);before=[s['id'] for s in r['seats']];seen=[]
    for i in range(10):
        seen.append(r['seats'][r['button']]['id']);r['hands_played']+=1;runner.next_button(r,c)
        if i<9:assert r['orbit']==1
    assert len(set(seen))==10 and r['orbit']==2
    assert [s['id'] for s in r['seats']]!=before
    assert set(r['pending_dealers'])==set(before)


def test_elimination_mid_orbit(runner):
    c=RunConfig(mode='sng');r=runner.create(c)
    eliminated=r['seats'][5]['id'];r['seats'][5]['stack']=0
    seen=[]
    for _ in range(9):
        seen.append(r['seats'][r['button']]['id']);runner.next_button(r,c)
    assert len(set(seen))==9 and eliminated not in seen and r['orbit']==2


def test_cash_settlement_retires_short_bankroll_and_preserves_assets(runner):
    c=RunConfig(bankroll=40000,buy_in=20000,settle_every=500);r=runner.create(c)
    r['hands_played']=500;r['seats'][-1]['reserve']=0;r['seats'][-1]['stack']=500
    before={s['id']:s['stack']+s['reserve'] for s in r['seats']}
    assert runner.prepare(r,c)
    assert all(s['stack']==20000 for s in r['seats'][:-1])
    assert r['seats'][-1]['retired'] and r['seats'][-1]['reserve']==500
    assert r['seats'][-1]['stack']==0
    assert {s['id']:s['stack']+s['reserve'] for s in r['seats']}==before


def test_cash_bankrupt_dealer_exits_without_blocking_others(runner):
    c=RunConfig(bankroll=20000,buy_in=20000);r=runner.create(c)
    loser=r['seats'][r['button']];winner=r['seats'][1]
    winner['stack']+=loser['stack'];loser['stack']=0
    before=runner.point(r)
    assert runner.prepare(r,c)
    assert loser['retired'] and loser['reserve']==0 and loser['rebuys']==0
    assert runner.point(r)==before
    assert r['seats'][r['button']]['id']!=loser['id']
    assert loser['id'] not in r['pending_dealers']
    hand=Hand(r['seats'],r['button'],c.blinds(0),c.seed)
    assert loser['id'] not in hand.ids and len(hand.ids)==9
    seen=[]
    for _ in range(9):
        seen.append(r['seats'][r['button']]['id']);runner.next_button(r,c)
    assert len(set(seen))==9 and loser['id'] not in seen
    assert sum(s['stack']+s['reserve'] for s in r['seats'])==sum(s['initial'] for s in r['seats'])
    assert runner.prepare(r,c)
    assert len([e for e in r['events'] if e['type']=='cash_exit'])==1


def test_cash_short_stack_can_play_until_next_required_buy_in(runner):
    c=RunConfig(bankroll=20000);r=runner.create(c)
    r['seats'][0]['stack']=50
    assert runner.prepare(r,c)
    assert r['seats'][0]['stack']==50 and not r['seats'][0].get('retired')


def test_cash_ends_when_only_one_funded_player_remains(runner):
    c=RunConfig(bankroll=20000);r=runner.create(c)
    for s in r['seats'][1:]:s['stack']=0
    r['seats'][0]['stack']=200000
    assert not runner.prepare(r,c)
    assert r['status']=='complete' and r['champion']==r['seats'][0]['id']
    assert sum(not s.get('retired',False) for s in r['seats'])==1



def test_rebuy_and_cash_rank_uses_total_assets(runner):
    c=RunConfig();r=runner.create(c)
    r['seats'][0]['stack']=0;r['seats'][1]['stack']+=20000
    runner.prepare(r,c)
    assert r['seats'][0]['rebuys']==1
    rows={s['id']:s for s in rankings(r)}
    assert rows[r['seats'][0]['id']]['profit']==-20000
    assert rows[r['seats'][1]['id']]['profit']==20000


def test_blind_schedule_holds_last_level():
    c=RunConfig(mode='sng')
    assert c.blinds(199)==BLINDS[0] and c.blinds(200)==BLINDS[1]
    assert c.blinds(100000)==BLINDS[-1]


class OfflineProvider:
    async def call(self,entry,request,**kwargs):
        choices=request['questions']['action']['criteria'];a='check' if 'check' in choices else 'fold' if 'fold' in choices else 'call'
        return {'answers':{'action':{'choice':a}}}

@pytest.mark.asyncio
async def test_sng_hand_limit_not_a_champion_and_resume(runner):
    runner.provider=OfflineProvider();c=RunConfig(mode='sng',max_hands=1);r=runner.create(c)
    runner.start(r['id']);await runner.tasks[r['id']]
    r=runner.store.run(r['id']);assert r['status']=='hand_limit' and r['champion'] is None
    runner.update_limit(r['id'],2);runner.start(r['id']);await runner.tasks[r['id']]
    r=runner.store.run(r['id']);assert r['hands_played']==2
    assert len(runner.store.hands(r['id']))==2


def test_restart_does_not_autorun_paid_work(runner):
    r=runner.create(RunConfig());r['status']='running';runner.store.save_run(r)
    new=Runner(runner.store,runner.provider)
    assert new.store.run(r['id'])['status']=='paused' and not new.tasks


def test_legacy_match_cannot_mix_new_adapter_decisions(runner):
    r=runner.create(RunConfig());r.pop('llm_adapter');r['hands_played']=1
    runner.store.save_run(r)
    with pytest.raises(ValueError,match='旧版接入'):runner.start(r['id'])
    assert not runner.tasks and runner.store.run(r['id'])['hands_played']==1


def test_sng_simultaneous_elimination_tied_by_start_stack(runner):
    # Deterministic real PokerKit games until both equal stacks lose together.
    entries=[Entry(id=f'p{i}',name=f'p{i}') for i in range(3)]
    cfg=RunConfig(mode='sng',entries=entries,sng_starting_stack=300)
    for seed in range(30):
        run=runner.create(cfg);h=Hand(run['seats'],run['button'],(50,100,0),seed,mode='sng')
        h.apply('raise_to_300');h.apply('call');h.apply('call')
        if sum(s>0 for s in h.state.stacks)==1:
            runner.finish_hand(run,h,cfg)
            assert sorted(s['place'] for s in run['seats'])==[1,2,2]
            assert run['status']=='complete' and run['champion']
            assert abs(sum(run['history'][-1]['ev_values'].values())-900)<.001
            break
    else:pytest.fail('Fixture failed to generate an outright winner')


def test_cash_500_hand_boundary_preserves_each_players_profit(runner):
    cfg=RunConfig(settle_every=500);run=runner.create(cfg)
    run['seats'][0]['stack']+=15000;run['seats'][1]['stack']-=15000
    before={s['id']:s['stack']+s['reserve'] for s in run['seats']}
    run['hands_played']=499
    assert runner.prepare(run,cfg)
    assert run['seats'][0]['stack']==35000
    assert not any(e['type']=='cashout' for e in run['events'])
    assert run['chip_rule_start_hand']==500
    run['hands_played']=500
    assert runner.prepare(run,cfg)
    assert all(s['stack']==cfg.buy_in for s in run['seats'])
    assert {s['id']:s['stack']+s['reserve'] for s in run['seats']}==before
    assert len([e for e in run['events'] if e['type']=='cashout' and e['hand']==500])==10
    assert all(s['rebuys']==0 for s in run['seats'])
    run['hands_played']=501
    assert runner.prepare(run,cfg)
    assert len([e for e in run['events'] if e['type']=='cashout'])==10
    assert len([e for e in run['events'] if e['type']=='chip_unit'])==1


@pytest.mark.asyncio
async def test_cash_resume_hand501_does_not_cash_out_twice(runner):
    cfg=RunConfig(max_hands=501);run=runner.create(cfg)
    run['hands_played']=500
    runner.prepare(run,cfg)
    hand=Hand(run['seats'],run['button'],cfg.blinds(500),cfg.seed+104729*501,501,'cash')
    run['active_hand']=501
    runner.store.save_run(run,hand.record())
    runner.provider=OfflineProvider()
    runner.start(run['id']);await runner.tasks[run['id']]
    result=runner.store.run(run['id'])
    assert result['status']=='complete' and result['hands_played']==501
    assert len([e for e in result['events'] if e['type']=='cashout'])==10
    assert all(s['bought']==2*cfg.buy_in for s in result['seats'])


@pytest.mark.asyncio
async def test_cash_continues_after_retirement_with_real_engine(runner):
    cfg=RunConfig(bankroll=20000,max_hands=3);run=runner.create(cfg)
    loser=run['seats'][run['button']];run['seats'][1]['stack']+=loser['stack'];loser['stack']=0
    runner.store.save_run(run);runner.provider=OfflineProvider()
    runner.start(run['id']);await runner.tasks[run['id']]
    result=runner.store.run(run['id'])
    assert result['hands_played']==3 and result['status']=='complete'
    for hand in runner.store.hands(run['id']):
        assert all(a['player']!=loser['id'] for a in hand['actions'])
    assert next(s for s in result['seats'] if s['id']==loser['id'])['retired']


def test_cash_cannot_resume_overdraft_legacy_hand(runner):
    run=runner.create(RunConfig());run['seats'][0]['reserve']=-20000;run['active_hand']=1
    runner.store.save_run(run)
    with pytest.raises(ValueError,match='透支买入'):runner.start(run['id'])
    assert not runner.tasks


def test_formal_cash_chip_rule_moves_only_remainder_and_preserves_history(runner):
    cfg=RunConfig(max_hands=10);run=runner.create(cfg)
    run['hands_played']=7
    for index,seat in enumerate(run['seats']):
        remainder=index+1
        seat['stack']-=remainder;seat['reserve']+=remainder
    historic=Hand(run['seats'],run['button'],cfg.blinds(6),cfg.seed,number=7,chip_unit=1)
    while historic.state.status:historic.apply(historic.legal()[0].id)
    runner.store.save_run(run,historic.record())
    stored_before=runner.store.db.execute('SELECT body FROM hands WHERE run_id=?',(run['id'],)).fetchone()[0]
    history=deepcopy(run['history']);statistics=deepcopy(run['statistics'])
    seats=deepcopy(run['seats']);assets={s['id']:s['stack']+s['reserve'] for s in seats}
    assert runner.prepare(run,cfg)
    for before,after in zip(seats,run['seats']):
        remainder=before['stack']%50
        assert after['stack']==before['stack']-remainder
        assert after['reserve']==before['reserve']+remainder
        assert after['initial']==before['initial'] and after['bought']==before['bought']
    assert {s['id']:s['stack']+s['reserve'] for s in run['seats']}==assets
    assert run['chip_rule_start_hand']==8
    assert [e['hand'] for e in run['events'] if e['type']=='chip_unit']==[8]
    assert run['history']==history and run['statistics']==statistics
    once=deepcopy(run)
    assert runner.prepare(run,cfg) and run==once
    runner.store.save_run(run)
    stored_after=runner.store.db.execute('SELECT body FROM hands WHERE run_id=?',(run['id'],)).fetchone()[0]
    assert stored_after==stored_before


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_legacy_unit',[False,True])
async def test_active_legacy_hand_restores_unit_one_before_new_cash_hand_uses_fifty(runner,missing_legacy_unit):
    runner.provider=OfflineProvider()
    entries=[Entry(id=f'local-{i}',name=f'Local {i}',provider='systemone',key_env='') for i in range(2)]
    cfg=RunConfig(entries=entries,max_hands=2,shuffle_each_orbit=False)
    run=runner.create(cfg)
    run['seats'][0]['stack']-=25;run['seats'][0]['reserve']+=25
    hand=Hand(run['seats'],run['button'],cfg.blinds(0),cfg.seed,chip_unit=1)
    hand.apply('call')
    old=deepcopy(hand.record())
    if missing_legacy_unit:old['spec'].pop('chip_unit')
    assert Hand.restore(old).chip_unit==1
    run['active_hand']=1;run['status']='paused'
    runner.store.save_run(run,old)
    assets=sum(s['stack']+s['reserve'] for s in run['seats'])
    history=deepcopy(run['history'])
    runner.start(run['id']);await runner.tasks[run['id']]
    result=runner.store.run(run['id'])
    assert result['status']=='complete' and result['hands_played']==2
    first=runner.store.hand(run['id'],1);second=runner.store.hand(run['id'],2)
    assert first['spec']['chip_unit']==1 and second['spec']['chip_unit']==50
    assert first['actions'][:len(old['actions'])]==old['actions']
    assert first['events'][:len(old['events'])]==old['events']
    assert {k:v for k,v in first['spec'].items() if k!='chip_unit'}=={k:v for k,v in old['spec'].items() if k!='chip_unit'}
    assert all(s['stack']%50==0 for s in second['spec']['seats'])
    assert result['chip_rule_start_hand']==2
    assert [e['hand'] for e in result['events'] if e['type']=='chip_unit']==[2]
    assert result['history'][:len(history)]==history
    assert sum(s['stack']+s['reserve'] for s in result['seats'])==assets


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected_unit',[('cash',50),('sng',1)])
async def test_new_formal_hands_use_cash_chip_rule_only(runner,mode,expected_unit):
    runner.provider=OfflineProvider()
    run=runner.create(RunConfig(mode=mode,max_hands=1))
    runner.start(run['id']);await runner.tasks[run['id']]
    record=runner.store.hand(run['id'],1)
    assert record['complete'] and record['spec']['chip_unit']==expected_unit
    result=runner.store.run(run['id'])
    if mode=='cash':assert result['chip_rule_start_hand']==1
    else:assert 'chip_rule_start_hand' not in result


@pytest.mark.parametrize('stack,reset',[(199950,False),(200000,True),(200050,True)])
def test_cash_threshold_uses_completed_stack_not_reserve(runner,stack,reset):
    cfg=RunConfig(cash_reset_at=200000);run=runner.create(cfg)
    run['hands_played']=47
    seat=run['seats'][0];seat['reserve']-=stack-seat['stack'];seat['stack']=stack
    before=runner.point(run);history=deepcopy(run['history'])
    assert runner.prepare(run,cfg)
    assert bool([e for e in run['events'] if e['type']=='cash_reset'])==reset
    assert run['seats'][0]['stack']==(cfg.buy_in if reset else stack)
    assert runner.point(run)==before and run['history']==history
    assert run['cash_reset_start_hand']==48
    once=deepcopy(run)
    assert runner.prepare(run,cfg) and run==once


def test_threshold_replaces_500_hand_reset_and_never_revives_retired_seats(runner):
    cfg=RunConfig(cash_reset_at=200000);run=runner.create(cfg);run['hands_played']=500
    run['seats'][0]['stack']+=500;run['seats'][1]['stack']-=500
    assert runner.prepare(run,cfg)
    assert run['seats'][0]['stack']==20500
    assert not any(e['type']=='cashout' for e in run['events'])
    # A retired seat's reserve is deliberately sufficient: retirement is final.
    retired=run['seats'][-1];retired['retired']=True;retired['reserve']+=retired['stack'];retired['stack']=0
    poor=run['seats'][-2];poor['reserve']=0;poor['stack']=150
    winner=run['seats'][0];winner['reserve']-=200000-winner['stack'];winner['stack']=200000
    before={s['id']:s['stack']+s['reserve'] for s in run['seats']};history=deepcopy(run['history']);gone=deepcopy(retired)
    assert runner.prepare(run,cfg)
    assert retired==gone and retired['id'] not in run['pending_dealers']
    assert poor['retired'] and poor['stack']==0 and poor['reserve']==150
    assert all(s['stack']==cfg.buy_in for s in run['seats'] if not s.get('retired'))
    assert all(s['rebuys']==0 for s in run['seats'])
    assert {s['id']:s['stack']+s['reserve'] for s in run['seats']}==before
    assert run['history']==history
    assert len([e for e in run['events'] if e['type']=='cash_reset'])==1
    assert len([e for e in run['events'] if e['type']=='cash_reset_rule'])==1
    assert sum(e['type']=='cash_exit' and e['player']==poor['id'] for e in run['events'])==1


@pytest.mark.asyncio
async def test_threshold_cutover_preserves_active_hand_and_resets_only_next_hand(runner):
    cfg=RunConfig(max_hands=2);run=runner.create(cfg)
    seat=run['seats'][0];seat['reserve']-=200000;seat['stack']+=200000
    h=Hand(run['seats'],run['button'],cfg.blinds(0),cfg.seed+104729,1,'cash',chip_unit=50)
    h.apply('fold');old=deepcopy(h.record())
    run['active_hand']=1;run['config']['cash_reset_at']=200000
    runner.store.save_run(run,h.record());runner.provider=OfflineProvider()
    runner.start(run['id']);await runner.tasks[run['id']]
    result=runner.store.run(run['id']);first=runner.store.hand(run['id'],1);second=runner.store.hand(run['id'],2)
    assert result['status']=='complete' and result['hands_played']==2
    assert first['spec']==old['spec'] and first['actions'][:len(old['actions'])]==old['actions']
    assert first['events'][:len(old['events'])]==old['events']
    assert all(s['stack']==cfg.buy_in for s in second['spec']['seats'])
    assert result['cash_reset_start_hand']==2
    assert [e['hand'] for e in result['events'] if e['type']=='cash_reset']==[1]
    assert sum(s['stack']+s['reserve'] for s in result['seats'])==sum(s['initial'] for s in result['seats'])


@pytest.mark.asyncio
async def test_threshold_checkpoint_resume_does_not_repeat_buy_in(runner):
    cfg=RunConfig(max_hands=12,cash_reset_at=200000);run=runner.create(cfg);run['hands_played']=11
    seat=run['seats'][0];seat['reserve']-=180000;seat['stack']+=180000
    assert runner.prepare(run,cfg)
    hand=Hand(run['seats'],run['button'],cfg.blinds(11),cfg.seed+104729*12,12,'cash',chip_unit=50)
    run['active_hand']=12;run['status']='running';runner.store.save_run(run,hand.record())
    restored=Runner(runner.store,OfflineProvider());restored.start(run['id']);await restored.tasks[run['id']]
    result=runner.store.run(run['id'])
    assert result['hands_played']==12 and result['status']=='complete'
    assert [e['hand'] for e in result['events'] if e['type']=='cash_reset']==[11]
    assert all(s['bought']==2*cfg.buy_in for s in result['seats'])
    assert len([e for e in result['events'] if e['type']=='cash_reset_rule'])==1


def test_threshold_does_not_change_sng_or_legacy_policy(runner):
    cfg=RunConfig(mode='sng',cash_reset_at=200000);run=runner.create(cfg)
    run['seats'][0]['stack']=200000;before=deepcopy(run)
    assert runner.prepare(run,cfg) and run==before
    legacy=RunConfig.model_validate({})
    assert legacy.cash_reset_at is None and legacy.settle_every==500


@pytest.mark.parametrize('threshold',[0,19950,20000,20001,200025])
def test_invalid_cash_threshold_is_rejected(threshold):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):RunConfig(cash_reset_at=threshold)

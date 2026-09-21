from copy import deepcopy
import json
import random
import pytest
from pokerbench.engine import Hand, observer_record
from pokerbench.ev import all_in_adjustment


def seats(n=3, stacks=None):
    return [dict(id=f'p{i}',name=f'PRIVATE_MODEL_{i}',stack=(stacks or [2000]*n)[i]) for i in range(n)]


@pytest.mark.parametrize('n',[2,6,10])
@pytest.mark.parametrize('seed',range(15))
def test_conservation_replay_and_information_boundary(n,seed):
    h=Hand(seats(n),0,(50,100,100 if seed%2 else 0),seed,mode='sng' if seed%2 else 'cash')
    rng=random.Random(seed+300)
    for _ in range(250):
        if not h.state.status:break
        obs=h.observation()
        assert 'PRIVATE_MODEL' not in json.dumps(obs)
        assert 'seed' not in json.dumps(obs)
        for s in obs['seats']:
            if s['id']!=obs['actor'] and not any(c!='??' for c in s['public_cards']):
                assert s['cards']==['??','??']
        action=rng.choice(h.legal()).id
        h.apply(action)
        public=observer_record(h.record())
        if h.state.status:
            assert all(s['cards']==s['public_cards'] for e in public['events'] for s in e['snapshot']['seats'])
    assert not h.state.status
    assert sum(h.state.stacks)==2000*n
    assert Hand.restore(h.record()).record()==h.record()
    ev=all_in_adjustment(h.record(),samples=100)
    assert abs(sum(ev['adjustments'].values()))<.01


def test_heads_up_and_short_ante_blind_first():
    h=Hand(seats(2,[2000,120]),0,(50,100,100),4,mode='sng')
    byid={s['id']:s for s in h.snapshot()['seats']}
    assert h.actor=='p0'
    assert byid['p1']['committed']==120 and byid['p1']['bet']==100
    assert byid['p0']['committed']==50


def test_short_all_in_does_not_reopen_raise():
    h=Hand(seats(3,[2000,350,2000]),0,(50,100,0),5,manual=True,hero='p0',hole_cards=['As','Ah'])
    h.apply('raise_to_300')
    h.apply('raise_to_350')
    h.apply('call')
    assert h.actor=='p0'
    assert {a.id for a in h.legal()}=={'fold','call'}
    with pytest.raises(ValueError):h.apply('raise_to_600')


def test_side_pots_and_fold_eligibility():
    h=Hand(seats(3,[1000,300,600]),0,(50,100,0),39)
    h.apply('raise_to_1000');h.apply('call');h.apply('call')
    assert not h.state.status
    assert sum(h.state.stacks)==1900
    ev=all_in_adjustment(h.record(),samples=500)
    assert ev['covered']
    assert sorted(p['amount'] for p in ev['pots'])==[600,900]
    assert abs(sum(ev['adjustments'].values()))<.001
    for p in ev['pots']:
        assert sum(p['expected_payout'].values())==pytest.approx(p['amount'])
    h=Hand(seats(3),0,(50,100,0),6)
    h.apply('fold');h.apply('fold')
    s={s['id']:s for s in h.snapshot()['seats']}
    assert s['p0']['payoff']==0 and s['p1']['payoff']==-50 and s['p2']['payoff']==50
    assert not all_in_adjustment(h.record())['covered']


def test_flop_pot_raise_amounts():
    h=Hand(seats(2),0,(50,100,0),5)
    h.apply('call');h.apply('check')
    assert h.street=='flop' and h.state.total_pot_amount==200
    assert any(a.id=='raise_to_100' for a in h.legal())
    h.apply('raise_to_100')
    # P=300, C=100, b=0: half-pot raise-to 100+.5*400=300.
    assert any(a.id=='raise_to_300' for a in h.legal())


def test_ev_locked_board_and_exact_runouts():
    h=Hand(seats(2,[300,300]),0,(50,100,0),42)
    h.apply('call');h.apply('check')
    h.apply('check');h.apply('check')
    assert h.street=='turn'
    h.apply('raise_to_200');h.apply('call')
    ev=all_in_adjustment(h.record())
    assert ev['covered'] and ev['method']=='exact' and ev['samples']==44
    assert len(ev['board_at_lock'])==4
    assert abs(sum(ev['adjustments'].values()))<.001


def test_public_replay_does_not_expose_holes_before_showdown():
    h=Hand(seats(3),0,(50,100,0),2)
    while h.state.status:
        h.apply('check' if any(a.id=='check' for a in h.legal()) else 'call')
    r=observer_record(h.record())
    assert all(s['cards']==['??','??'] for s in r['events'][0]['snapshot']['seats'])
    omni=observer_record(h.record(),omniscient=True)
    assert all(s['cards']!=['??','??'] for s in omni['events'][0]['snapshot']['seats'])


def test_split_pot_with_odd_chip():
    # Folded SB contributes 1; board royal flush makes the remaining two tie.
    from pokerkit import Deck
    prefix=['2c','3c','4c','5c','6c','7c','8c','Ah','Kh','Qh','9c','Jh','Tc','Th']
    deck=prefix+[repr(c) for c in Deck.STANDARD if repr(c) not in prefix]
    h=Hand(seats(3,[50,50,50]),0,(1,2,0),0,deck=deck)
    h.apply('call');h.apply('fold');h.apply('check')
    while h.state.status:h.apply('check')
    assert h.snapshot()['board']==['Ah','Kh','Qh','Jh','Th']
    result={s['id']:s['stack'] for s in h.snapshot()['seats']}
    assert result['p1']==49 and sorted([result['p0'],result['p2']])==[50,51]
    assert sum(result.values())==150


@pytest.mark.parametrize('mode',['cash','sng'])
def test_shown_losing_all_in_cards_remain_public_at_settlement(mode):
    h=Hand(seats(3,[2000,350,2000]),0,(50,100,0),5,mode=mode)
    h.apply('raise_to_2000');h.apply('call');h.apply('fold')
    record=observer_record(h.record())
    # Both all-in contenders, including the loser killed by PokerKit, stay face-up.
    final={s['id']:s for s in record['events'][-1]['snapshot']['seats']}
    assert final['p0']['cards']==h.original_holes['p0']
    assert final['p1']['cards']==h.original_holes['p1']
    assert final['p2']['cards']==['??','??']
    assert all(s['cards']==['??','??'] for s in record['events'][0]['snapshot']['seats'])
    assert sum(s['stack'] for s in final.values())==4350
    for pid in ('p0','p1'):
        visible=False
        for event in record['events']:
            row=next(s for s in event['snapshot']['seats'] if s['id']==pid)
            if row['cards']!=['??','??']:visible=True
            if visible:assert row['cards']==h.original_holes[pid]


def test_uncontested_win_does_not_force_cards_face_up():
    h=Hand(seats(2),0,(50,100,0),7,mode='sng')
    h.apply('fold')
    assert all(s['cards']==['??','??'] for s in observer_record(h.record())['events'][-1]['snapshot']['seats'])


def test_historical_visibility_repair_preserves_actions_payouts_and_is_idempotent():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('backfill_public_cards',Path(__file__).resolve().parents[2]/'scripts/backfill_public_cards.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    h=Hand(seats(2),0,(50,100,0),8,mode='sng');h.apply('raise_to_2000');h.apply('call')
    old=h.record();original=deepcopy(old)
    for event in old['events']:
        if event['snapshot']['complete']:
            for row in event['snapshot']['seats']:
                if row['payoff']<0:row['public_cards']=['??','??']
    assert old!=original
    fixed=module.repair(old)
    assert fixed==original and module.repair(fixed)==fixed
    assert fixed['actions']==old['actions'] and fixed['spec']==old['spec']
    assert [s['stack'] for s in fixed['events'][-1]['snapshot']['seats']]==[s['stack'] for s in old['events'][-1]['snapshot']['seats']]

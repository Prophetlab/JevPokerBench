import asyncio
import json
import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pokerbench.config import Entry, RunConfig, Settings
from pokerbench.engine import Hand
from pokerbench.rooms import RoomInput, create_room, room_state
from pokerbench.runner import Runner
from pokerbench.store import Store


class OfflineModels:
    def __init__(self):self.calls=[]
    def available(self,entry):return True
    async def call(self,entry,request,**kwargs):
        assert entry.provider!='human'
        self.calls.append((entry.id,deepcopy(request)))
        choices=request['questions']['action']['criteria']
        choice='check' if 'check' in choices else 'call'
        return {'answers':{'action':{'choice':choice}}}


@pytest.fixture
def client(tmp_path,monkeypatch):
    import pokerbench.api as api_module
    config=tmp_path/'config';config.mkdir()
    entries=[Entry(id='bot',name='Local Model',provider='systemone',key_env='',proxy=False),
             Entry(id='deepseek',name='DeepSeek',expected_model='deepseek-flash'),
             Entry(id='luna',name='GPT-5.6 Luna'),Entry(id='alias',name='Expensive',expected_model='openai/gpt-5.6-luna')]
    (config/'entries.json').write_text(json.dumps([e.model_dump() for e in entries]))
    monkeypatch.setattr(api_module,'ROOT',tmp_path)
    app=api_module.create_app(Settings(_env_file=None,pokerbench_data_dir=str(tmp_path/'data'),deepseek_api_key='test-only',pokerbench_invite_code='test-invite'))
    provider=OfflineModels();app.state.runner.provider=provider
    with TestClient(app) as c:yield c,app,entries


def new_room(client,**kwargs):
    c,app,entries=client
    # Existing token-owned tables must keep working until explicitly claimed.
    run,token=create_room(app.state.runner,RoomInput(**{'model_ids':['bot'],'max_hands':3,**kwargs}),entries)
    rid=run['id'];headers={'X-Player-Token':token}
    run=app.state.store.run(rid)
    # Fixed heads-up seat order makes the human the initial actor.
    run['seats'].sort(key=lambda s:s['id']!='human-player');run['button']=0
    app.state.store.save_run(run)
    return rid,headers


def wait_state(c,rid,headers):
    for _ in range(100):
        data=c.get(f'/api/rooms/{rid}/state',headers=headers).json()
        if data['run']['status']!='running':return data
        time.sleep(.005)
    pytest.fail('Offline room did not stop at human turn/hand boundary')


def test_room_roster_excludes_gpt_and_freezes_deepseek_override(client):
    c,app,entries=client
    assert c.post('/api/auth/register',json={'id':'roster','password':'test-password'}).status_code==200
    assert [e['id'] for e in c.get('/api/rooms/models').json()]==['bot','deepseek']
    for ids in [['luna'],['alias'],['missing'],[],['bot']*10]:
        assert c.post('/api/rooms',json={'model_ids':ids}).status_code==422
    for thinking in ['low','non-thinking']:
        rid,headers=new_room(client,model_ids=['deepseek'],deepseek_thinking=thinking,mode='sng')
        cfg=app.state.store.run(rid)['config']
        assert cfg['entries'][1]['reasoning_effort']==thinking
        assert cfg['sng_starting_stack']==20000 and cfg['level_every']==200
        assert cfg['shuffle_each_orbit'] and len(cfg['blind_levels'])==15
        assert c.post('/api/series',json={'run_id':rid}).status_code==422
    assert next(e for e in c.get('/api/entries').json() if e['id']=='deepseek')['reasoning_effort'] is None


def test_private_seat_tokens_and_hole_cards(client):
    c,app,_=client;rid,headers=new_room(client)
    assert c.get(f'/api/rooms/{rid}/state').status_code==403
    assert c.post(f'/api/runs/{rid}/start').status_code==403
    assert c.post(f'/api/runs/{rid}/pause',headers={'X-Player-Token':'wrong'}).status_code==403
    assert c.get(f'/api/runs/{rid}').status_code==403
    public=c.get(f'/api/runs/{rid}',headers=headers).json()
    assert 'player_token_hash' not in public and 'player_token' not in public and 'seed' not in public
    assert public['human_player_id']=='human-player'
    assert c.post(f'/api/runs/{rid}/start',headers=headers).status_code==200
    data=wait_state(c,rid,headers);assert data['can_act']
    seats=data['record']['events'][-1]['snapshot']['seats']
    assert next(s for s in seats if s['id']=='human-player')['cards']!=['??','??']
    assert next(s for s in seats if s['id']=='opponent-1')['cards']==['??','??']
    assert c.get(f'/api/runs/{rid}/hands/1').status_code==403
    other=c.get(f'/api/runs/{rid}/hands/1?view=omniscient&hero=human-player',headers=headers).json()
    assert all(s['cards']==['??','??'] for s in other['events'][0]['snapshot']['seats'])
    assert 'spec' not in data['record'] and data['record']['analysis'] is None
    assert c.get(f'/api/runs/{rid}/export').status_code==403


def test_human_actions_reject_wrong_turn_stale_and_double_click(client):
    c,app,_=client;rid,headers=new_room(client)
    action={'hand_number':1,'action_count':0,'action_id':'call'}
    assert c.post(f'/api/rooms/{rid}/action',json=action,headers=headers).status_code==422
    c.post(f'/api/runs/{rid}/start',headers=headers);data=wait_state(c,rid,headers)
    for bad in [{**action,'action_id':'raise_to_1'},{**action,'hand_number':2},{**action,'action_count':999}]:
        assert c.post(f'/api/rooms/{rid}/action',json=bad,headers=headers).status_code==422
    assert c.post(f'/api/rooms/{rid}/action',json=action).status_code==403
    assert c.post(f'/api/rooms/{rid}/action',json=action,headers=headers).status_code==200
    data=wait_state(c,rid,headers)
    assert data['action_count']>0
    assert c.post(f'/api/rooms/{rid}/action',json=action,headers=headers).status_code==422
    assert all(pid!='human-player' for pid,_ in app.state.runner.provider.calls)
    for _,request in app.state.runner.provider.calls:
        for seat in request['state']['seats']:
            if seat['id']!=request['state']['actor']:assert seat['cards']==seat['public_cards']


@pytest.mark.parametrize('mode',['cash','sng'])
def test_hand_boundaries_pause_and_last_hand_limit(client,mode):
    c,app,_=client;rid,headers=new_room(client,mode=mode,max_hands=1)
    c.post(f'/api/runs/{rid}/start',headers=headers);data=wait_state(c,rid,headers)
    c.post(f'/api/runs/{rid}/pause',headers=headers)
    assert c.get(f'/api/rooms/{rid}/state',headers=headers).json()['run']['status']=='paused'
    c.post(f'/api/runs/{rid}/start',headers=headers);data=wait_state(c,rid,headers)
    assert c.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'fold'},headers=headers).status_code==200
    data=wait_state(c,rid,headers)
    assert data['run']['hands_played']==1
    assert data['run']['status']==('complete' if mode=='cash' else 'hand_limit')
    # Even completed personal hands cannot expose folded opponents via the benchmark routes.
    exported=c.get(f'/api/runs/{rid}/hands/1?view=omniscient&hero=bot',headers=headers).json()
    assert all(s['cards']==['??','??'] for s in exported['events'][0]['snapshot']['seats'])
    assert not any(e.get('action',{}).get('decision') for e in exported['events'] if e.get('action'))


def test_next_hand_requires_player_and_reload_can_resume(client):
    c,app,_=client;rid,headers=new_room(client)
    c.post(f'/api/runs/{rid}/start',headers=headers);wait_state(c,rid,headers)
    c.post(f'/api/rooms/{rid}/action',json={'hand_number':1,'action_count':0,'action_id':'fold'},headers=headers)
    data=wait_state(c,rid,headers)
    assert data['run']['status']=='waiting_next_hand' and data['run']['active_hand'] is None
    assert len(app.state.store.hands(rid))==1
    # No in-memory action queue or browser connection is needed to recover the turn.
    restored=Runner(app.state.store,app.state.runner.provider)
    assert restored.store.run(rid)['status']=='waiting_next_hand'
    c.post(f'/api/runs/{rid}/start',headers=headers);data=wait_state(c,rid,headers)
    assert data['run']['active_hand']==2 and data['can_act']


def test_cannot_create_human_room_through_unprotected_generic_api(client):
    c,app,_=client;rid,_=new_room(client)
    cfg=app.state.store.run(rid)['config']
    assert c.post('/api/runs',json=cfg).status_code==422


@pytest.mark.asyncio
async def test_human_room_uses_existing_funded_rebuy_rules(tmp_path):
    runner=Runner(Store(str(tmp_path/'rules.sqlite')),OfflineModels())
    run,token=create_room(runner,RoomInput(model_ids=['bot']),[Entry(id='bot',name='Bot')])
    cfg=RunConfig.model_validate(run['config'])
    assert cfg.bankroll==1000000 and cfg.buy_in==20000 and cfg.settle_every==500 and cfg.cash_reset_at==200000
    assert cfg.cash_small_blind==50 and cfg.cash_big_blind==100
    human=next(s for s in run['seats'] if s['id']=='human-player')
    bot=next(s for s in run['seats'] if s['id']=='opponent-1')
    bot['stack']+=human['stack']+human['reserve'];human['stack']=human['reserve']=0
    assert not runner.prepare(run,cfg)
    assert human['retired'] and run['status']=='complete'


def test_duplicate_models_get_independent_seats_cards_and_accounts(client):
    c,app,_=client
    assert c.post('/api/auth/register',json={'id':'repeated','password':'test-password'}).status_code==200
    assert c.post('/api/auth/invite',json={'code':'test-invite'}).status_code==200
    result=c.post('/api/rooms',json={'model_ids':['deepseek']*9,'deepseek_thinking':'non-thinking'}).json()
    run=app.state.store.run(result['run']['id']);cfg=RunConfig.model_validate(run['config'])
    bots=[e for e in cfg.entries if e.provider!='human']
    assert len(bots)==9 and len({e.id for e in bots})==9
    assert len({e.name for e in bots})==9
    assert all(e.model=='deepseek-flash' and e.reasoning_effort=='non-thinking' for e in bots)
    hand=Hand(run['seats'],run['button'],cfg.blinds(0),cfg.seed)
    dealt=[card for cards in hand.original_holes.values() for card in cards]
    assert len(dealt)==20 and len(set(dealt))==20
    assert len(run['history'][0]['values'])==10
    assert all(s['stack']==cfg.buy_in and s['reserve']==cfg.bankroll-cfg.buy_in for s in run['seats'])


def test_host_kick_is_owner_only_and_waits_for_settlement(client):
    c,app,_=client;rid,headers=new_room(client)
    url=f'/api/rooms/{rid}/kick/opponent-1'
    assert c.post(url).status_code==403
    assert c.post(f'/api/rooms/{rid}/kick/human-player',headers=headers).status_code==422
    c.post(f'/api/runs/{rid}/start',headers=headers);data=wait_state(c,rid,headers)
    before=app.state.store.run(rid)
    assert c.post(url,headers=headers).status_code==200
    assert not any(s.get('retired') for s in app.state.store.run(rid)['seats'])
    assert c.get(f'/api/rooms/{rid}/state',headers=headers).json()['run']['pending_kicks']==['opponent-1']
    c.post(f'/api/rooms/{rid}/action',headers=headers,json={'hand_number':1,'action_count':0,'action_id':'fold'})
    after=app.state.store.run(rid)
    bot=next(s for s in after['seats'] if s['id']=='opponent-1')
    assert after['status']=='complete' and bot['retired_reason']=='host_kick'
    assert sum(s['stack']+s['reserve'] for s in after['seats'])==sum(s['initial'] for s in before['seats'])


@pytest.mark.parametrize('action,expected',[('call','check'),('raise_to_300','fold')])
def test_room_timeout_reports_and_continues_without_retiring(client,action,expected):
    c,app,_=client;rid,headers=new_room(client)
    async def timeout(*args,**kwargs):raise TimeoutError()
    app.state.runner.provider.call=timeout
    c.post(f'/api/runs/{rid}/start',headers=headers);wait_state(c,rid,headers)
    c.post(f'/api/rooms/{rid}/action',headers=headers,json={'hand_number':1,'action_count':0,'action_id':action})
    data=wait_state(c,rid,headers)
    raw=app.state.store.hand(rid,1)
    assert raw['actions'][1]['id']==expected
    assert any(e['type']=='model_timeout' for e in data['run']['events'])
    assert data['run']['status'] in ('waiting_human','waiting_next_hand')
    assert not any(s.get('retired') for s in app.state.store.run(rid)['seats'])


@pytest.mark.asyncio
async def test_room_deadline_cancels_pending_call(tmp_path,monkeypatch):
    from pokerbench import runner as module
    store=Store(str(tmp_path/'timeout.db'));provider=OfflineModels();runner=Runner(store,provider)
    registry=[Entry(id='bot',name='Bot',provider='systemone',key_env='')]
    run,_=create_room(runner,RoomInput(model_ids=['bot']),registry)
    run['seats'].sort(key=lambda s:s['id']=='human-player');run['button']=0;store.save_run(run)
    cancelled=[]
    async def pending(*a,**k):
        try:await asyncio.Event().wait()
        finally:cancelled.append(True)
    provider.call=pending
    original=asyncio.wait_for
    async def short_wait(future,timeout):
        assert timeout==15
        return await original(future,.01)
    monkeypatch.setattr(module.asyncio,'wait_for',short_wait)
    await runner.play(run['id'])
    result=store.run(run['id'])
    assert cancelled and result['hands_played']==1 and result['status']=='waiting_next_hand'
    assert store.hand(run['id'],1)['actions'][0]['id']=='fold'


def test_cash_chip_unit_and_legacy_remainder(client):
    c,app,_=client;rid,headers=new_room(client)
    run=app.state.store.run(rid);seat=run['seats'][0];seat['stack']-=25;seat['reserve']+=25;app.state.store.save_run(run)
    c.post(f'/api/runs/{rid}/start',headers=headers);wait_state(c,rid,headers)
    record=app.state.store.hand(rid,1)
    assert record['spec']['chip_unit']==50
    assert all(s['stack']%50==0 for s in record['spec']['seats'])
    assert sum(s['stack']+s['reserve'] for s in app.state.store.run(rid)['seats'])==sum(s['initial'] for s in run['seats'])


@pytest.mark.parametrize('seed',range(12))
def test_half_unit_bets_and_split_pots(seed):
    seats=[{'id':str(i),'name':str(i),'stack':500,'reserve':0} for i in range(3)]
    h=Hand(seats,0,(50,100,0),seed,chip_unit=50)
    while h.state.status:
        actions=h.legal()
        assert all(a.pay%50==0 and a.to%50==0 for a in actions)
        h.apply(next((a.id for a in actions if a.id in ('call','check')),actions[0].id))
        assert all(s%50==0 for s in h.state.stacks)
    assert sum(h.state.stacks)==1500
    assert h.state.divmod(250,2)==(100,50)
    assert Hand.restore(h.record()).snapshot()==h.snapshot()

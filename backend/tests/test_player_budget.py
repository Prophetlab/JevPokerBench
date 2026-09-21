from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from pokerbench.accounts import Accounts
from pokerbench.config import Entry, Settings
from pokerbench.engine import Hand
from pokerbench.player_budget import PlayerBudgetExceeded, usage, blocked
from pokerbench.rooms import RoomInput, create_room
from pokerbench.runner import Runner
from pokerbench.store import Store


def owned_store(tmp_path):
    store=Store(str(tmp_path/'budget.sqlite'))
    Accounts(store)
    return store


def invite(store, *users):
    with store.db:
        store.db.executemany('INSERT INTO player_invites VALUES(?,?)',[(u,time.time()-1) for u in users])


def reserve(store, run_id, amount, **kwargs):
    return store.reserve(run_id,amount,10000,1000,provider='deepseek',**kwargs)


def test_invitation_is_required_only_for_sponsored_deepseek(tmp_path):
    store=owned_store(tmp_path)
    with store.db:store.db.execute('INSERT INTO room_owners VALUES(?,?,0)',('room','alice'))
    for run_id in ('room','advisor/alice'):
        with pytest.raises(PlayerBudgetExceeded):reserve(store,run_id,.01)
        personal=reserve(store,run_id,8,self_funded=True)
        store.finish_call(personal,8,{})
    for provider in ('systemone','jev'):
        call=store.reserve('room',1,10000,100,provider=provider)
        store.finish_call(call,1,{})
    assert usage(store.db,'alice')==0 and blocked(store.db,'alice')
    assert store.spend()==2
    assert len(store.calls())==4


def test_account_five_yuan_allowance_spans_rooms_advisor_and_pending_calls(tmp_path):
    store=owned_store(tmp_path);invite(store,'alice','bob')
    with store.db:
        store.db.executemany('INSERT INTO room_owners VALUES(?,?,0)',[('one','alice'),('two','alice'),('three','bob')])
    first=reserve(store,'one',4)
    store.finish_call(first,4,{})
    pending=reserve(store,'two',.75)
    advisor=reserve(store,'advisor/alice',.25)
    assert usage(store.db,'alice')==5 and blocked(store.db,'alice')
    for run_id in ('one','two','advisor/alice'):
        with pytest.raises(PlayerBudgetExceeded):reserve(store,run_id,.001)
    reserve(store,'three',5)
    reserve(store,'formal-benchmark',100)
    assert usage(store.db,'bob')==5
    store.finish_call(pending,.25,{})
    assert usage(store.db,'alice')==4.5 and not blocked(store.db,'alice')
    store.finish_call(advisor,None,{'error':'timeout'})
    assert usage(store.db,'alice')==4.5
    reserve(store,'advisor/alice',.5)
    assert blocked(store.db,'alice')
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    store.db.close();restored=Store(path)
    assert usage(restored.db,'alice')==5 and blocked(restored.db,'alice')
    with pytest.raises(PlayerBudgetExceeded):reserve(restored,'two',.01)
    restored.db.close()


def test_failed_large_reservation_does_not_consume_or_lock_remaining_credit(tmp_path):
    store=owned_store(tmp_path);invite(store,'alice')
    paid=reserve(store,'advisor/alice',4.875);store.finish_call(paid,4.875,{})
    with pytest.raises(PlayerBudgetExceeded):reserve(store,'advisor/alice',.126)
    assert usage(store.db,'alice')==4.875 and not blocked(store.db,'alice')
    assert len(store.calls())==1
    reserve(store,'advisor/alice',.125)
    assert usage(store.db,'alice')==5


def test_billing_metadata_survives_finalization_and_self_funding_stays_separate(tmp_path):
    store=owned_store(tmp_path);invite(store,'alice')
    personal=reserve(store,'advisor/alice',7,self_funded=True)
    sponsored=reserve(store,'advisor/alice',2)
    assert store.spend()==2 and usage(store.db,'alice')==2
    store.finish_call(personal,6,{'provider':'deepseek','billing_user':'bob','self_funded':False})
    store.finish_call(sponsored,1,{'provider':'jev','billing_user':'bob','self_funded':True})
    assert store.spend()==1 and store.spend('advisor/alice')==7
    assert usage(store.db,'alice')==1 and usage(store.db,'bob')==0


def test_atomic_parallel_room_and_advisor_reservations(tmp_path):
    store=owned_store(tmp_path);invite(store,'alice')
    with store.db:
        store.db.executemany('INSERT INTO room_owners VALUES(?,?,0)',[('one','alice'),('two','alice')])
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    connections=[Store(path) for _ in range(3)]
    start=threading.Barrier(3)
    # Widen the race just before INSERT. Correct transaction locking will serialize
    # the readers before this callback; sleeping does not release a database lock.
    def delayed_insert(sql):
        if sql.startswith('INSERT INTO calls'):time.sleep(.05)
    def attempt(pair):
        connection,run_id=pair
        connection.db.set_trace_callback(delayed_insert)
        start.wait(timeout=5)
        try:return reserve(connection,run_id,3)
        except PlayerBudgetExceeded:return None
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(attempt,zip(connections,('one','two','advisor/alice'))))
        assert sum(r is not None for r in results)==1, 'Concurrent reservations spent the same invitation credit'
        assert usage(store.db,'alice')==3
        assert len(store.calls())==1
    finally:
        for connection in connections:connection.db.close()


class MeteredOffline:
    def __init__(self,store):self.store=store;self.executed=[]
    def available(self,entry):return True
    async def call(self,entry,request,**kwargs):
        amount=1 if entry.provider=='deepseek' else 0
        call=self.store.reserve(kwargs['run_id'],amount,10000,kwargs['run_budget'],provider=entry.provider)
        self.store.finish_call(call,amount,{'decision_id':kwargs['decision_id']})
        self.executed.append(entry.provider)
        choices=request['questions']['action']['criteria']
        choice='check' if 'check' in choices else 'call'
        return {'answers':{'action':{'choice':choice}}}


@pytest.mark.parametrize('mode',['cash','sng'])
@pytest.mark.asyncio
async def test_exhausted_credit_finishes_legally_without_retiring_paid_seat(tmp_path,mode):
    store=owned_store(tmp_path);invite(store,'alice')
    provider=MeteredOffline(store);runner=Runner(store,provider)
    registry=[Entry(id='paid',name='Paid',provider='deepseek'),
              Entry(id='local',name='Local',provider='systemone',input_cny_per_million=0,output_cny_per_million=0)]
    run,_=create_room(runner,RoomInput(mode=mode,model_ids=['paid','local'],max_hands=3,run_budget_cny=20),registry,owner_id='alice')
    run['seats'].sort(key=lambda s:s['id']);run['button']=0
    store.save_run(run)
    initial=sum(s['stack']+s['reserve'] for s in run['seats'])
    call=reserve(store,run['id'],5);store.finish_call(call,5,{})
    runner.start(run['id']);await runner.tasks[run['id']]
    for _ in range(30):
        current=store.run(run['id'])
        if current['status']=='waiting_next_hand':break
        assert current['status']=='waiting_human',current['message']
        hand=Hand.restore(store.hand(run['id'],current['active_hand']))
        choices={a.id for a in hand.legal()}
        runner.human_action(run['id'],hand.number,len(hand.actions),'check' if 'check' in choices else 'call')
        await runner.tasks[run['id']]
    else:pytest.fail('Hand did not complete')
    assert blocked(store.db,'alice') and 'deepseek' not in provider.executed
    first=store.hand(run['id'],1)
    assert first['complete']
    assert any(a['player']=='opponent-1' and a['kind'] in ('check','fold') for a in first['actions'])
    runner.start(run['id']);await runner.tasks[run['id']]
    current=store.run(run['id'])
    assert current['status']=='waiting_human',current['message']
    assert current['active_hand']==2
    assert not any(s.get('retired') for s in current['seats'])
    assert sum(s['stack']+s['reserve'] for s in current['seats'])==initial
    assert 'systemone' in provider.executed and store.spend(run['id'])==5


def test_invite_budget_is_private_and_public_account_directory_is_disabled(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import pokerbench.api as api_module
    monkeypatch.setattr(api_module,'ROOT',tmp_path)
    app=api_module.create_app(Settings(_env_file=None,pokerbench_data_dir=str(tmp_path/'app'),pokerbench_invite_code='test-invite'))
    with TestClient(app) as c:
        for name in ('Alice','Bob'):
            assert c.post('/api/auth/register',json={'id':name,'password':'fixture-password-123'}).status_code==200
        assert c.post('/api/auth/login',json={'id':'Alice','password':'fixture-password-123'}).status_code==200
        assert c.get('/api/auth/budget').json()['limit_cny']==0
        assert c.post('/api/auth/invite',json={'code':'test-invite'}).json()['limit_cny']==5
        c.post('/api/auth/logout')
        assert c.get('/api/auth/budget').status_code==401
        assert c.get('/api/auth/directory').status_code==404


@pytest.mark.asyncio
async def test_official_adapter_propagates_account_limit_without_network(tmp_path,monkeypatch):
    import httpx
    from pokerbench.provider import Provider
    store=owned_store(tmp_path);invite(store,'alice')
    with store.db:store.db.execute('INSERT INTO room_owners VALUES(?,?,0)',('room','alice'))
    call=reserve(store,'room',4.9);store.finish_call(call,4.9,{})
    async def forbidden(*args,**kwargs):pytest.fail('Budget guard allowed an outbound paid request')
    monkeypatch.setattr(httpx.AsyncClient,'post',forbidden)
    provider=Provider(Settings(_env_file=None,deepseek_api_key='test-only'),store)
    entry=Entry(id='ds',name='DS',provider='deepseek')
    hand=Hand([{'id':'a','stack':20000},{'id':'b','stack':20000}],0,(50,100,0),5)
    with pytest.raises(PlayerBudgetExceeded):
        await provider.call(entry,hand.request(entry.model),decision_id='room:1:0',run_id='room',run_budget=20,max_tokens=131072)
    assert len(store.calls('room'))==1

import json
from copy import deepcopy

import httpx
import pytest

from pokerbench.config import Entry,Settings
from pokerbench.provider import Provider,ProviderError
from pokerbench.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('benchmark,detail,repairs',[
    (True,'Row action: 4427 input tokens exceed limit 4096; no truncation allowed',True),
    (False,'Row action: 4427 input tokens exceed limit 4096; no truncation allowed',False),
    (True,'Invalid request',False),
])
async def test_overflow_preserves_state_and_cached_decisions(tmp_path,monkeypatch,benchmark,detail,repairs):
    store=Store(str(tmp_path/'s.db'))
    provider=Provider(Settings(_env_file=None),store)
    entry=Entry(id='local',name='Local',provider='systemone',key_env='',expected_model='local')
    request={'state':{'history':[{'id':'call','pay':100}]*30,'label':'河牌'},
             'questions':{'action':{'type':'choice','criteria':{'call':{},'fold':{}}}}}
    original=deepcopy(request);sent=[]
    async def post(client,url,**kw):
        payload=deepcopy(kw['json']);sent.append(payload)
        if isinstance(payload['state'],dict):return httpx.Response(422,json={'detail':detail})
        assert json.loads(payload['state'])==original['state']
        assert payload['questions']==original['questions']
        return httpx.Response(200,json={'model':'local','answers':{'action':{
            'type':'choice','choice':'call','probabilities':{'call':.8,'fold':.2},'confidence':.8}}})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    args=dict(decision_id='run:8:30',run_id='run',benchmark=benchmark)
    if repairs:
        result=await provider.call(entry,request,**args)
        assert result['format_repair']=='lossless_compact_json_state'
        assert await provider.call(entry,request,**args)==result
        assert len(sent)==2
        calls=store.calls('run')
        assert len(calls)==2 and json.loads(calls[1]['body'])['format_repair']==result['format_repair']
    else:
        with pytest.raises(ProviderError,match='HTTP 422'):await provider.call(entry,request,**args)
        assert len(sent)==1
    assert request==original

import asyncio
import hashlib
import json
import math
import os
import time
from copy import deepcopy
from contextlib import asynccontextmanager

import httpx
from dotenv import dotenv_values
from system_one_adapter import AsyncSystemOneAdapterClient
from typesafe_sdk import RetryPolicy, TypeSafeError

from .official_adapter import ADAPTER_METHOD, MeteredChatProvider, RetryableTransportError, identity_error

from .config import ROOT, Entry, Settings
from .store import Store
from .public_access import redact_key


class ProviderError(Exception):
    pass


def condition_legal_actions(response: dict, request: dict):
    """Explicit forced-action policy for endpoints with a reserved abstention class."""
    answers=response.get("answers",{})
    for qid,question in request["questions"].items():
        answer=answers.get(qid,{})
        probs=answer.get("probabilities",{})
        if not isinstance(probs,dict) or "__insufficient_evidence__" not in probs:
            continue
        legal=set(question["criteria"])
        if set(probs)!=legal|{"__insufficient_evidence__"}:
            raise ProviderError("模型概率未覆盖全部合法动作")
        if any(isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1 for p in probs.values()):
            raise ProviderError("模型概率之和不为 1")
        mass=sum(probs[k] for k in legal)
        if mass<=0:raise ProviderError("模型没有合法动作概率")
        projected={k:probs[k]/mass for k in question["criteria"]}
        chosen=max(projected,key=projected.get)
        answer["legal_action_projection"]={"policy":"condition_on_legal_actions",
            "abstention_probability":probs["__insufficient_evidence__"],"original_probabilities":probs.copy(),
            "reported_choice":answer.get("choice"),"reported_abstention":bool(answer.get("is_abstention")),
            "choice_changed":chosen!=answer.get("choice")}
        answer["probabilities"],answer["choice"]=projected,chosen
    return response


def validate_answer(response: dict, request: dict, quantized: bool = False):
    if not isinstance(response,dict):
        raise ProviderError("模型响应必须是 JSON 对象")
    expected = set(request["questions"])
    answers = response.get("answers")
    if not isinstance(answers,dict) or set(answers)!=expected:
        raise ProviderError("模型响应缺少或多出问题")
    for qid,question in request["questions"].items():
        answer=answers[qid]
        if not isinstance(answer,dict) or answer.get("type")!="choice":
            raise ProviderError("当前动作接口要求 Choice 响应")
        probs=answer.get("probabilities",{})
        if not isinstance(probs,dict) or set(probs)!=set(question["criteria"]):
            raise ProviderError("模型概率未覆盖全部合法动作")
        if any(isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or p<0 or p>1 for p in probs.values()):
            raise ProviderError("模型概率不是有效的 0–1 数值")
        total=sum(probs.values())
        # Keep the relative strategy while accepting non-unit positive totals.
        if total<=0:
            raise ProviderError("模型概率之和不为 1：没有正概率动作")
        if total!=1:
            answer["normalization_original_sum"]=total
            answer["normalization_original_probabilities"]=probs.copy()
            answer["probabilities"]={k:p/total for k,p in probs.items()}
            probs=answer["probabilities"]
        chosen=answer.get("choice")
        if chosen not in probs or probs[chosen]<max(probs.values())-1e-8:
            raise ProviderError("Choice 与最高概率动作不一致")
        confidence=answer.get("confidence")
        if isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or not 0<=confidence<=1:
            raise ProviderError("confidence 必须是 0–1 数值")
    return response


class Provider:
    def __init__(self, settings: Settings, store: Store):
        self.settings,self.store=settings,store
        self.semaphore=asyncio.Semaphore(128)
        self.jev_semaphore=asyncio.Semaphore(128)
        self.env_keys=dotenv_values(ROOT/".env")

    @asynccontextmanager
    async def capacity(self,entry):
        if entry.provider in ('deepseek','openai_compatible'):
            yield
        elif entry.provider=='jev':
            async with self.jev_semaphore:yield
        else:
            async with self.semaphore:yield

    def key(self, entry: Entry):
        return os.getenv(entry.key_env) or {"DEEPSEEK_API_KEY":self.settings.deepseek_api_key,
                                          "JEV_API_KEY":self.settings.jev_api_key}.get(entry.key_env,"") or self.env_keys.get(entry.key_env,"")

    def available(self, entry: Entry):
        return bool(self.key(entry)) or entry.provider in ("systemone","openai_compatible") and not entry.key_env

    async def call(self, entry: Entry, request: dict, *, decision_id: str, run_id: str,
                   run_budget: float=20, timeout: float=120, max_tokens: int=8192, should_stop=None, key_override: str | None=None):
        request={**request,"model":entry.model}
        request_hash=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        existing=self.store.decision(decision_id)
        if existing:
            if existing["request_hash"]!=request_hash:
                raise ProviderError("决策 ID 的输入不一致")
            if existing.get("thinking","provider_default")!=(entry.reasoning_effort or "provider_default"):
                raise ProviderError("决策 ID 的思考设置不一致")
            mismatch=identity_error(entry,{"model":existing.get("model"),"revision":existing.get("model_revision")})
            if mismatch:raise ProviderError(mismatch)
            return existing
        if key_override is not None:
            from .public_access import personal_entry
            entry=personal_entry(entry)
        key=key_override if key_override is not None else self.key(entry)
        if not key_override and not self.available(entry):
            raise ProviderError(f"缺少 {entry.key_env} 环境配置")
        if entry.provider in ("deepseek","openai_compatible"):
            async with self.capacity(entry):
                return await self._call_adapter(entry,request,key,request_hash,decision_id,run_id,
                                                run_budget,timeout,max_tokens,should_stop,key_override is not None)
        payload=request
        url=entry.base_url.rstrip("/")+"/v1/systemone"
        # Conservative byte-based input token bound; rate is peak, includes thinking output.
        reserve=(len(json.dumps(payload).encode())+512)*entry.input_cny_per_million/1e6+max_tokens*entry.output_cny_per_million/1e6
        async with self.capacity(entry):
            started=time.monotonic()
            attempts=[]
            total_cost=0.0
            for attempt in range(3):
                if should_stop and should_stop():
                    raise asyncio.CancelledError
                call_id=self.store.reserve(run_id,reserve,self.settings.pokerbench_budget_cny,run_budget,provider=entry.provider,self_funded=key_override is not None)
                start=time.monotonic()
                headers={"Authorization":f"Bearer {key}"} if key else {}
                charge=None
                raw={}
                meta={"decision_id":decision_id,"attempt":attempt+1,"request":payload}
                retryable=True
                try:
                    async with httpx.AsyncClient(timeout=timeout,trust_env=key_override is None) as client:
                        response=await client.post(url,json=payload,headers=headers)
                    if response.status_code!=200:
                        retryable=response.status_code in (408,429,500,502,503,504,529)
                        raise ProviderError(f"模型端点返回 HTTP {response.status_code}")
                    raw=response.json()
                    if not isinstance(raw,dict):
                        raise ProviderError("模型响应必须是 JSON 对象")
                    meta["raw_response"]=deepcopy(raw)
                    usage=raw.get("usage",{})
                    usage=usage if isinstance(usage,dict) else {}
                    input_tokens=usage.get("prompt_tokens",usage.get("input_tokens"))
                    output_tokens=usage.get("completion_tokens",usage.get("output_tokens"))
                    if all(isinstance(n,int) and not isinstance(n,bool) and n>=0 for n in (input_tokens,output_tokens)):
                        charge=(input_tokens*entry.input_cny_per_million+output_tokens*entry.output_cny_per_million)/1e6
                    meta.update(model=raw.get("model",entry.model),input_tokens=input_tokens,
                                output_tokens=output_tokens)
                    mismatch=identity_error(entry,raw)
                    if mismatch:
                        retryable=False
                        raise ProviderError(mismatch)
                    answer=raw
                    if entry.abstention_policy=="condition_on_legal_actions":
                        condition_legal_actions(answer,request)
                    validate_answer(answer,request,quantized=entry.provider=="jev")
                except (httpx.HTTPError,ProviderError,ValueError,KeyError,IndexError,TypeError,AttributeError) as exc:
                    reason=str(exc) if isinstance(exc,ProviderError) else "网络连接异常" if isinstance(exc,httpx.HTTPError) else "JSON 或响应结构无效"
                    meta.update(error=type(exc).__name__,reason=reason,latency=time.monotonic()-start)
                    self.store.finish_call(call_id,charge,redact_key(meta,key_override) if key_override else meta)
                    total_cost+=reserve if charge is None else charge
                    attempts.append({"attempt":attempt+1,"outcome":"rejected","reason":reason,"call_id":call_id})
                    if retryable and attempt<2:
                        await asyncio.sleep(attempt+1)
                        continue
                    raise ProviderError(f"{reason}；已尝试 {attempt+1} 次，未执行动作") from None
                meta.update(latency=time.monotonic()-start,outcome="accepted")
                self.store.finish_call(call_id,charge,redact_key(meta,key_override) if key_override else meta)
                total_cost+=reserve if charge is None else charge
                attempts.append({"attempt":attempt+1,"outcome":"accepted","call_id":call_id})
                result={"model":raw.get("model",entry.model),"answers":answer["answers"],
                        "usage":{"input_tokens":input_tokens,"output_tokens":output_tokens},
                        "latency":time.monotonic()-started,"cost_cny_estimate":total_cost,"request_hash":request_hash,
                        "decision_id":decision_id,"provider":entry.provider,"proxy":entry.proxy,
                        "probability_source":"endpoint",
                        "thinking":"provider_default","entry_id":entry.id,"attempts":attempts,
                        "retry_count":len(attempts)-1,"format_repair":meta.get("format_repair")}
                result["abstention_policy"]=entry.abstention_policy
                result["model_revision"]=raw.get("revision")
                if key_override:result=redact_key(result,key_override)
                self.store.save_decision(decision_id,result)
                return result
        raise ProviderError("模型调用未完成")

    async def _call_adapter(self, entry, request, key, request_hash, decision_id, run_id,
                            run_budget, timeout, max_tokens, should_stop, self_funded=False):
        transport=MeteredChatProvider(entry,self.settings,self.store,key,decision_id,run_id,
                                      run_budget,timeout,max_tokens,should_stop,self_funded)
        started=time.monotonic()
        # Upstream handles schema corrections and transport retries. The metered
        # provider caps their combined request count, so allowances cannot multiply.
        retry=RetryPolicy(max_retries=2,exceptions={RetryableTransportError},
                          backoff_initial=1,backoff_max=2,backoff_jitter=0,timeout=None)
        options={"structured_outputs":entry.structured_outputs,"llm_answer_mode":"probabilities",
                 "normalize_probabilities":False,"n_retry_malformed_structure":2}
        async with AsyncSystemOneAdapterClient(**options,retry=retry) as client:
            while len(transport.attempts)<3:
                try:
                    response=await client.system_one(state=request["state"],questions=request["questions"],model=transport)
                except TypeSafeError:
                    reason=transport.attempts[-1].get("reason","JSON 或响应结构无效") if transport.attempts else "模型响应无效"
                    transport.mark_last("rejected",reason)
                    raise ProviderError(f"{reason}；已尝试 {len(transport.attempts)} 次，未执行动作") from None
                answer=response.model_dump(mode="json")
                try:
                    validate_answer(answer,request)
                except ProviderError as exc:
                    # Upstream validates keys and bounds but, with normalization
                    # disabled, does not reject a zero/invalid total. Never turn
                    # that into a fabricated uniform strategy.
                    transport.mark_last("rejected",str(exc))
                    if len(transport.attempts)<3:continue
                    raise ProviderError(f"{exc}；已尝试 3 次，未执行动作") from None
                transport.mark_last("accepted")
                known=all(a.get("input_tokens") is not None for a in transport.attempts)
                result={"model":transport.served_model,"answers":answer["answers"],
                        "usage":{"input_tokens":sum(a["input_tokens"] for a in transport.attempts) if known else None,
                                 "output_tokens":sum(a["output_tokens"] for a in transport.attempts) if known else None},
                        "latency":time.monotonic()-started,"cost_cny_estimate":transport.cost,"request_hash":request_hash,
                        "decision_id":decision_id,"provider":entry.provider,"proxy":entry.proxy,
                        "probability_source":"verbalized","thinking":entry.reasoning_effort or "provider_default","entry_id":entry.id,
                        "adapter":{**ADAPTER_METHOD,"structured_outputs":entry.structured_outputs},
                        "retry_count":len(transport.attempts)-1,
                        "attempts":[{k:a[k] for k in ("attempt","outcome","call_id","reason") if k in a} for a in transport.attempts]}
                if self_funded:result=redact_key(result,key)
                self.store.save_decision(decision_id,result)
                return result
        raise ProviderError("模型调用未完成")

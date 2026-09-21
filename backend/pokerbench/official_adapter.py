"""Official System One client uses this public provider hook for metered HTTP.

Prompting, schema construction, decoding and corrective retries stay in the
unmodified upstream package. This transport bounds and accounts for every call.
"""
import asyncio
import json
import time

import httpx
import httpx2
from system_one_adapter.providers import ProviderResult
from system_one_adapter.providers.base import render_messages
from typesafe_sdk import TypeSafeAPIError, TypeSafeError
from .public_access import redact_key

ADAPTER_METHOD = {"name":"system-one-adapter", "version":"0.2.0",
                  "llm_answer_mode":"probabilities", "normalize_probabilities":False,
                  "max_requests_per_decision":3, "n_retry_malformed_structure":2}


class RetryableTransportError(TypeSafeError):
    pass


def identity_error(entry, response):
    actual=response.get("model")
    # The benchmark uses Luna's stable alias; provider version suffixes are allowed.
    luna_alias=(entry.expected_model=="gpt-5.6-luna" and isinstance(actual,str)
                and "gpt-5.6-luna" in actual.split("/")[-1])
    if entry.expected_model and actual!=entry.expected_model and not luna_alias:
        return f"模型身份不符：期望 {entry.expected_model}，实际 {str(response.get('model'))[:100]}"
    if entry.expected_revision and response.get("revision")!=entry.expected_revision:
        return "模型权重版本不符：返回的 revision 缺失或已经改变"
    return None


class MeteredChatProvider:
    def __init__(self, entry, settings, store, key, decision_id, run_id,
                 run_budget, timeout, max_tokens, should_stop, self_funded=False):
        self.entry,self.settings,self.store=entry,settings,store
        self.self_funded=self_funded
        self.model_name=entry.model
        self.key,self.decision_id,self.run_id=key,decision_id,run_id
        self.run_budget,self.timeout,self.max_tokens=run_budget,timeout,max_tokens
        self.should_stop=should_stop
        self.attempts=[]
        self.cost=0.0
        self.served_model=entry.model

    def translate_error(self, error):
        return error

    def mark_last(self, outcome, reason=None):
        if not self.attempts:return
        item=self.attempts[-1]
        item["outcome"]=outcome
        if reason:item["reason"]=reason
        self.store.finish_call(item["call_id"],item["charge"],redact_key(item,self.key) if self.self_funded else item)

    async def request(self, messages, *, schema, structured):
        if self.should_stop and self.should_stop():raise asyncio.CancelledError
        if len(self.attempts)>=3:raise TypeSafeError("已达到每次决策 3 次请求的上限")
        if self.attempts and self.attempts[-1]["outcome"]=="received":
            self.mark_last("rejected","官方 adapter 请求格式纠正")
        payload={"model":self.model_name,"messages":render_messages(messages),"max_tokens":self.max_tokens,
                 "response_format":({"type":"json_schema","json_schema":{"name":"evaluation","schema":schema,"strict":True}}
                                    if structured else {"type":"json_object"})}
        if self.entry.reasoning_effort=="non-thinking":
            payload["thinking"]={"type":"disabled"}
        elif self.entry.reasoning_effort is not None:
            payload[self.entry.reasoning_parameter]=({"effort":self.entry.reasoning_effort}
                if self.entry.reasoning_parameter=="reasoning" else self.entry.reasoning_effort)
        # Includes the official schema and any previous answer/correction messages.
        reserve=(len(json.dumps(payload).encode())+512)*self.entry.input_cny_per_million/1e6+self.max_tokens*self.entry.output_cny_per_million/1e6
        call_id=self.store.reserve(self.run_id,reserve,self.settings.pokerbench_budget_cny,self.run_budget,provider=self.entry.provider,self_funded=self.self_funded)
        item={"call_id":call_id,"decision_id":self.decision_id,"attempt":len(self.attempts)+1,
              "adapter":ADAPTER_METHOD,"request":payload,"charge":None,"outcome":"pending"}
        self.attempts.append(item)
        started=time.monotonic()
        try:
            base=self.entry.base_url or self.settings.deepseek_base_url
            headers={"Authorization":f"Bearer {self.key}"} if self.key else {}
            if self.entry.credential_id:
                from .custom_endpoint import custom_client
                connection=custom_client(base,self.timeout)
            else:
                connection=httpx.AsyncClient(timeout=self.timeout,trust_env=not self.self_funded)
            async with connection as client:
                response=await client.post(base.rstrip("/")+"/chat/completions",json=payload,headers=headers)
            if response.status_code!=200:
                item["reason"]=f"模型端点返回 HTTP {response.status_code}"
                raise TypeSafeAPIError(response.status_code,{},httpx2.Headers(),item["reason"])
            raw=response.json()
            usage=raw.get("usage",{})
            usage=usage if isinstance(usage,dict) else {}
            inp,out=usage.get("prompt_tokens"),usage.get("completion_tokens")
            known=all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in (inp,out))
            item.update(input_tokens=inp if known else None,output_tokens=out if known else None)
            if known:item["charge"]=(inp*self.entry.input_cny_per_million+out*self.entry.output_cny_per_million)/1e6
            self.served_model=raw.get("model") or self.model_name
            item.update(model=self.served_model,raw_response=raw)
            mismatch=identity_error(self.entry,raw)
            if mismatch:
                item["reason"]=mismatch
                raise TypeSafeError(mismatch)
            choice=raw["choices"][0]
            if choice.get("finish_reason")!="stop":raise RetryableTransportError("模型输出未完整结束")
            content=choice["message"]["content"]
            if not isinstance(content,str):raise RetryableTransportError("模型未返回文本 JSON")
            item["outcome"]="received"
            return ProviderResult(text=content,input_tokens=inp if known else 0,output_tokens=out if known else 0)
        except httpx.HTTPError:
            item["reason"]="网络连接异常"
            raise RetryableTransportError(item["reason"]) from None
        except RetryableTransportError as exc:
            item["reason"]=str(exc)
            raise
        except (ValueError,TypeError,AttributeError,KeyError,IndexError):
            item["reason"]="JSON 或响应结构无效"
            raise RetryableTransportError(item["reason"]) from None
        finally:
            if item["outcome"]=="pending":item["outcome"]="rejected"
            item["latency"]=time.monotonic()-started
            self.cost+=reserve if item["charge"] is None else item["charge"]
            self.store.finish_call(call_id,item["charge"],redact_key(item,self.key) if self.self_funded else item)

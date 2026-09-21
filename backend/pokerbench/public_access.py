"""Public presentation and user-supplied credentials. Secrets never enter run state."""
from fastapi import HTTPException
import json
import re

PRIVATE_FIELDS={'base_url','key_env','expected_revision','revision','input_cny_per_million',
                'output_cny_per_million','cost_cny_estimate','run_budget_cny','spent_cny_estimate',
                'budget_cny','request_hash','raw_response','request','seed','player_token_hash'}


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in PRIVATE_FIELDS}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def public_entry(entry,ready):
    return {k:v for k,v in {**entry.model_dump(),'ready':ready,'requires_invitation':entry.provider=='deepseek'}.items()
            if k in ('id','name','color','provider','model','proxy','ready','requires_invitation','reasoning_effort','credential_id')}


def user_keys(request):
    result={}
    for provider,header in [('jev','x-jev-key'),('deepseek','x-deepseek-key')]:
        value=request.headers.get(header,'').strip()
        if value:
            if len(value)>1024 or any(ord(c)<33 or ord(c)>126 for c in value):
                raise HTTPException(422,'API Key 格式无效')
            result[provider]=value
    encoded=request.headers.get('x-agent-keys','')
    if encoded:
        try:
            if len(encoded)>12000:raise ValueError()
            custom=json.loads(encoded)
            if not isinstance(custom,dict) or len(custom)>9:raise ValueError()
            for key,value in custom.items():
                if not re.fullmatch(r'custom-[a-z0-9_-]{1,32}',key) or not isinstance(value,str) or not 1<=len(value)<=1024 or any(ord(c)<33 or ord(c)>126 for c in value):raise ValueError()
            result.update(custom)
        except (ValueError,TypeError):raise HTTPException(422,'自定义 Agent Key 格式无效') from None
    return result


def personal_entry(entry):
    # A visitor's key is sent only to the fixed official provider, never to a registry URL.
    if entry.credential_id:return entry
    urls={'jev':'https://api.typesafe.ai','deepseek':'https://api.deepseek.com'}
    return entry.model_copy(update={'base_url':urls[entry.provider]})


def redact_key(value,key):
    """Guard against an untrusted endpoint reflecting the supplied key in its reply."""
    if isinstance(value,str):return value.replace(key,'[redacted]') if key else value
    if isinstance(value,dict):return {redact_key(k,key):redact_key(v,key) for k,v in value.items()}
    if isinstance(value,list):return [redact_key(v,key) for v in value]
    return value

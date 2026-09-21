"""Human tables use registry models, the same poker rules, and a private seat token."""
import hashlib
import secrets
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from .config import Entry, RunConfig
from .engine import observer_record


class CustomAgent(BaseModel):
    id: str = Field(pattern=r"^custom-[a-z0-9_-]{1,32}$")
    name: str = Field(min_length=1,max_length=60)
    endpoint: str = Field(min_length=1,max_length=2048)
    model: str = Field(min_length=1,max_length=160)
    api_format: Literal['chat_completions','responses','anthropic'] = 'chat_completions'

    @model_validator(mode='after')
    def endpoint_url(self):
        from .custom_protocol import agent_endpoint
        self.endpoint=agent_endpoint(self.endpoint,self.api_format)
        if not self.name.strip() or not self.model.strip():
            raise ValueError('Name and model cannot be blank.')
        return self

    def entry(self):
        return Entry(id=self.id,name=self.name,provider='openai_compatible',model=self.model,
            base_url=self.endpoint,key_env='',proxy=False,revision='user-supplied',credential_id=self.id,api_format=self.api_format,
            input_cny_per_million=0,output_cny_per_million=0)


class RoomInput(BaseModel):
    name: str = Field(default="My table",min_length=1,max_length=100)
    player_name: str = Field(default="You",min_length=1,max_length=40)
    mode: Literal["cash","sng"] = "cash"
    custom_agents: list[CustomAgent] = Field(default_factory=list,max_length=9)
    model_ids: list[str] = Field(min_length=1,max_length=9)
    billing_mode: Literal["hosted","personal"] = "hosted"
    deepseek_thinking: Literal["low","non-thinking"] = "low"
    max_hands: int = Field(default=5000,ge=1,le=100000)
    run_budget_cny: float = Field(default=20,gt=0,le=10000)


class HumanActionInput(BaseModel):
    hand_number: int = Field(ge=1)
    action_count: int = Field(ge=0)
    action_id: str = Field(max_length=80)


def selectable(entry):
    # These restrictions apply to human rooms; the benchmark roster stays intact.
    if entry.credential_id:return True
    identity=" ".join((entry.id,entry.name,entry.model,entry.expected_model or "")).lower()
    return entry.provider!="human" and not any(word in identity for word in ("gpt","luna"))


def create_room(runner, body, registry, *, owner_id=None):
    available={e.id:e for e in registry if selectable(e)}
    if any(pid not in available for pid in body.model_ids):
        raise ValueError("请选择列表中的模型，GPT 不参与自组局")
    entries=[available[pid].model_copy(deep=True) for pid in body.model_ids]
    if any(not runner.provider.available(e) and not (body.billing_mode=="personal" and e.provider in ("jev","deepseek")) for e in entries):
        raise ValueError("所选模型尚未配置连接")
    counts={}
    for index,entry in enumerate(entries):
        original=entry.id
        counts[original]=counts.get(original,0)+1
        entry.id=f"opponent-{index+1}"
        if body.model_ids.count(original)>1:
            entry.name=f"{entry.name} #{counts[original]}"
        if entry.provider=="deepseek":
            entry.reasoning_effort=body.deepseek_thinking
    human_id="human-player"
    entries.insert(0,Entry(id=human_id,name=body.player_name,provider="human",model="human",
                           base_url="",key_env="",proxy=False,revision="manual",color="#d9af4e"))
    cfg=RunConfig(name=body.name,mode=body.mode,entries=entries,human_player_id=human_id,
                  seed=secrets.randbelow(2**63),max_hands=body.max_hands,run_budget_cny=body.run_budget_cny,billing_mode=body.billing_mode,
                  decision_timeout=15,max_output_tokens=32768)
    token=secrets.token_urlsafe(32)
    run=runner.create(cfg,persist=False)
    run["player_token_hash"]=hashlib.sha256(token.encode()).hexdigest()
    runner.store.save_run(run,owner_id=owner_id)
    return run,token


def check_owner(run, request):
    token=request.headers.get("x-player-token","")
    if not run["config"].get("human_player_id") or not secrets.compare_digest(
        run.get("player_token_hash",""),hashlib.sha256(token.encode()).hexdigest()
    ):
        raise HTTPException(403,"此浏览器没有该真人席位的操作权限")


def room_record(record, hero=None):
    result=observer_record(record,hero=hero)
    result["analysis"]=None
    for event in result["events"]:
        if event.get("action"):
            event["action"].pop("decision",None)
    return result


def room_state(run, store):
    number=run["active_hand"] or run["hands_played"]
    if not number:
        return {"record":None,"action_count":0,"can_act":False}
    record=store.hand(run["id"],number)
    return {"record":room_record(record,run["config"]["human_player_id"]),
            "action_count":len(record["actions"]),"can_act":run["status"]=="waiting_human"}

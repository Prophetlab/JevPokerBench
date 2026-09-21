from contextlib import asynccontextmanager
import asyncio
import json
import time
from pathlib import Path
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .advisor import AdvisorInput, equity, reconstruct
from .accounts import Accounts, private_json, same_origin
from .player_budget import install_routes as install_player_budget_routes
from .config import ROOT, Entry, RunConfig, Settings, default_entries
from .engine import observer_record
from .provider import Provider, ProviderError
from .public_access import clean, public_entry, user_keys
from .player_budget import invited, PlayerBudgetExceeded
from .official_adapter import ADAPTER_METHOD
from .runner import Runner, public_run
from .store import Store
from .series import SeriesRunner, public_series
from .rooms import RoomInput, HumanActionInput, selectable, create_room, check_owner, room_state, room_record


def create_app(settings: Settings | None = None):
    settings=settings or Settings()
    data=Path(settings.pokerbench_data_dir)
    data.mkdir(parents=True,exist_ok=True)
    store=Store(str(data/"pokerbench.sqlite"))
    provider=Provider(settings,store)
    runner=Runner(store,provider)
    series_runner=SeriesRunner(runner)
    entries_file=ROOT/"config"/"entries.json"

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks=[*series_runner.tasks.values(),*runner.tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        store.db.close()

    app=FastAPI(title="PokerBench",version="0.1.0",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store,app.state.runner,app.state.provider=store,runner,provider
    app.state.series_runner=series_runner
    accounts=Accounts(store)
    accounts.routes(app)
    install_player_budget_routes(app,store,accounts,settings)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        if request.url.path.startswith('/api/auth/'):
            # Validation failures must not echo a password from the submitted body.
            return JSONResponse({'detail':[{k:e[k] for k in ('type','loc','msg')} for e in exc.errors()]},status_code=422)
        return await request_validation_exception_handler(request,exc)

    def entries():
        return [Entry.model_validate(e) for e in json.loads(entries_file.read_text())] if entries_file.exists() else default_entries()

    def admin(request: Request):
        same_origin(request)
        if settings.pokerbench_admin_token:
            if request.headers.get("authorization")!=f"Bearer {settings.pokerbench_admin_token}":
                raise HTTPException(401,"需要管理令牌")
        elif request.client and request.client.host not in ("127.0.0.1","::1","testclient"):
            raise HTTPException(403,"远程管理需设置 POKERBENCH_ADMIN_TOKEN")

    def room_owner(run, request, *, write=False):
        owner=accounts.owner(run['id'])
        if owner:
            user=accounts.user(request,required=True)
            if user['uid']!=owner:
                raise HTTPException(403,"你没有这个牌桌的操作权限")
            if write:same_origin(request)
        else:
            # Unclaimed legacy rooms retain their original token and admin checks.
            check_owner(run,request)
            if write:admin(request)

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404,content={"detail":"记录不存在"})

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,content={"detail":str(exc)})

    @app.exception_handler(ProviderError)
    async def model_error(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502,content={"detail":str(exc)})

    @app.exception_handler(PlayerBudgetExceeded)
    async def no_credit(request,exc):
        return JSONResponse({"detail":"DeepSeek 邀请额度不足，请使用自己的 Key 或选择其他模型"},status_code=402,headers={"Cache-Control":"no-store"})

    @app.get("/api/health")
    async def health():
        return {"ok":True,"version":"0.1.0"}

    @app.get("/api/entries")
    async def get_entries():
        return [public_entry(e,provider.available(e)) for e in entries()]

    @app.get("/api/admin/entries",dependencies=[Depends(admin)])
    async def admin_entries():
        return [e.model_dump() for e in entries()]

    @app.put("/api/entries",dependencies=[Depends(admin)])
    async def save_entries(body: list[Entry]):
        if not 2<=len(body)<=10 or len({e.id for e in body})!=len(body) or any(e.provider=="human" for e in body):
            raise ValueError("需要 2–10 个不重复的参赛条目")
        entries_file.parent.mkdir(parents=True,exist_ok=True)
        temporary=entries_file.with_suffix(".tmp")
        temporary.write_text(json.dumps([e.model_dump() for e in body],ensure_ascii=False,indent=2))
        temporary.replace(entries_file)
        return await get_entries()

    @app.get("/api/defaults")
    async def defaults():
        return clean(RunConfig(entries=entries()).model_dump())

    @app.get("/api/runs")
    async def list_runs():
        return [public_run(r,store) for r in store.runs() if not r["config"].get("human_player_id")]

    @app.get("/api/rooms/models")
    async def room_models():
        return [public_entry(e,provider.available(e)) for e in entries() if selectable(e)]

    @app.post("/api/rooms")
    async def new_room(body: RoomInput, request: Request):
        same_origin(request)
        user=accounts.user(request,required=True)
        keys=user_keys(request)
        custom=[agent.entry() for agent in body.custom_agents]
        if len({e.id for e in custom})!=len(custom):raise HTTPException(422,'自定义 Agent ID 重复')
        if custom and body.billing_mode!='personal':raise HTTPException(422,'自定义 Agent 请使用自己的 API Key')
        registry={e.id:e for e in [*entries(),*custom]}
        for pid in body.model_ids:
            entry=registry.get(pid)
            if entry is None or not selectable(entry):raise HTTPException(422,'请选择可用模型')
            if body.billing_mode=='personal' and entry.provider in ('deepseek','jev','openai_compatible') and not keys.get(entry.credential_id or entry.provider):
                raise HTTPException(422,'请填写所选模型的个人 API Key')
            if body.billing_mode!='personal' and entry.provider=='deepseek' and not invited(store.db,user['uid']):
                raise HTTPException(403,'DeepSeek 需要邀请码或你自己的 API Key')
        body=body.model_copy(update={'player_name':user['display_id']})
        run,_=create_room(runner,body,list(registry.values()),owner_id=user['uid'])
        return private_json({"run":public_run(run,store)})

    @app.get("/api/rooms/mine")
    async def my_rooms(request: Request):
        user=accounts.user(request,required=True)
        ids=store.db.execute("SELECT run_id FROM room_owners WHERE user_id=? AND run_id NOT IN (SELECT run_id FROM deleted_rooms) ORDER BY visited DESC",(user['uid'],))
        return private_json([public_run(store.run(row[0]),store) for row in ids])

    @app.delete("/api/rooms/{run_id}")
    async def delete_room(run_id: str, request: Request):
        same_origin(request)
        user=accounts.user(request,required=True)
        if accounts.owner(run_id)!=user['uid']:
            raise HTTPException(403,"你没有这个牌桌的操作权限")
        if store.db.execute('SELECT 1 FROM deleted_rooms WHERE run_id=?',(run_id,)).fetchone():
            return {"ok":True}
        run=store.run(run_id)
        task=runner.tasks.get(run_id)
        if run['status']=='running' or task and not task.done():
            raise HTTPException(409,"请先暂停牌局再删除")
        # Retain ownership and the full billing ledger; deletion cannot reset credit.
        with store.db:
            store.db.execute('INSERT INTO deleted_rooms VALUES(?,?)',(run_id,time.time()))
        return {"ok":True}

    @app.post("/api/rooms/{run_id}/claim")
    async def claim_room(run_id: str, request: Request):
        same_origin(request)
        user=accounts.user(request,required=True)
        owner=accounts.owner(run_id)
        if owner and owner!=user['uid']:
            raise HTTPException(403,"这个牌桌已绑定其他账号")
        run=store.run(run_id)
        if not owner:
            check_owner(run,request)
            with store.db:
                store.db.execute("INSERT INTO room_owners VALUES(?,?,?)",(run_id,user['uid'],time.time()))
        return private_json({"run":public_run(run,store)})

    @app.post("/api/rooms/{run_id}/visit")
    async def visit_room(run_id: str, request: Request):
        same_origin(request)
        store.run(run_id)
        user=accounts.user(request,required=True)
        if accounts.owner(run_id)!=user['uid']:
            raise HTTPException(403,"你没有这个牌桌的操作权限")
        with store.db:
            store.db.execute("UPDATE room_owners SET visited=? WHERE run_id=?",(time.time(),run_id))
        return {"ok":True}

    @app.get("/api/rooms/{run_id}/state")
    async def player_state(run_id: str, request: Request):
        run=store.run(run_id)
        room_owner(run,request)
        from fastapi.responses import JSONResponse
        return JSONResponse({"run":public_run(run,store),**room_state(run,store)},
                            headers={"Cache-Control":"no-store"})

    @app.post("/api/rooms/{run_id}/action")
    async def player_action(run_id: str, body: HumanActionInput, request: Request):
        room_owner(store.run(run_id),request,write=True)
        runner.set_personal_keys(run_id,user_keys(request))
        try:
            runner.human_action(run_id,body.hand_number,body.action_count,body.action_id)
        except Exception:
            runner.personal_keys.pop(run_id,None)
            raise
        return {"ok":True}

    @app.post("/api/rooms/{run_id}/kick/{player_id}")
    async def kick_player(run_id: str, player_id: str, request: Request):
        room_owner(store.run(run_id),request,write=True)
        runner.kick(run_id,player_id)
        return {"ok":True}

    class SeriesInput(BaseModel):
        run_id: str
        target: int = Field(default=50,ge=1,le=1000)

    @app.get("/api/series")
    async def list_series():
        return [public_series(s,store) for s in store.all_series()]

    @app.post("/api/series",dependencies=[Depends(admin)])
    async def new_series(body: SeriesInput):
        return public_series(series_runner.create(body.run_id,body.target),store)

    @app.post("/api/series/{series_id}/start",dependencies=[Depends(admin)])
    async def start_series(series_id: str):
        series_runner.start(series_id)
        return {"ok":True}

    @app.post("/api/series/{series_id}/pause",dependencies=[Depends(admin)])
    async def pause_series(series_id: str):
        series_runner.pause(series_id)
        return {"ok":True}

    @app.post("/api/runs",dependencies=[Depends(admin)])
    async def new_run(cfg: RunConfig):
        if cfg.human_player_id:
            raise ValueError("真人牌桌请从自己组局创建")
        return public_run(runner.create(cfg),store)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str,request: Request):
        run=store.run(run_id)
        if run["config"].get("human_player_id"):room_owner(run,request)
        return public_run(run,store)

    @app.post("/api/runs/{run_id}/start")
    async def start(run_id: str, request: Request):
        run=store.run(run_id)
        if run["config"].get("human_player_id"):
            room_owner(run,request,write=True)
        else:admin(request)
        if run["config"].get("human_player_id"):
            runner.set_personal_keys(run_id,user_keys(request))
        if run.get("series_id"):
            series=store.series(run["series_id"])
            if run_id!=series["run_ids"][-1]:
                raise ValueError("请从当前场次继续系列赛")
            series_runner.start(series["id"])
        else:
            try:runner.start(run_id)
            except Exception:
                runner.personal_keys.pop(run_id,None)
                raise
        return {"ok":True}

    @app.post("/api/runs/{run_id}/pause")
    async def pause(run_id: str, request: Request):
        run=store.run(run_id)
        if run["config"].get("human_player_id"):
            room_owner(run,request,write=True)
        else:admin(request)
        if run.get("series_id"):
            series_runner.pause(run["series_id"])
        else:
            runner.pause(run_id)
        return {"ok":True}

    class LimitInput(BaseModel):
        max_hands: int = Field(ge=1,le=100000)

    @app.patch("/api/runs/{run_id}/limit")
    async def limit(run_id: str, body: LimitInput, request: Request):
        if store.run(run_id)["config"].get("human_player_id"):
            room_owner(store.run(run_id),request,write=True)
        else:admin(request)
        runner.update_limit(run_id,body.max_hands)
        return {"ok":True}

    @app.get("/api/runs/{run_id}/hands")
    async def list_hands(run_id: str,request: Request):
        run=store.run(run_id)
        if run["config"].get("human_player_id"):room_owner(run,request)
        return [{"number":h["spec"]["number"],"complete":h["complete"],"actions":len(h["actions"]),
                 "max_pot":max(e["snapshot"]["pot"] for e in h["events"])} for h in store.hands(run_id)]

    @app.get("/api/runs/{run_id}/hands/{number}")
    async def hand(run_id: str, number: int, request: Request, view: str="public", hero: str | None=None):
        record=store.hand(run_id,number)
        if store.run(run_id)["config"].get("human_player_id"):
            room_owner(store.run(run_id),request)
            return clean(room_record(record))
        return clean(observer_record(record,hero=hero if record["complete"] else None,omniscient=view=="omniscient"))

    @app.get("/api/runs/{run_id}/export",dependencies=[Depends(admin)])
    async def export(run_id: str, request: Request):
        run=store.run(run_id)
        if run["config"].get("human_player_id"):
            room_owner(run,request)
            return private_json({"run":public_run(run,store),"hands":[room_record(h,run["config"]["human_player_id"]) for h in store.hands(run_id)]})
        if run["status"]=="running" or run["active_hand"]:
            raise HTTPException(409,"只有无进行中手牌的比赛可导出完整回放")
        return {"run":public_run(run,store),"hands":[observer_record(h,omniscient=True) for h in store.hands(run_id)],
                "method":{"action_policy":"argmax","seat_shuffle":"after each completed dealer orbit",
                          "sng_objective":"rank only","thinking":"per_entry",
                          "thinking_by_entry":{e["id"]:e.get("reasoning_effort") or "provider_default" for e in run["config"]["entries"]}}}

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request):
        run=store.run(run_id)
        if run["config"].get("human_player_id"):room_owner(run,request)
        async def events():
            last=None
            while not await request.is_disconnected():
                body=json.dumps(public_run(store.run(run_id),store),ensure_ascii=False)
                if body!=last:
                    yield f"data: {body}\n\n"
                    last=body
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache"})

    @app.post("/api/advisor/preview")
    async def preview(body: AdvisorInput, request: Request):
        same_origin(request)
        hand=reconstruct(body)
        probabilities=await asyncio.to_thread(equity,hand,body.hero,body.samples)
        return {"state":hand.snapshot(hero=body.hero),"math":probabilities,
                "can_advise":hand.actor==body.hero and hand.state.status,"stack_assumption":hand.stack_assumption,
                "events":observer_record(hand.record(),hero=body.hero)["events"]}

    @app.post("/api/advisor/advise")
    async def advise(body: AdvisorInput, request: Request):
        same_origin(request);user=accounts.user(request,required=True)
        hand=reconstruct(body)
        if hand.actor!=body.hero:raise ValueError("当前还没轮到你行动；请补录前面的动作")
        entry=next((e for e in entries() if e.id==body.model_entry_id and selectable(e)),None)
        if not entry:raise ValueError("模型条目不存在")
        keys=user_keys(request)
        if body.billing_mode=='personal' and entry.provider in ('jev','deepseek') and not keys.get(entry.provider):
            raise HTTPException(422,'请填写自己的 API Key')
        if entry.provider=='deepseek' and not keys.get('deepseek') and not invited(store.db,user['uid']):
            raise HTTPException(403,'DeepSeek 需要邀请码或你自己的 API Key')
        observation=hand.request(entry.model)
        if hand.stack_assumption:observation['state']['stack_assumption']=hand.stack_assumption
        try:
            decision=await asyncio.wait_for(provider.call(entry,observation,decision_id='advisor:'+uuid.uuid4().hex,
                run_id='advisor/'+user['uid'],run_budget=settings.pokerbench_budget_cny,timeout=15,
                **({'key_override':keys[entry.provider]} if entry.provider in keys else {})),15)
        except TimeoutError:raise HTTPException(504,'模型超时，请稍后重试')
        return clean({'decision':decision,'state':hand.snapshot(hero=body.hero),'notice':'模型动作偏好，不是获胜概率'})

    @app.post("/v1/systemone",dependencies=[Depends(admin)])
    async def systemone(request: dict):
        if set(request)!={"model","state","questions"} or not isinstance(request["questions"],dict) or not request["questions"]:
            raise ValueError("需要 model、state、questions")
        if len(request["questions"])>16:
            raise ValueError("最多 16 个问题")
        for question in request["questions"].values():
            if not isinstance(question,dict) or question.get("type")!="choice" or not isinstance(question.get("criteria"),dict) or not 2<=len(question["criteria"])<=255:
                raise ValueError("PokerBench 动作网关支持 2–255 选项的 Choice")
        entry=next((e for e in entries() if e.id==request["model"]),None)
        if entry is None:
            entry=next((e for e in entries() if e.model==request["model"]),None)
        if entry is None:
            raise ValueError("未知模型条目")
        result=await provider.call(entry,request,decision_id="gateway:"+uuid.uuid4().hex,run_id="gateway",run_budget=settings.pokerbench_budget_cny)
        return {k:result[k] for k in ("model","answers","usage")}

    @app.get("/v1/models")
    async def model_list():
        return {"models":[{"name":e.id,"description":f"{e.name}: {e.model}","release_date":"2026-09-20"} for e in entries()]}

    dist=ROOT/"frontend"/"dist"
    if (dist/"assets").exists():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/")
    async def frontend():
        if not (dist/"index.html").exists():
            raise HTTPException(503,"请先构建前端：cd frontend && npm ci && npm run build")
        return FileResponse(dist/"index.html")

    return app


app=create_app()

import asyncio
import json
from copy import deepcopy
import random
import time
import uuid

from .config import RunConfig
from .engine import Hand
from .ev import attach_ev
from .provider import Provider, ProviderError
from .official_adapter import ADAPTER_METHOD
from .store import Store
from .public_access import clean
from .player_budget import PlayerBudgetExceeded, block, blocked, owner, paid, retire_paid
from .reporting import timing_summary, record_timing, hand_highlight, selected_highlights


class Runner:
    def __init__(self, store: Store, provider: Provider):
        self.store,self.provider=store,provider
        self.tasks: dict[str,asyncio.Task] = {}
        self.stop: set[str] = set()
        self.personal_keys: dict[str,dict[str,str]] = {}
        self.store.db.execute("CREATE TABLE IF NOT EXISTS room_kicks(run_id TEXT, player_id TEXT, PRIMARY KEY(run_id,player_id))")
        self.store.db.commit()
        for run in self.store.runs():
            if run["status"]=="running":
                run["status"]="paused"
                run["message"]="进程重启；从已保存的动作继续"
                self.store.save_run(run)

    def create(self, cfg: RunConfig, *, persist: bool = True):
        seats=[{"id":e.id,"name":e.name,"stack":cfg.buy_in if cfg.mode=="cash" else cfg.sng_starting_stack,
                "reserve":cfg.bankroll-cfg.buy_in if cfg.mode=="cash" else 0,
                "initial":cfg.bankroll if cfg.mode=="cash" else cfg.sng_starting_stack,
                "bought":cfg.buy_in if cfg.mode=="cash" else 0,"rebuys":0,"place":None} for e in cfg.entries]
        random.Random(cfg.seed).shuffle(seats)
        run={"id":uuid.uuid4().hex[:12],"config":cfg.model_dump(),"created":time.time(),
             "llm_adapter":deepcopy(ADAPTER_METHOD),"status":"ready","message":"尚未开始","hands_played":0,"active_hand":None,
             "seats":seats,"button":0,"orbit":1,"pending_dealers":[s["id"] for s in seats],
             "seat_history":[],"history":[],"events":[],"statistics":{},"champion":None,
             "timing_samples":{},"highlights":[]}
        run["history"].append(self.point(run))
        if persist:
            self.store.save_run(run)
        return run

    def point(self, run):
        return {"hand":run["hands_played"],"values":{s["id"]:s["stack"]+s["reserve"]-s["initial"] if run["config"]["mode"]=="cash" else s["stack"] for s in run["seats"]}}

    def set_personal_keys(self,run_id,keys):
        run=self.store.run(run_id)
        if run['config'].get('billing_mode')=='personal':
            active={s['id'] for s in run['seats'] if not s.get('retired')}
            if any(e['id'] in active and e['provider'] in ('jev','deepseek','openai_compatible') and not keys.get(e.get('credential_id') or e['provider']) for e in run['config']['entries']):
                raise ValueError('请在本浏览器重新填写自己的 API Key')
            self.personal_keys[run_id]=keys

    def start(self, run_id: str):
        run=self.store.run(run_id)
        self.set_personal_keys(run_id,self.personal_keys.get(run_id,{}))
        if run["config"]["mode"]=="cash" and any(s["reserve"]<0 and not s.get("retired") for s in run["seats"]):
            raise ValueError("本场含透支买入记录，请先处理历史赛果")
        if run["status"]=="complete":
            raise ValueError("比赛已经完成")
        if run_id in self.tasks and not self.tasks[run_id].done():
            return
        if run["hands_played"]>=run["config"]["max_hands"]:
            raise ValueError("已达到手数上限；请先提高上限")
        if any(e["provider"] in ("deepseek","openai_compatible") for e in run["config"]["entries"]) and run.get("llm_adapter")!=ADAPTER_METHOD:
            if run["hands_played"] or run["active_hand"]:
                raise ValueError("本场使用旧版接入；请新建比赛使用官方 adapter，避免混合不同评测方法")
            run["llm_adapter"]=deepcopy(ADAPTER_METHOD)
        self.stop.discard(run_id)
        run["status"],run["message"]="running","等待模型行动"
        self.store.save_run(run)
        self.tasks[run_id]=asyncio.create_task(self.play(run_id))

    def pause(self, run_id: str):
        self.stop.add(run_id)
        run=self.store.run(run_id)
        if run["status"] in ("waiting_human","waiting_next_hand"):
            run["status"],run["message"]="paused","比赛已暂停，可从记录继续"
            self.store.save_run(run)

    def human_action(self, run_id: str, number: int, action_count: int, action_id: str):
        run=self.store.run(run_id)
        self.set_personal_keys(run_id,self.personal_keys.get(run_id,{}))
        if run["status"]!="waiting_human" or run["active_hand"]!=number:
            raise ValueError("当前不是你的行动时间，请刷新牌桌")
        hand=Hand.restore(self.store.hand(run_id,number))
        if hand.actor!=run["config"].get("human_player_id") or len(hand.actions)!=action_count:
            raise ValueError("牌桌已更新，请重新选择动作")
        if action_id not in {a.id for a in hand.legal()}:
            raise ValueError("不是当前合法动作")
        hand.apply(action_id)
        if not hand.state.status:
            self.finish_hand(run,hand,RunConfig.model_validate(run["config"]))
            if run["status"]!="complete":
                if run["hands_played"]>=run["config"]["max_hands"]:
                    self.finish_limit(run)
                else:
                    run["status"],run["message"]="waiting_next_hand","本手结束，准备下一手"
            self.store.save_run(run,hand.record())
            self.personal_keys.pop(run_id,None)
        else:
            self.store.save_run(run,hand.record())
            self.start(run_id)

    def kick(self, run_id, player_id):
        run=self.store.run(run_id)
        if not run['config'].get('human_player_id') or player_id==run['config']['human_player_id']:
            raise ValueError("只能移除自己牌桌的模型")
        seat=next((s for s in run['seats'] if s['id']==player_id),None)
        if seat is None or seat.get('retired') or run['status']=='complete':
            raise ValueError("该席位已经离桌或牌局已结束")
        # Separate durable command: an in-flight decision cannot overwrite it.
        with self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO room_kicks VALUES(?,?)',(run_id,player_id))
        task=self.tasks.get(run_id)
        if not run['active_hand'] and (not task or task.done()):
            self.retire_kicked(run,RunConfig.model_validate(run['config']))
            self.store.save_run(run)

    def retire_kicked(self, run, cfg):
        if not cfg.human_player_id:return
        ids={r[0] for r in self.store.db.execute('SELECT player_id FROM room_kicks WHERE run_id=?',(run['id'],))}
        timed_out={e.id for e in cfg.entries if e.provider=='deepseek' and sum(v['type']=='model_timeout' and v.get('player')==e.id for v in run['events'])>=3}
        ids|=timed_out
        for seat in run['seats']:
            if seat['id'] not in ids or seat.get('retired'):continue
            if cfg.mode=='sng' and seat['place'] is None:
                seat['place']=sum(s['stack']>0 and not s.get('retired') for s in run['seats'])
            seat['reserve']+=seat['stack'];seat['stack']=0
            seat['retired']=True;seat['retired_reason']='timeout_limit' if seat['id'] in timed_out else 'host_kick'
            run['events'].append({'type':'host_kick','hand':run['hands_played'],'player':seat['id'],'label':'DeepSeek 累计超时 3 次，已离席' if seat['id'] in timed_out else '房主已移除模型'})
        remaining=[s for s in run['seats'] if not s.get('retired') and (s['stack']>0 or cfg.mode=='cash' and s['reserve']>=cfg.buy_in)]
        if ids and len(remaining)<2:
            run['status'],run['message']='complete','不足两名玩家，本局结束'
            if cfg.mode=='sng' and remaining:
                remaining[0]['place']=1;run['champion']=remaining[0]['id']

    def update_limit(self, run_id: str, max_hands: int):
        run=self.store.run(run_id)
        if run["status"]=="running":
            raise ValueError("请先暂停比赛")
        if max_hands<=run["hands_played"] or max_hands>100000:
            raise ValueError("新上限必须大于已完成手数，且不超过 100000")
        run["config"]["max_hands"]=max_hands
        if run["status"]=="hand_limit":
            run["status"]="paused"
        self.store.save_run(run)

    async def play(self, run_id: str):
        run=self.store.run(run_id)
        cfg=RunConfig.model_validate(run["config"])
        entries={e.id:e for e in cfg.entries}
        keys=self.personal_keys.get(run_id,{}) if cfg.billing_mode=="personal" else {}
        try:
            while run["hands_played"]<cfg.max_hands:
                if run_id in self.stop:
                    break
                if run["active_hand"]:
                    hand=Hand.restore(self.store.hand(run_id,run["active_hand"]))
                else:
                    if not self.prepare(run,cfg):
                        self.store.save_run(run)
                        return
                    hand=Hand(run["seats"],run["button"],cfg.blinds(run["hands_played"]),
                              cfg.seed+104729*(run["hands_played"]+1),run["hands_played"]+1,cfg.mode,chip_unit=50 if cfg.mode=="cash" else 1)
                    run["active_hand"]=hand.number
                    run["seat_history"].append({"hand":hand.number,"button":run["button"],"orbit":run["orbit"],
                                                "seats":[s["id"] for s in run["seats"]]})
                    self.store.save_run(run,hand.record())
                while hand.state.status:
                    if run_id in self.stop:
                        break
                    if not hand.actor:
                        raise ValueError("牌局没有合法行动者，已暂停检查")
                    if hand.actor==cfg.human_player_id:
                        run["status"],run["message"]="waiting_human","轮到你行动"
                        self.store.save_run(run,hand.record())
                        return
                    entry=entries[hand.actor]
                    did=f"{run_id}:{hand.number}:{len(hand.actions)}"
                    run["message"]=f"第 {hand.number} 手 · {entry.name} 决策中"
                    self.store.save_run(run)
                    request=hand.request(entry.model,run["statistics"])
                    if cfg.mode=="sng":
                        request["state"]["tournament"]={"level":min(run["hands_played"]//cfg.level_every+1,len(cfg.blind_levels)),
                            "hands_until_next_level":cfg.level_every-run["hands_played"]%cfg.level_every if run["hands_played"]//cfg.level_every<len(cfg.blind_levels)-1 else None,
                            "next_blinds":cfg.blinds((run["hands_played"]//cfg.level_every+1)*cfg.level_every),
                            "remaining_players":sum(s["stack"]>0 for s in run["seats"]),"prizes":None}
                    try:
                        user_id=owner(self.store.db,run_id) if cfg.human_player_id and cfg.billing_mode!="personal" and paid(entry) else None
                        if user_id and blocked(self.store.db,user_id):
                            raise PlayerBudgetExceeded(user_id)
                        timeouts=sum(e['type']=='model_timeout' and e.get('player')==entry.id for e in run['events'])
                        if cfg.human_player_id and entry.provider=='deepseek' and timeouts>=3:
                            hand.apply('check' if any(a.id=='check' for a in hand.legal()) else 'fold')
                            self.store.save_run(run,hand.record())
                            continue
                        pending=self.provider.call(entry,request,
                            decision_id=did,run_id=run_id,run_budget=cfg.run_budget_cny,
                            benchmark=not bool(cfg.human_player_id),
                            timeout=15 if cfg.human_player_id else cfg.decision_timeout,max_tokens=min(cfg.max_output_tokens,32768) if cfg.human_player_id else cfg.max_output_tokens,should_stop=lambda:run_id in self.stop,**({"key_override":keys[entry.credential_id or entry.provider]} if (entry.credential_id or entry.provider) in keys else {}))
                        decision=await asyncio.wait_for(pending,15) if cfg.human_player_id else await pending
                    except PlayerBudgetExceeded as exc:
                        block(self.store.db,exc.user_id)
                        # A dealt hand must finish legally; no more DeepSeek decisions.
                        # Check if free, otherwise fold; the host can remove the seat.
                        choices={a.id for a in hand.legal()}
                        hand.apply('check' if 'check' in choices else 'fold')
                        run['message']='DeepSeek 邀请额度不足，本次自动过牌或弃牌'
                        run['events'].append({'type':'model_error','hand':hand.number,'player':entry.id,'label':run['message']})
                        self.store.save_run(run,hand.record())
                        await asyncio.sleep(0)
                        continue
                    except (TimeoutError, ProviderError) as exc:
                        if not cfg.human_player_id:raise
                        if run_id in self.stop:break
                        action='check' if any(a.id=='check' for a in hand.legal()) else 'fold'
                        label=('模型超时（15 秒），自动过牌' if action=='check' else '模型超时（15 秒），自动弃牌') if isinstance(exc,TimeoutError) else ('模型暂不可用，自动过牌' if action=='check' else '模型暂不可用，自动弃牌')
                        hand.apply(action)
                        run['events'].append({'type':'model_timeout' if isinstance(exc,TimeoutError) else 'model_error','hand':hand.number,'player':entry.id,'label':label})
                        self.store.save_run(run,hand.record())
                        await asyncio.sleep(0)
                        continue
                    if run_id in self.stop:
                        break
                    hand.apply(decision["answers"]["action"]["choice"],decision)
                    record_timing(run,entry.id,decision,self.store.decision_attempt_count(run_id,did))
                    self.store.save_run(run,hand.record())
                    await asyncio.sleep(0)
                if hand.state.status:
                    break
                self.finish_hand(run,hand,cfg)
                self.store.save_run(run,hand.record())
                if run["status"]=="complete":
                    return
                if cfg.human_player_id and run["hands_played"]<cfg.max_hands:
                    run["status"],run["message"]="waiting_next_hand","本手结束，准备下一手"
                    self.store.save_run(run)
                    return
            if run_id in self.stop:
                run["status"],run["message"]="paused","已暂停，已完成动作不会重复执行"
            else:
                self.finish_limit(run)
            self.store.save_run(run)
        except asyncio.CancelledError:
            run["status"],run["message"]="paused","比赛已暂停，可从记录继续"
            self.store.save_run(run)
            raise
        except Exception as exc:
            run["status"],run["message"]="error",str(exc)[:300]
            self.store.save_run(run)

        finally:
            self.personal_keys.pop(run_id,None)

    def finish_limit(self, run):
        if run["config"]["mode"]=="sng":
            run["status"],run["message"]="hand_limit","达到试跑手数上限；SNG 尚未决出冠军"
        else:
            run["status"],run["message"]="complete","本次手数完成"
            ordered=rankings(run)
            run["champion"]=ordered[0]["id"] if ordered[0]["profit"]>ordered[1]["profit"] else None

    def prepare(self, run: dict, cfg: RunConfig):
        self.retire_kicked(run,cfg)
        # Existing fractional stacks: cash out only the remainder between hands.
        if cfg.mode=='cash':
            if 'chip_rule_start_hand' not in run:
                run['chip_rule_start_hand']=run['hands_played']+1
                run['events'].append({'type':'chip_unit','hand':run['hands_played']+1,'label':'最小筹码 0.5；历史零头保留在余额'})
            for seat in run['seats']:
                remainder=seat['stack']%50
                seat['stack']-=remainder;seat['reserve']+=remainder
        retire_paid(run,cfg,self.store.db)
        if run['status']=='complete':return False
        if run['seats'][run['button']].get('retired'):
            self.next_button(run,cfg)
        if cfg.mode=="cash":
            settle=run["hands_played"]>0 and run["hands_played"]%cfg.settle_every==0
            for seat in run["seats"]:
                if seat.get("retired"):
                    continue
                if settle:
                    seat["reserve"]+=seat["stack"]
                    seat["stack"]=0
                if seat["stack"]==0:
                    if seat["reserve"]<cfg.buy_in:
                        seat["retired"]=True
                        run["events"].append({"type":"cash_exit","hand":run["hands_played"],
                                              "player":seat["id"],"label":"资金不足买入，退出现金桌"})
                        continue
                    seat["reserve"]-=cfg.buy_in
                    seat["stack"]=cfg.buy_in
                    seat["bought"]+=cfg.buy_in
                    seat["rebuys"]+=int(not settle)
                    run["events"].append({"type":"cashout" if settle else "rebuy","hand":run["hands_played"],
                                          "player":seat["id"],"label":f"{cfg.settle_every} 手结码重新买入" if settle else "补码"})
            active={s["id"] for s in run["seats"] if not s.get("retired") and s["stack"]>0}
            run["pending_dealers"]=sorted(set(run["pending_dealers"]) & active)
            if len(active)<2:
                run["status"],run["message"]="complete","不足两名有资金的玩家，现金桌结束"
                rows=rankings(run)
                run["champion"]=rows[0]["id"] if rows[0]["profit"]>rows[1]["profit"] else None
                return False
            if run["seats"][run["button"]]["id"] not in active:
                self.next_button(run,cfg)
        return True

    def finish_hand(self, run: dict, hand: Hand, cfg: RunConfig):
        old={s["id"]:s["stack"] for s in run["seats"]}
        result={s["id"]:s for s in hand.snapshot(reveal=True)["seats"]}
        for seat in run["seats"]:
            seat["stack"]=result[seat["id"]]["stack"]
        if sum(s["stack"]+s["reserve"] for s in run["seats"])!=sum(s["initial"] for s in run["seats"]):
            raise ValueError("筹码守恒检查失败")
        run["hands_played"]+=1
        run["active_hand"]=None
        run["history"].append(self.point(run))
        hand.analysis=attach_ev(run,hand.record())
        involved=set(hand.ids)
        for pid in involved:
            stat=run["statistics"].setdefault(pid,{"hands":0,"vpip":0,"pfr":0,"three_bet":0})
            pre=[a for a in hand.actions if a["player"]==pid and a["street"]=="preflop"]
            stat["hands"]+=1
            stat["vpip"]+=int(any(a["pay"]>0 for a in pre))
            stat["pfr"]+=int(any(a["kind"]=="raise" for a in pre))
            stat["three_bet"]+=int(any(a.get("raise_number")==3 for a in pre))
        swings=sorted(((pid,result[pid]["stack"]-old[pid]) for pid in involved),key=lambda p:abs(p[1]),reverse=True)
        run["events"].append({"type":"swing","hand":hand.number,"player":swings[0][0],
                              "amount":swings[0][1],"label":"本手最大筹码变动"})
        if any(a["all_in"] for a in hand.actions):
            run["events"].append({"type":"all_in","hand":hand.number,"label":"全下对抗"})
        if cfg.mode=="sng":
            alive=[s for s in run["seats"] if s["stack"]>0]
            out=sorted([s for s in run["seats"] if old[s["id"]]>0 and s["stack"]==0],key=lambda s:old[s["id"]],reverse=True)
            previous=None
            for i,seat in enumerate(out):
                if old[seat["id"]]!=previous:
                    rank=len(alive)+i+1
                seat["place"]=rank
                previous=old[seat["id"]]
                run["events"].append({"type":"elimination","hand":hand.number,"player":seat["id"],"label":f"第 {rank} 名出局"})
            if len(alive)==1:
                alive[0]["place"]=1
                run["champion"]=alive[0]["id"]
                run["status"],run["message"]="complete","SNG 已决出冠军"
        if cfg.mode=="sng" and cfg.blinds(run["hands_played"])!=cfg.blinds(run["hands_played"]-1):
            run["events"].append({"type":"level","hand":hand.number,"label":"下手进入新盲注级别"})
        eliminated=[s["id"] for s in run["seats"] if cfg.mode=="sng" and old[s["id"]]>0 and s["stack"]==0]
        highlight=hand_highlight(hand,eliminated)
        if highlight:run.setdefault("highlights",[]).append(highlight)
        self.retire_kicked(run,cfg)
        self.next_button(run,cfg)

    def next_button(self, run: dict, cfg: RunConfig):
        dealer=run["seats"][run["button"]]["id"]
        active={s["id"] for s in run["seats"] if not s.get("retired") and (s["stack"]>0 or (cfg.mode=="cash" and s["reserve"]>=cfg.buy_in))}
        pending=set(run["pending_dealers"]) & active
        pending.discard(dealer)
        if not pending:
            run["orbit"]+=1
            pending=active.copy()
            if cfg.shuffle_each_orbit and len(active)>1:
                positions=[i for i,s in enumerate(run["seats"]) if s["id"] in active]
                shuffled=[run["seats"][i] for i in positions]
                random.Random(cfg.seed+run["orbit"]*32452843).shuffle(shuffled)
                for i,seat in zip(positions,shuffled):
                    run["seats"][i]=seat
                run["events"].append({"type":"shuffle","hand":run["hands_played"],"label":f"庄位轮转完成 · 第 {run['orbit']} 轮随机换座"})
        run["pending_dealers"]=sorted(pending)
        for distance in range(1,len(run["seats"])+1):
            pos=(run["button"]+distance)%len(run["seats"])
            if run["seats"][pos]["id"] in pending:
                run["button"]=pos
                break


def rankings(run: dict):
    cash=run["config"]["mode"]=="cash"
    rows=deepcopy(run["seats"])
    for row in rows:
        row["equity"]=row["stack"]+row["reserve"]
        row["profit"]=row["equity"]-row["initial"]
        row["bb100"]=row["profit"]/run["config"]["cash_big_blind"]*100/run["hands_played"] if cash and run["hands_played"] else None
    rows.sort(key=lambda s: -s["profit"] if cash else (s["place"] is not None,s["place"] or 0,-s["stack"]))
    prev=None
    for index,row in enumerate(rows):
        score=row["profit"] if cash else (row["place"],row["stack"] if row["place"] is None else 0)
        if score!=prev:
            rank=index+1
        row["rank"]=row["place"] or rank
        prev=score
    return rows


def public_run(run: dict, store: Store):
    cfg=run["config"]
    exposed={k:deepcopy(run[k]) for k in ("id","created","status","message","hands_played","active_hand","orbit","events","history","champion","seat_history")}
    exposed.update(timing=timing_summary(run),highlights=deepcopy(selected_highlights(run)),llm_adapter=deepcopy(run.get("llm_adapter",{"name":"legacy-custom"})),name=cfg["name"],mode=cfg["mode"],max_hands=cfg["max_hands"],
                   human_player_id=cfg.get("human_player_id"),billing_mode=cfg.get("billing_mode","hosted"),
                   series_id=run.get("series_id"),series_number=run.get("series_number"),
                   settle_every=cfg["settle_every"],buy_in=cfg["buy_in"],
                   ev_summary=run.get("ev_summary",{"covered_hands":0}),
                   small_blind=RunConfig.model_validate(cfg).blinds(run["hands_played"])[0],
                   big_blind=RunConfig.model_validate(cfg).blinds(run["hands_played"])[1],
                   proxy=any(e["proxy"] for e in cfg["entries"]),standings=rankings(run),
                   entries=[{**{k:e[k] for k in ("id","name","color","provider","model","proxy","revision")},"abstention_policy":e.get("abstention_policy","reject"),"reasoning_effort":e.get("reasoning_effort"),"reasoning_parameter":e.get("reasoning_parameter","reasoning_effort"),"credential_id":e.get("credential_id"),"expected_model":e.get("expected_model"),"expected_revision":e.get("expected_revision")} for e in cfg["entries"]],
                   provider_retries=sum(json.loads(c["body"]).get("attempt",1)>1 for c in store.calls(run["id"])),
                   cost_cny_estimate=store.spend(run["id"]),run_budget_cny=cfg["run_budget_cny"])
    if cfg.get('human_player_id'):
        exposed['pending_kicks']=[row[0] for row in store.db.execute('SELECT player_id FROM room_kicks WHERE run_id=?',(run['id'],))]
    exposed["chip_rule_start_hand"]=run.get("chip_rule_start_hand")
    return clean(exposed)

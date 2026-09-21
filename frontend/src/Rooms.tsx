import {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, FastForward, Play, Plus, Trash2, Users} from 'lucide-react';
import {api, Entry, GameEvent, money, Run, RoomInput, statusName} from './types';
import {PokerTable, actionText} from './Table';
import {t} from './i18n';
import {useRoomPlayback} from './useRoomPlayback';
import {PersonalAccess,PersonalAccessState,usePersonalAccess} from './PersonalAccess';
import {clearPersonalSession,Player,saveCustomAgent,removeCustomAgent,saveRoomAgentKey} from './personalSession';
import {BillingMode,modelAvailable,providerOf,Provider} from './modelAccess';
import {PersonalRequest} from './api';
import {CustomAgent} from './customAgents';

type RoomState={run:Run;record:{complete:boolean;events:GameEvent[]}|null;action_count:number;can_act:boolean};
const tokenKey=(id:string)=>`pokerbench-player-${id}`;
const seatToken=(id:string)=>localStorage.getItem(tokenKey(id))||'';
async function roomApi<T=any>(path:string,id:string,method='GET',body?:unknown,personal?:PersonalRequest):Promise<T>{
    return api<T>(path,method,body,{'X-Player-Token':seatToken(id)},personal);
}
export function Rooms({runs,onUpdate}:{runs:Run[];onUpdate:(run:Run)=>void}) {
    const access=usePersonalAccess();
    return <><PersonalAccess access={access}/>{access.user&&<PlayerRooms key={access.user.id} user={access.user} access={access} runs={runs} onUpdate={onUpdate}/>}</>;
}
function CustomAgents({access,onAdded}:{access:PersonalAccessState;onAdded:(id:string)=>void}){
    const blank=()=>({id:'',name:'',endpoint:'',model:''});
    const [draft,setDraft]=useState<CustomAgent>(blank),[key,setKey]=useState(''),[error,setError]=useState('');
    const save=()=>{
        setError('');
        try{
            const agent={id:draft.id||`custom-${crypto.randomUUID().slice(0,8)}`,name:draft.name.trim()||draft.model.trim().slice(0,60),endpoint:draft.endpoint.trim(),model:draft.model.trim()};
            saveCustomAgent(agent,key.trim());setDraft(blank());setKey('');if(!draft.id)onAdded(agent.id);
        }catch(e){setError((e as Error).message);}
    };
    return <section className="custom-agents" aria-label={t('自己的 Agent')}><h3>{t('添加自己的 Agent')}</h3><p className="fine">{t('Jev Official 只需在上方填写密钥。其他兼容 OpenAI 的模型填写密钥、公开 HTTPS 端点和模型名称；保存后可重复添加席位。')}</p>
      <form onSubmit={e=>{e.preventDefault();save();}}><div className="form-grid">
        <label>{t('Agent 显示名称')}<input maxLength={60} value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/></label>
        <label>{t('模型名称')}<input required maxLength={160} readOnly={!!draft.id} value={draft.model} onChange={e=>setDraft({...draft,model:e.target.value})}/></label>
        <label>{t('公开 HTTPS 端点')}<input type="url" required maxLength={2048} readOnly={!!draft.id} placeholder="https://api.provider.com/v1" value={draft.endpoint} onChange={e=>setDraft({...draft,endpoint:e.target.value})}/></label>
        <label>{t('Agent API 密钥')}<input type="password" required autoComplete="off" spellCheck={false} maxLength={1024} value={key} onChange={e=>setKey(e.target.value)}/></label>
      </div><p className="fine">{t('密钥仅保留在当前账号的浏览器会话中；牌局会保存模型与端点设置。请先设置服务商消费上限。')}</p><div className="inline-controls"><button className="secondary" disabled={!draft.id&&Object.keys(access.agentKeys).length>=9}>{t(draft.id?'更新 Agent 密钥':'保存 Agent')}</button>{draft.id&&<button className="text-button" type="button" onClick={()=>{setDraft(blank());setKey('');setError('');}}>{t('取消')}</button>}</div></form>
      {error&&<p className="notice error" role="alert">{t(error)}</p>}
      <div className="saved-agents">{access.agents.map(agent=><div key={agent.id}><span><strong>{agent.name}</strong><small>{agent.model} · {new URL(agent.endpoint).hostname}</small></span><button className="secondary" onClick={()=>{setDraft(agent);setKey('');setError('');}}>{t('更新密钥')}</button><button className="text-button" onClick={()=>removeCustomAgent(agent.id)}>{t('移除')}</button></div>)}</div>
    </section>;
}
function RoomAgentKeys({entries,access}:{entries:Entry[];access:PersonalAccessState}){
    const [draft,setDraft]=useState<Record<string,string>>({}),[message,setMessage]=useState('');
    const unique=entries.filter((entry,index)=>entries.findIndex(e=>e.credential_id===entry.credential_id)===index);
    return <form className="custom-agents" onSubmit={e=>{e.preventDefault();try{for(const entry of unique){const id=entry.credential_id!;if(draft[id])saveRoomAgentKey(id,draft[id].trim());}setDraft({});setMessage('牌局 Agent 密钥已保存。');}catch(error){setMessage((error as Error).message);}}}><h3>{t('此牌局的 Agent 密钥')}</h3><p className="fine">{t('请为已保存的牌局重新输入密钥；模型与端点保持创建时的设置。')}</p><div className="form-grid">{unique.map(entry=><label key={entry.credential_id}>{t('{0} 的个人密钥',entry.name)}<input type="password" required={!access.agentKeys[entry.credential_id!]} autoComplete="off" maxLength={1024} value={draft[entry.credential_id!]||''} onChange={e=>setDraft({...draft,[entry.credential_id!]:e.target.value})}/></label>)}</div><button className="secondary">{t('保存牌局 Agent 密钥')}</button>{message&&<p className="fine" role="status">{t(message)}</p>}</form>;
}
function PlayerRooms({user,access,runs,onUpdate}:{user:Player;access:PersonalAccessState;runs:Run[];onUpdate:(run:Run)=>void}) {
    const [models,setModels]=useState<Entry[]>([]),[selected,setSelected]=useState<string[]>([]);
    const [name,setName]=useState('My table');
    const [recentRooms,setRecentRooms]=useState<Run[]>([]);
    const [legacy,setLegacy]=useState(()=>runs.filter(r=>r.human_player_id&&seatToken(r.id)));
    const [mode,setMode]=useState<'cash'|'sng'>('cash'),[thinking,setThinking]=useState('low');
    const [limit,setLimit]=useState(5000),[busy,setBusy]=useState(false),[error,setError]=useState('');
    const [current,setCurrent]=useState(()=>location.hash.split('/')[1]||'');
    const [data,setData]=useState<RoomState|null>(null),[creating,setCreating]=useState(!current);
    const tableRef=useRef<HTMLDivElement>(null);
    const updateRef=useRef(onUpdate);updateRef.current=onUpdate;
    const [billingMode,setBillingMode]=useState<BillingMode>('hosted');
    const customEntries:Entry[]=access.agents.filter(a=>!!access.agentKeys[a.id]).map(a=>({id:a.id,name:a.name,model:a.model,provider:'openai_compatible',credential_id:a.id,color:'#9a753a',ready:true}));
    const available=[...models.filter(e=>modelAvailable(e,billingMode,access.budget,access.keys)),...(billingMode==='personal'?customEntries:[])];
    const availableIds=available.map(e=>e.id).join(',');
    useEffect(()=>{setSelected(old=>{const valid=old.filter(id=>available.some(e=>e.id===id));return valid.length?valid:available.length?[available.find(e=>e.provider==='jev')?.id||available[0].id]:[];});},[availableIds]);
    const refreshRooms=async()=>{const rows=await api<Run[]>('/api/rooms/mine');setRecentRooms(rows);};
    const updateRoom=(run:Run)=>{updateRef.current(run);setRecentRooms(old=>old.map(r=>r.id===run.id?run:r));};
    useEffect(()=>{void refreshRooms().catch(e=>setError(e.message));},[]);
    useEffect(()=>{api<Entry[]>('/api/rooms/models').then(setModels).catch(e=>setError(e.message));},[]);
    useEffect(()=>{
        const changed=()=>{if(location.hash.startsWith('#rooms')){const id=location.hash.split('/')[1]||'';setCurrent(id);setCreating(!id);}};
        window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed);
    },[]);
    useEffect(()=>{
        if(!current||creating)return;
        let active=true,pending=false;
        setData(null);setError('');
        const load=async()=>{if(pending)return;pending=true;try{
            const next=await roomApi<RoomState>(`/api/rooms/${current}/state`,current);
            if(active){setData(next);updateRoom(next.run);}
        }catch(e){if(active){if((e as Error&{status?:number}).status===401)clearPersonalSession();else setError((e as Error).message);}}finally{pending=false;}};
        void load();const timer=setInterval(load,500);return()=>{active=false;clearInterval(timer);};
    },[current,creating]);
    const choose=(id:string)=>{setError('');setCurrent(id);setCreating(false);location.hash=`rooms/${id}`;void api(`/api/rooms/${id}/visit`,'POST',{}).then(refreshRooms).catch(e=>setError(e.message));};
    const claim=async(id:string)=>{
        setBusy(true);setError('');
        try{await roomApi(`/api/rooms/${id}/claim`,id,'POST',{});localStorage.removeItem(tokenKey(id));setLegacy(old=>old.filter(r=>r.id!==id));choose(id);}
        catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    const remove=async(room:Run)=>{
        if(busy||!window.confirm(t('删除“{0}”？牌局将从列表移除，已产生的费用不会退回。',room.name)))return;
        setBusy(true);setError('');
        try{
            await roomApi(`/api/rooms/${room.id}`,room.id,'DELETE');
            localStorage.removeItem(tokenKey(room.id));
            setRecentRooms(old=>old.filter(r=>r.id!==room.id));
            if(current===room.id){setCurrent('');setData(null);setCreating(true);location.hash='rooms';}
            await refreshRooms();
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    const create=async()=>{
        if(busy||!selected.length||selected.some(id=>!available.some(e=>e.id===id)))return;
        setBusy(true);setError('');
        try{
            const input:RoomInput={name,player_name:user.id,mode,model_ids:selected,deepseek_thinking:thinking,max_hands:limit,run_budget_cny:20,billing_mode:billingMode,custom_agents:access.agents.filter(a=>selected.includes(a.id))};
            const providers=available.filter(e=>selected.includes(e.id)).map(providerOf).filter((p):p is Provider=>p!==null);
            const result=await api<{run:Run}>('/api/rooms','POST',input,{}, {billingMode,providers,agentIds:input.custom_agents?.map(a=>a.id)});
            onUpdate(result.run);choose(result.run.id);
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    const kick=async(playerId:string)=>{
        if(busy)return;setBusy(true);setError('');
        try{
            await roomApi(`/api/rooms/${current}/kick/${playerId}`,current,'POST',{});
            const next=await roomApi<RoomState>(`/api/rooms/${current}/state`,current);setData(next);updateRoom(next.run);void access.refreshBudget();
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    const act=async(kind:string,actionId?:string)=>{
        if(!data||busy||(kind!=='pause'&&(playback.behind||missingRunKeys)))return;setBusy(true);setError('');
        try{
            if(kind==='action')await roomApi(`/api/rooms/${current}/action`,current,'POST',{hand_number:data.run.active_hand,action_count:data.action_count,action_id:actionId},runPersonal);
            else await roomApi(`/api/runs/${current}/${kind}`,current,'POST',{},kind==='start'?runPersonal:undefined);
            const next=await roomApi<RoomState>(`/api/rooms/${current}/state`,current);setData(next);updateRoom(next.run);void access.refreshBudget();
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    const run=data?.run.id===current?data.run:undefined;
    const runProviders=(run?.entries||[]).filter(e=>e.id!==run?.human_player_id&&!run?.standings.find(s=>s.id===e.id)?.retired).map(providerOf).filter((p):p is Provider=>p!==null);
    const runAgentIds=(run?.entries||[]).filter(e=>e.credential_id&&!run?.standings.find(s=>s.id===e.id)?.retired).map(e=>e.credential_id!);
    const runPersonal:PersonalRequest={billingMode:run?.billing_mode||'hosted',providers:runProviders,agentIds:runAgentIds};
    const missingRunKeys=run?.billing_mode==='personal'&&(runProviders.some(p=>p!=='systemone'&&!access.keys[p])||runAgentIds.some(id=>!access.agentKeys[id]));
    const playback=useRoomPlayback(current,run?data?.record?.events||[]:[]);
    const events=playback.events,event=events.at(-1),state=event?.snapshot;
    const hasTable=!creating&&!!state;
    useEffect(()=>{const table=tableRef.current;if(table)table.scrollLeft=(table.scrollWidth-table.clientWidth)/2;},[current,hasTable]);
    const canAct=!missingRunKeys&&!!data?.can_act&&!playback.behind&&state?.actor===run?.human_player_id;
    const shownHands=state&&!state.complete?state.hand_number-1:run?.hands_played;
    const recent:Record<string,NonNullable<GameEvent['action']>>={};for(const e of events)if(e.action)recent[e.action.player]=e.action;
    return <section className="rooms-page"><div className="page-heading"><div><p className="eyebrow">{t('YOUR SEAT AT THE TABLE')}</p><h1>{t('自己组局')}</h1><p className="muted">{t('你坐一席，自选模型对手。现金桌或 SNG，同一套德州扑克规则。')}</p></div>{!creating&&<button className="secondary" onClick={()=>{setCreating(true);location.hash='rooms';}}><Plus size={16}/>{t('组一个新局')}</button>}</div>
    {legacy.length>0&&<details className="legacy-rooms"><summary>{t('导入此浏览器的旧牌局')}</summary><p className="fine">{t('绑定后，可用当前账号在其他浏览器继续；原有牌局进度不变。')}</p>{legacy.map(r=><div key={r.id}><span>{r.name} · {r.id.slice(-6)}</span><button className="secondary" disabled={busy} onClick={()=>void claim(r.id)}>{t('绑定到我的账号')}</button></div>)}</details>}
    {recentRooms.length>0&&<section className="recent-rooms" aria-label={t('我的牌局')}><h3>{t('我的牌局')}</h3><div>{recentRooms.map(r=><article className="room-card" key={r.id}><button className={!creating&&r.id===current?'selected':''} onClick={()=>choose(r.id)}><strong>{r.name}</strong><span>{t(r.mode==='cash'?'现金桌':'SNG 锦标赛')} · {t('已完成 {0} 手',r.hands_played)} · {r.entries.length} {t('席')}</span><small>{t(statusName[r.status]||r.status)} · {r.id.slice(-6)}</small><b>{t(r.status==='complete'?'查看牌局':'继续对局')} <ArrowUpRight size={14}/></b></button><button className="room-delete" disabled={busy||r.status==='running'} title={t(r.status==='running'?'请先暂停牌局再删除':'删除牌局')} onClick={()=>void remove(r)}><Trash2 size={13}/>{t('删除牌局')}</button></article>)}</div></section>}
    {error&&<p className="notice error" role="alert">{t(error)}</p>}
    {creating&&<p className="notice personal-key-note">{t('支持自备 API 密钥，包括 Jev Official 和 DeepSeek。建议先在服务商设置消费上限。')} {t('仅在当前浏览器会话保存；经服务器安全发送至服务商。费用由你的服务商收取。')}</p>}
    <CustomAgents access={access} onAdded={id=>{if(creating){setBillingMode('personal');setSelected(old=>[...old.filter(existing=>access.agents.some(a=>a.id===existing)||models.some(e=>e.id===existing&&modelAvailable(e,'personal',access.budget,access.keys))),id].slice(0,9));}}}/>
    {creating&&<label className="billing-mode">{t('调用方式')}<select aria-label={t("调用方式")} value={billingMode} onChange={e=>setBillingMode(e.target.value as BillingMode)}><option value="hosted">{t('托管服务')}</option><option value="personal">{t('个人密钥')}</option></select><small>{t(billingMode==='personal'?'使用个人密钥时，调用费用由你的模型服务商账户承担。':'托管 DeepSeek 需要邀请且账户仍有余额。')}</small></label>}
    {creating&&available.length===0&&<p className="notice">{t(billingMode==='personal'?'请先保存对应的 Jev 或 DeepSeek 个人密钥。':'暂无可用模型，请稍后重试。')}</p>}
    {runAgentIds.length>0&&<RoomAgentKeys key={current} entries={run!.entries.filter(e=>e.credential_id&&!run!.standings.find(s=>s.id===e.id)?.retired)} access={access}/>}
    {missingRunKeys&&<p className="notice">{t('此牌局使用个人密钥，请补充所有在席模型的密钥以继续。')}</p>}
    {creating?<form className="room-setup" onSubmit={e=>{e.preventDefault();void create();}}><div className="room-models"><div className="section-head"><h2>{t('选择模型对手')}</h2><span className="tag">{t('已选 {0} / 9',selected.length)}</span></div><p className="fine">{t('选择 1–9 个模型席位，同一模型可重复添加；加上你共 2–10 人。托管 GPT 不开放，可使用自己的兼容 Agent。')}</p><div className="model-picker">{available.map(e=>{const count=selected.filter(id=>id===e.id).length;return <div key={e.id} className={`model-option ${count?'selected':''}`}><span><strong><i style={{background:e.color}}/>{e.name}</strong><small>{e.model||e.name}</small></span><div className="model-count"><button type="button" aria-label={t('减少 {0} 席位',e.name)} disabled={!count} onClick={()=>setSelected(old=>old.filter((_,i)=>i!==old.lastIndexOf(e.id)))}>−</button><output aria-label={t('{0} 席位数',e.name)}>{count}</output><button type="button" aria-label={t('增加 {0} 席位',e.name)} disabled={selected.length>=9} onClick={()=>setSelected(old=>[...old,e.id])}>+</button></div></div>;})}</div>
    {available.some(e=>selected.includes(e.id)&&e.provider==='deepseek')&&<label className="room-thinking">{t('DeepSeek 思考模式')}<select value={thinking} onChange={e=>setThinking(e.target.value)}><option value="low">Low</option><option value="non-thinking">Non-thinking</option></select><small>{t('仅影响本局 DeepSeek，不修改自动比赛或模型列表中的配置。')}</small></label>}</div>
    <aside className="room-options"><div className="form-grid"><label>{t('牌桌名称')}<input required maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label></div><fieldset className="room-formats"><legend>{t('选择赛制')}</legend>{(['cash','sng'] as const).map(format=><label key={format} className={mode===format?'selected':''}><input type="radio" name="room-format" checked={mode===format} onChange={()=>{setMode(format);setLimit(format==='cash'?5000:10000);}}/><span><strong>{format==='cash'?t('现金桌'):t('SNG 锦标赛')}</strong><small>{format==='cash'?t('总资金 10,000，买入 200，盲注 0.5/1。每 500 手结码，买入资金不足则退出。'):t('起始 20,000 筹码，每 200 手升盲，共 15 档；淘汰至一人，按最终名次排名。')}</small></span></label>)}</fieldset><p className="fine">{t('每个完整庄位轮次后随机换座。每手结束后由你开始下一手。')} {t('现金桌最小筹码 0.5。')}</p><div className="form-grid"><label>{t('手数上限')}<input type="number" min={1} max={100000} required value={limit} onChange={e=>setLimit(+e.target.value)}/></label></div><p className="fine">{t('自组局使用虚拟筹码，结果不进入正式总榜。个人额度见上方账户信息。')}</p><button type="submit" className="primary full" disabled={busy||selected.length<1}>{busy?t('创建中…'):t('创建牌桌并入座')}<ArrowUpRight size={16}/></button></aside></form>:run?<><div className="run-toolbar"><div><h2>{run.name}</h2><p className="fine">{t(run.mode==='cash'?'现金桌':'SNG 锦标赛')} · {shownHands} / {run.max_hands} {t('HAND')} · {t(run.billing_mode==='personal'?'个人密钥':'托管服务')}</p></div><div className="inline-controls"><span className={`status ${run.status==='running'?'live':''}`}>{playback.behind?t('正在展示牌桌动作'):t(statusName[run.status]||run.status)}</span>{run.status!=='running'&&run.status!=='waiting_human'&&run.status!=='complete'&&run.status!=='waiting_next_hand'&&run.hands_played<run.max_hands&&<button className="primary" disabled={busy||playback.behind||missingRunKeys} onClick={()=>act('start')}>{t(run.status==='ready'?'开始比赛':'继续')}</button>}{(run.status==='running'||run.status==='waiting_human')&&<button className="secondary" disabled={busy} onClick={()=>act('pause')}>{t('暂停')}</button>}</div></div>
    {run.mode==='cash'&&<p className="fine chip-history-note">{t("历史实际盈亏保持原值；新下注以 0.5 为单位，权益调整值可能含小数。")} {run.chip_rule_start_hand&&t("从第 {0} 手起采用 0.5 下注单位。",run.chip_rule_start_hand)}</p>}
    <div className="room-playback-controls"><p className="room-status" role="status">{playback.behind?t('正在展示牌桌动作'):t(run.message)}</p><button className={playback.fast?'primary':'secondary'} aria-pressed={playback.fast} onClick={playback.toggle}>{playback.fast?<Play size={15}/>:<FastForward size={15}/>}<span>{t(playback.fast?'恢复正常速度':'快进')}</span></button><span className="fine">{t(playback.fast?'最快速度 · 实时跟随':'正常速度 · 1 秒一个动作')}</span></div>{state?<><div className="room-current-action" key={`${state.hand_number}-${event?.index}`} aria-live="polite"><strong>{event?.action?run.entries.find(e=>e.id===event.action?.player)?.name:t('牌桌事件')}</strong><span>{event?.action?actionText(event.action,run.mode):t(event?.label||'')}</span></div><div className="table-stage room-table" ref={tableRef}><PokerTable soundScope={run.id} state={state} entries={run.entries} mode={run.mode} event={event} recentActions={recent} hero={run.human_player_id||undefined} centerAction={run.status==='waiting_next_hand'&&!playback.behind&&<button className="primary next-hand-button" disabled={busy||missingRunKeys} onClick={()=>act('start')}>{t('下一手')}<ArrowUpRight size={17}/></button>}/></div><div className="human-actions"><div><h3>{canAct?t('轮到你行动'):playback.behind?t('正在展示牌桌动作'):state.complete?t('本手已结算'):t('等待模型行动')}</h3><p className="fine">{canAct?t('选择一个合法动作。加注金额表示本轮总下注额。'):t('对局中只显示你的底牌和公开信息。')}</p></div><div className="action-buttons">{canAct&&state.legal_actions.map(a=><button className={a.all_in?'primary':'secondary'} key={a.id} disabled={busy} onClick={()=>act('action',a.id)}>{t(a.label)}{a.kind==='raise'?' → '+money(a.to,run.mode):a.kind==='call'?' '+money(a.pay,run.mode):''}</button>)}</div></div><div className="room-history"><h3>{t('本手行动记录')}</h3>{events.filter(e=>e.action||e.type==='board'||e.type==='settlement').slice().reverse().map(e=><p key={e.index}><span>{e.action?run.entries.find(p=>p.id===e.action?.player)?.name:t('牌桌事件')}</span><strong>{e.action?actionText(e.action,run.mode):t(e.label)}</strong></p>)}</div></>:<div className="empty-state"><Users size={30}/><p>{t('席位已就绪，点击开始比赛发牌。')}</p></div>}
    <div className="room-model-controls"><h3>{t('管理模型席位')}</h3><p className="fine">{t('超时会自动过牌或弃牌。DeepSeek 在本局累计超时 3 次后离桌；其他模型由房主手动移除。移除在本手结算后生效。')}</p><div className="inline-controls">{run.entries.filter(e=>e.id!==run.human_player_id).map(e=>{const retired=run.standings.find(s=>s.id===e.id)?.retired;const pending=run.pending_kicks?.includes(e.id);return <button className="secondary" key={e.id} disabled={busy||retired||pending||run.status==='complete'} onClick={()=>void kick(e.id)}>{e.name} · {t(retired?(run.standings.find(s=>s.id===e.id)?.retired_reason==='timeout_limit'?'DeepSeek 已累计超时 3 次 · 已离桌':'已离桌'):pending?'本手结束后离桌':'移除')}</button>;})}</div></div>
    <div role="status">{run.events.filter(e=>e.type==='model_timeout'||e.type==='model_error').slice(-3).map((e,i)=><p className="notice" key={i}>{t('第 {0} 手',e.hand)} · {run.entries.find(p=>p.id===e.player)?.name} · {t(e.label)}</p>)}</div>
    {!playback.behind&&<div className="scroll-table room-standings"><table><thead><tr><th>#</th><th>{t('参赛席位')}</th><th>{t(run.mode==='cash'?'净盈利':'筹码')}</th><th>{t('状态')}</th></tr></thead><tbody>{run.standings.map(s=><tr key={s.id}><td>{s.rank}</td><td>{s.id===run.human_player_id?t('我'):s.name}</td><td>{money(run.mode==='cash'?s.profit:s.stack,run.mode)}</td><td>{s.retired?t(s.retired_reason==='host_kick'?'房主已移除模型':s.retired_reason==='timeout_limit'?'DeepSeek 已累计超时 3 次 · 已离桌':s.retired_reason==='account_budget'?'API 额度用尽 · 已退出':'资金不足 · 已退出'):s.place?t('最终第 {0} 名',s.place):'—'}</td></tr>)}</tbody></table></div>}</>:<div className="empty-state">{t('正在加载牌桌…')}</div>}
    </section>;
}

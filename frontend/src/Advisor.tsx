import {t} from './i18n';
import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2, Pencil, RotateCw } from 'lucide-react';
import { api, Decision, Entry, money, State, streetName } from './types';
import { PokerTable } from './Table';
import {PersonalAccess,usePersonalAccess} from './PersonalAccess';
import {BillingMode,modelAvailable,providerOf} from './modelAccess';
type EventInput = {
    id: string;
    kind: string;
    player?: string;
    amount?: number;
    cards?: string[];
    time?: string;
};
const fresh = (n = 6) => ({ mode: 'cash', seats: Array.from({ length: n }, (_, i) => ({ id: `p${i}`, name: `玩家 ${i + 1}`, stack: 20000 })), button: 0, hero: n > 3 ? 'p3' : 'p0', hole_cards: ['As', 'Ks'], small_blind: 50, big_blind: 100, ante: 0, events: [] as EventInput[], samples: 3000, model_entry_id: 'jev', billing_mode:'hosted' as BillingMode, stack_mode: 'assumed', assumed_stack_bb: 100 });
function positionAt(i: number, button: number, n: number) {
    const offset = (i - button + n) % n;
    if (n === 2)
        return offset === 0 ? 'BTN / SB' : 'BB';
    if (offset === 0)
        return 'BTN';
    if (offset === 1)
        return 'SB';
    if (offset === 2)
        return 'BB';
    if (offset === 3)
        return 'UTG';
    if (offset === n - 1)
        return 'CO';
    if (offset === n - 2)
        return 'HJ';
    return `UTG+${offset - 3}`;
}
export function Advisor() {
    const access=usePersonalAccess();
    const [entries,setEntries]=useState<Entry[]>([]);
    useEffect(()=>{api<Entry[]>('/api/rooms/models').then(setEntries).catch(e=>setError(e.message));},[]);
    const [input, setInput] = useState(fresh()), [preview, setPreview] = useState<any>(null), [decision, setDecision] = useState<Decision | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(''), [dirty, setDirty] = useState(true);
    const available=entries.filter(e=>modelAvailable(e,input.billing_mode,access.budget,access.keys));
    const availableIds=available.map(e=>e.id).join(',');
    const [editing, setEditing] = useState<number | null>(null), [seatTarget, setSeatTarget] = useState<'hero' | 'button'>('hero');
    const version = useRef(0);
    const [insert, setInsert] = useState(-1), [eventKind, setEventKind] = useState('call'), [player, setPlayer] = useState('p3'), [amount, setAmount] = useState(300), [cards, setCards] = useState('Ah 7c 2d'), [time, setTime] = useState('');
    const edit = (next: typeof input) => { version.current++; setInput(next); setDirty(true); setDecision(null); setError(''); };
    useEffect(()=>{if(available.length&&!available.some(e=>e.id===input.model_entry_id))edit({...input,model_entry_id:available.find(e=>e.provider==='jev')?.id||available[0].id});},[availableIds]);
    const inspect = async (data = input) => { const current = version.current; setBusy('检查牌局'); setError(''); try {
        const result = await api('/api/advisor/preview', 'POST', data);
        if (current !== version.current)
            return;
        setPreview(result);
        setDirty(false);
        if (result.state.actor)
            setPlayer(result.state.actor);
    }
    catch (e) {
        if (current === version.current) {
            setPreview(null);
            setError((e as Error).message);
        }
    }
    finally {
        if (current === version.current)
            setBusy('');
    } };
    useEffect(() => { const timer = setTimeout(() => void inspect(input), 350); return () => clearTimeout(timer); }, [input]);
    const resize = (n: number) => { setEditing(null); setInsert(-1); const seats = Array.from({ length: n }, (_, i) => input.seats[i] || { id: `p${i}`, name: `玩家 ${i + 1}`, stack: 20000 }); edit({ ...input, seats, button: input.button % n, hero: seats.some(s => s.id === input.hero) ? input.hero : seats[0].id }); };
    const pickSeat = (i: number) => { setEditing(null); setInsert(-1); edit(seatTarget === 'hero' ? { ...input, hero: input.seats[i].id } : { ...input, button: i }); };
    const addEvent = () => { const item: EventInput = { id: editing === null ? crypto.randomUUID().slice(0, 8) : input.events[editing].id, kind: eventKind, ...(time ? { time } : {}), ...(eventKind === 'board' ? { cards: cards.trim().split(/[\s,]+/) } : { player, ...(eventKind === 'raise' ? { amount } : {}) }) }; const events = [...input.events]; if (editing !== null)
        events[editing] = item;
    else
        events.splice(insert < 0 ? events.length : insert, 0, item); edit({ ...input, events }); setInsert(-1); setEditing(null); };
    const move = (i: number, delta: number) => { setEditing(null); const events = [...input.events]; if (i + delta < 0 || i + delta >= events.length)
        return; [events[i], events[i + delta]] = [events[i + delta], events[i]]; edit({ ...input, events }); };
    const ask = async () => {
        const model=available.find(e=>e.id===input.model_entry_id),provider=model&&providerOf(model);
        if(!access.user||!model||!provider||busy)return;
        const current = version.current; setBusy('模型正在判断'); setError(''); try {
        const r = await api('/api/advisor/advise', 'POST', input, {}, {billingMode:input.billing_mode,providers:[provider]});
        if (current === version.current)
            setDecision(r.decision);
    }
    catch (e) {
        if (current === version.current)
            setError((e as Error).message);
    }
    finally {
        if (current === version.current)
            setBusy('');
        void access.refreshBudget();
    } };
    const s: State | undefined = preview?.state;
    return <section><PersonalAccess access={access}/><div className="section-head"><div><p className="eyebrow">{t("HAND LAB / 第二阶段预览")}</p><h1>{t("把一手牌，看清楚。")}</h1><p className="muted">{t("录入牌局、检验顺序，再查看数学权益与模型建议。")}</p></div><span className="tag">{t("默认建议模型：Jev")}</span></div>
    <fieldset className="advisor-fieldset" disabled={busy === '模型正在判断'}><div className="quick-table-setup"><div className="quick-inputs"><p className="eyebrow">{t("从这里开始")}</p><h3>{t("选位置，填两张牌。")}</h3><div className="form-grid"><label>{t("人数")}<select value={input.seats.length} onChange={e => resize(+e.target.value)}>{Array.from({ length: 9 }, (_, i) => <option key={i} value={i + 2}>{i + 2}{t("人")}</option>)}</select></label><label>{t("赛制")}<select value={input.mode} onChange={e => edit({ ...input, mode: e.target.value })}><option value="cash">{t("现金桌")}</option><option value="sng">SNG</option></select></label><label>{t("底牌 1")}<input aria-label={t("底牌1")} value={input.hole_cards[0]} maxLength={2} onChange={e => edit({ ...input, hole_cards: [e.target.value.slice(0, 1).toUpperCase() + e.target.value.slice(1).toLowerCase(), input.hole_cards[1]] })}/></label><label>{t("底牌 2")}<input aria-label={t("底牌2")} value={input.hole_cards[1]} maxLength={2} onChange={e => edit({ ...input, hole_cards: [input.hole_cards[0], e.target.value.slice(0, 1).toUpperCase() + e.target.value.slice(1).toLowerCase()] })}/></label></div><label className="checkbox stack-toggle"><input type="checkbox" checked={input.stack_mode === 'exact'} onChange={e => edit({ ...input, stack_mode: e.target.checked ? 'exact' : 'assumed' })}/>{t("考虑各座位实际后手")}</label>{input.stack_mode === 'assumed' ? <p className="fine">{t("免填后手：统一按 100BB 开手估算。短码、全下、边池请启用实际后手。")}</p> : <p className="fine">{t("在下方高级设置填写各座位开手筹码。")}</p>}</div><div className="seat-picker"><div className="seat-pick-tools"><div className="mode-switch"><button className={seatTarget === 'hero' ? 'active' : ''} onClick={() => setSeatTarget('hero')}>{t("点选我的位置")}</button><button className={seatTarget === 'button' ? 'active' : ''} onClick={() => setSeatTarget('button')}>{t("点选庄家")}</button></div><button className="text-button" onClick={() => edit({ ...input, button: (input.button + 1) % input.seats.length })}><RotateCw size={14}/>{t("庄家顺移一位")}</button></div><div className="seat-map"><div className="seat-map-felt"><span>{t("点一下座位")}</span><strong>{seatTarget === 'hero' ? t("我坐这里") : t("庄家在这里")}</strong></div>{input.seats.map((seat, i) => { const angle = Math.PI / 2 + i * 2 * Math.PI / input.seats.length; return <button key={seat.id} aria-label={t("座位 {0} · {1}{2}", i + 1, positionAt(i, input.button, input.seats.length), seat.id === input.hero ? t(" · 我") : '')} aria-pressed={seatTarget === 'hero' ? seat.id === input.hero : i === input.button} className={`seat-choice ${seat.id === input.hero ? 'hero' : ''} ${i === input.button ? 'dealer' : ''}`} style={{ left: `${50 + 42 * Math.cos(angle)}%`, top: `${50 + 34 * Math.sin(angle)}%` }} onClick={() => pickSeat(i)}><small>{t("座位")}{i + 1}</small><b>{seat.id === input.hero ? t("我") : positionAt(i, input.button, input.seats.length)}</b>{i === input.button && <em>D</em>}</button>; })}</div><p className="fine">{t("我在 {0} · 庄家在座位 {1}。换位后自动重新校验已有行动。", positionAt(input.seats.findIndex(s => s.id === input.hero), input.button, input.seats.length), input.button + 1)}</p></div></div><details className="setup-details"><summary>{t("高级设置：盲注与后手")}<span>{input.seats.length}{t("人 ·")}{input.mode === 'cash' ? t("现金桌") : 'SNG'} · {input.hole_cards.join(' ')}</span></summary><div className="form-grid">
      <label>{t("小盲")}<input type="number" min="1" value={input.small_blind} onChange={e => edit({ ...input, small_blind: +e.target.value })}/></label>
      <label>{t("大盲")}<input type="number" min="1" value={input.big_blind} onChange={e => edit({ ...input, big_blind: +e.target.value })}/></label>
      <label>BB ante<input type="number" min="0" value={input.ante} onChange={e => edit({ ...input, ante: +e.target.value })}/></label>

    </div><p className="fine">{t("输入统一为整数筹码。现金桌 100 筹码 = 1 虚拟单位；As 为黑桃 A，Td 为方块 10。")}</p>{input.stack_mode === 'exact' && <div className="seat-inputs">{input.seats.map((s, i) => <label key={s.id}>{t("座位 {0} 开手筹码", i + 1)}<input type="number" min="1" value={s.stack} onChange={e => edit({ ...input, seats: input.seats.map((p, j) => j === i ? { ...p, stack: +e.target.value } : p) })}/></label>)}</div>}</details>
    <div className="advisor-layout"><div><div className="section-head compact"><h3>{t("行动时间轴")}</h3><span className="muted">{t("输入后自动校验 · 可插入、编辑、改序")}</span></div>
      {input.events.length === 0 ? <div className="empty-timeline">{t("盲注已自动入池。先录入此前玩家的行动。")}</div> : <ol className="editable-events">{input.events.map((e, i) => <li key={e.id}><span className="event-index">{i + 1}</span><span><b>{e.kind === 'board' ? e.cards?.join(' ') : `${input.seats.some(p => p.id === e.player) ? t("座位 ") + (input.seats.findIndex(p => p.id === e.player) + 1) : e.player} · ${t(e.kind.toUpperCase())}${e.amount ? ' → ' + e.amount : ''}`}</b><small>{e.time || t("按顺序录入")}</small></span><button aria-label={t("上移事件{0}", i + 1)} onClick={() => move(i, -1)}><ArrowUp size={14}/></button><button aria-label={t("下移事件{0}", i + 1)} onClick={() => move(i, 1)}><ArrowDown size={14}/></button><button aria-label={t("在事件{0}前插入", i + 1)} onClick={() => { setInsert(i); setEditing(null); }}><Plus size={14}/></button><button aria-label={t("编辑事件{0}", i + 1)} onClick={() => { setEditing(i); setEventKind(e.kind); setPlayer(e.player || input.hero); setAmount(e.amount || 300); setCards(e.cards?.join(' ') || ''); setTime(e.time || ''); }}><Pencil size={14}/></button><button aria-label={t("删除事件{0}", i + 1)} onClick={() => { setEditing(null); edit({ ...input, events: input.events.filter((_, j) => i !== j) }); }}><Trash2 size={14}/></button></li>)}</ol>}
      <div className="event-editor"><p className="small-label">{editing !== null ? t("编辑第 {0} 条", editing + 1) : insert < 0 ? t("追加新事件") : t("插入第 {0} 条之前", insert + 1)}</p><div className="form-grid three"><label>{t("动作")}<select value={eventKind} onChange={e => setEventKind(e.target.value)}><option value="call">{t("Call · 跟注")}</option><option value="check">{t("Check · 过牌")}</option><option value="fold">{t("Fold · 弃牌")}</option><option value="raise">{t("Bet / Raise · 下注或加注")}</option><option value="board">{t("发公共牌")}</option></select></label>{eventKind === 'board' ? <label>{t("公共牌")}<input value={cards} onChange={e => setCards(e.target.value)} placeholder="Ah 7c 2d"/></label> : <label>{t("行动玩家")}<select value={player} onChange={e => setPlayer(e.target.value)}>{input.seats.map((s, i) => <option key={s.id} value={s.id}>{t("座位")}{i + 1}{input.hero === s.id ? t("（我）") : ''}</option>)}</select></label>}
      {eventKind === 'raise' ? <label>{t("本街加注到")}<input type="number" min="1" value={amount} onChange={e => setAmount(+e.target.value)}/></label> : null}<label>{t("时间（可选）")}<input type="time" step="1" value={time} onChange={e => setTime(e.target.value)}/></label></div><div className="inline-controls"><button className="secondary" onClick={addEvent}><Plus size={15}/>{editing === null ? t("添加事件") : t("保存事件")}</button><button className="text-button" onClick={() => { if (input.events.some(e => !e.time)) {
        setError("按时间排序前，请为每条事件填写时间");
        return;
    } edit({ ...input, events: [...input.events].sort((a, b) => a.time!.localeCompare(b.time!)) }); }}>{t("按时间排序")}</button></div></div>
      <div className="inline-controls advisor-actions"><button className="primary" disabled={!!busy} onClick={() => inspect()}>{busy === '检查牌局' ? t("计算中…") : t("校验牌局 · 计算权益")}</button><span className="fine">{dirty ? (error ? t("请修正输入后自动重算") : t("正在自动校验…")) : t("已自动校验当前输入")}</span></div>
      {error && <div className="notice error" role="alert">{t(error)}</div>}
      {s && <div className={dirty ? 'stale' : ''}><div className="table-stage advisor-table"><div className="table-meta"><span>{t(streetName[s.street])} · {s.actor ? t("等待座位 {0}", input.seats.findIndex(p => p.id === s.actor) + 1) : t("等待公共牌或已结束")}</span><span>{t("我的视角")}</span></div><PokerTable state={s} mode={input.mode} hero={input.hero} hideStacks={input.stack_mode === 'assumed'}/></div></div>}
    </div><aside className="math-panel"><p className="eyebrow">{t("01 / 数学权益")}</p>{preview?.math?.available && !dirty ? <><div className="equity-number">{(preview.math.equity * 100).toFixed(1)}<span>%</span></div><p className="muted">{t("预期分池份额，包含平局")}</p><div className="math-readings"><div><span>{t("独赢概率")}</span><b>{(preview.math.win * 100).toFixed(1)}%</b></div><div><span>{t("平局概率")}</span><b>{(preview.math.tie * 100).toFixed(1)}%</b></div><div><span>{t("95% 模拟区间")}</span><b>{preview.math.ci95.map((v: number) => (v * 100).toFixed(1)).join('–')}%</b></div><div><span>{t("当前跟注比例")}</span><b>{preview.math.pot_odds === null ? t("未到我的回合") : (preview.math.pot_odds * 100).toFixed(1) + '%'}</b></div></div><p className="fine">{preview.math.samples.toLocaleString()}{t("次 Monte Carlo。")}{t(preview.math.assumption)}{t("。权益不是行动 EV。")}</p>{preview.math.settled_pots?.length > 1 && <div className="math-readings">{preview.math.settled_pots.map((p: any, i: number) => <div key={i}><span>{t("参与底池")}{i + 1} · {money(p.amount, input.mode)}</span><b>{(p.equity * 100).toFixed(1)}%</b></div>)}</div>}</> : <div className="math-placeholder">—<p>{t("校验当前输入后显示")}</p></div>}
      <div className="model-advice"><p className="eyebrow">{t("02 / 模型建议")}</p>{preview?.stack_assumption && <p className="assumption-note">{t(preview.stack_assumption)}</p>}<h3>{t("下一步，怎么打？")}</h3><label className="advisor-billing">{t("调用方式")}<select aria-label={t("调用方式")} value={input.billing_mode} onChange={e=>edit({...input,billing_mode:e.target.value as BillingMode})}><option value="hosted">{t("托管服务")}</option><option value="personal">{t("个人密钥")}</option></select></label><label>{t("建议模型")}<select aria-label={t("建议模型")} value={input.model_entry_id} onChange={e => edit({ ...input, model_entry_id: e.target.value })}>{available.length===0&&<option value="">{t("暂无可用模型，请稍后重试。")}</option>}{available.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label><p className="fine">{t(input.billing_mode==='personal'?'使用个人密钥时，调用费用由你的模型服务商账户承担。':'当前使用托管服务。')}</p><p className="fine">{t("模型给出动作偏好；不代表获胜概率或最优混合策略。")}</p><button className="primary" onClick={ask} disabled={!access.user || !available.some(e=>e.id===input.model_entry_id) || !!busy || dirty || !preview?.can_advise}>{busy === '模型正在判断' ? t("模型判断中…") : t("获取下一步建议")}</button>{!dirty && s && !preview?.can_advise && <p className="fine">{t("尚未轮到你，请继续录入前面的动作。")}</p>}
      {decision && <div className="prob-list">{Object.entries(decision.answers.action.probabilities).sort((a, b) => b[1] - a[1]).map(([id, p]) => <div className="prob-row" key={id}><div><span>{t(s?.legal_actions.find(a => a.id === id)?.label || id)}{id.startsWith('raise_to_') ? ' → ' + money(+id.replace('raise_to_', ''), input.mode) : ''}</span><b>{(p * 100).toFixed(1)}%</b></div><div className="prob-track"><i style={{ width: `${p * 100}%` }}/></div></div>)}<p className="fine">{t("决策用时 {0} 秒",decision.latency.toFixed(2))}</p></div>}
      </div></aside></div></fieldset>
  </section>;
}

import {t} from './i18n';
import { CSSProperties, ReactNode, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Pause, Play, Trophy } from 'lucide-react';
import { api, Entry, GameEvent, money, Run, State, streetName } from './types';
import {playCue,soundProgress} from './tableAudio';
import {handWinners} from './handResults';
const suit: Record<string, string> = { s: '♠', h: '♥', d: '♦', c: '♣' };
export function PlayingCard({ value, small = false }: {
    value: string;
    small?: boolean;
}) {
    if (!value || value === '??')
        return <span className={`card back ${small ? 'small' : ''}`}><span>✣</span></span>;
    return <span className={`card ${small ? 'small' : ''} ${'hd'.includes(value[1]) ? 'red' : ''}`}><b>{value[0] === 'T' ? '10' : value[0]}</b><span>{suit[value[1]]}</span></span>;
}
export function actionText(action: NonNullable<GameEvent['action']>, mode: string) {
    const verb = action.kind === 'raise' ? (action.all_in ? 'ALL IN' : action.raise_number === 1 ? 'BET' : action.raise_number === 3 ? '3-BET' : action.raise_number && action.raise_number > 3 ? `${action.raise_number}-BET` : 'RAISE') : action.kind === 'call' ? (action.all_in ? 'CALL · ALL IN' : 'CALL') : action.kind === 'fold' ? 'FOLD' : 'CHECK';
    return t(verb) + (action.kind === 'raise' ? ` → ${money(action.to, mode)}` : action.kind === 'call' ? ` ${money(action.pay, mode)}` : '');
}
export function PokerTable({ state, entries = [], mode = 'cash', event, recentActions = {}, hero, hideStacks = false, centerAction, soundScope }: {
    state: State;
    entries?: Entry[];
    mode?: string;
    event?: GameEvent;
    recentActions?: Record<string, NonNullable<GameEvent['action']>>;
    hero?: string;
    hideStacks?: boolean;
    centerAction?: ReactNode;
    soundScope?: string;
}) {
    const seats = state.seats;
    const winners=handWinners(state);
    useEffect(()=>{
        if(!soundScope||!event)return;
        if(soundProgress.advance(soundScope,state.hand_number,event.index)){
            if(state.complete&&winners.length)playCue('win');
            else if(event.type==='board')playCue('board');
            else if(event.action)playCue('action');
        }
    },[soundScope,state.hand_number,event?.index]);
    const coords = seats.map((_, i) => { const a = Math.PI / 2 + 2 * Math.PI * i / seats.length; return { x: 50 + 42 * Math.cos(a), y: 50 + 35 * Math.sin(a) }; });
    const dealer = coords[state.button];
    const action = event?.action;
    const from = action ? coords[seats.findIndex(s => s.id === action.player)] : null;
    return <><div className={`table-wrap ${event?.type === 'settlement' ? 'settled-table' : ''}`}><div className="felt"><div className="felt-line"/><span className="felt-wordmark">PROPHETLAB · JEV POKERBENCH</span>
    <div className="community"><div className="board">{Array.from({ length: 5 }, (_, i) => state.board[i] ? <span className="board-deal" key={`${i}-${state.board[i]}`} style={{ '--delay': `${i < 3 ? i * 80 : 0}ms` } as CSSProperties}><PlayingCard value={state.board[i]}/></span> : <span className="card empty" key={i}/>)}</div>
      <div className="pot"><span>{state.complete ? t("本手已结算") : t("当前底池")}</span><strong key={state.pot} className="pot-number">{money(state.pot, mode)}</strong></div>
      {state.pots.length > 1 && <div className="side-pots">{state.pots.map((p, i) => <span key={i}>{i ? t("边池 ") + i : t("主池")} {money(p.amount, mode)}</span>)}</div>}
    {centerAction&&<div className="table-center-action">{centerAction}</div>}
    </div></div>
    {dealer&&<span className="dealer-button" aria-label={t("庄家")} title={t("庄家")} style={{left:`calc(${dealer.x}% ${dealer.x>50?'-':'+'} var(--dealer-offset))`,top:`${dealer.y}%`}}>D</span>}
    {action && action.pay > 0 && from && <div key={`chips-${event?.index}`} aria-hidden="true" className="chip-transfer" style={{ '--sx': `${from.x}%`, '--sy': `${from.y}%` } as CSSProperties}><i /><i /><i /><span>+{money(action.pay, mode)}</span></div>}
    {seats.map((s, i) => {
            const entry = entries.find(e => e.id === s.id), last = recentActions[s.id];
            return <div key={s.id} className={`seat ${state.actor === s.id ? 'acting' : ''} ${s.folded ? 'folded' : ''} ${action?.player === s.id ? 'just-acted' : ''} ${winners.some(w=>w.id===s.id)?'hand-winner':''} ${s.stack === 0 && s.position === 'OUT' ? 'out' : ''}`} style={{ left: `${coords[i].x}%`, top: `${coords[i].y}%` }}>
      <div className="seat-cards">{s.cards.map((c, j) => <PlayingCard key={j} value={c} small/>)}</div>
      <div className="seat-body"><div className="seat-top"><i style={{ background: entry?.color || '#c3d7ce' }}/><span>{hero === s.id ? t("我") : entry?.name || t(s.name)}</span><em>{s.position || `S${i + 1}`}</em></div><b>{hideStacks ? '—' : money(s.stack, mode)}</b><small>{hideStacks ? t("后手未填") : s.folded ? t("已弃牌") : s.all_in ? t('ALL IN') : state.actor === s.id ? t("待行动") : t("后手")}</small></div>
      {last && <div className={`seat-action ${last.kind} ${last.all_in ? 'all-in' : ''}`} key={`${s.id}-${last.id}-${last.to}-${last.raise_number}`}>{actionText(last, mode)}</div>}
      {winners.some(w=>w.id===s.id)&&<span className="seat-win"><Trophy size={13}/>{t('赢得底池')} {money(s.committed+(s.payoff||0),mode)}</span>}
      {s.bet > 0 && <span className={`wager ${coords[i].y > 50 ? 'above' : 'below'}`}><i /> {money(s.bet, mode)}</span>}
    </div>;
        })}
  </div>{state.complete&&<div className="hand-result" role="status"><div><Trophy size={22}/><strong>{t(winners.length>1?'本手底池获胜者':'本手获胜者')}</strong><span>{t('第 {0} 手',state.hand_number)}</span></div>{winners.length?winners.map(w=><p key={w.id}><b>{hero===w.id?t('我'):entries.find(e=>e.id===w.id)?.name||t(w.name)}</b><span>{t('赢得底池')} {money(w.committed+(w.payoff||0),mode)}</span><small>{t('本手净收益')} {w.payoff!>0?'+':''}{money(w.payoff||0,mode)}</small></p>):<p>{t('本手筹码已结算。')}</p>}</div>}</>;
}
export function Replay({ run, initialHand = 1, initialLive = false }: {
    run: Run;
    initialHand?: number;
    initialLive?: boolean;
}) {
    const [number, setNumber] = useState(initialHand), [events, setEvents] = useState<GameEvent[]>([]), [step, setStep] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1), [view, setView] = useState('omniscient'), [error, setError] = useState('');
    const [hands, setHands] = useState<{
        number: number;
        complete: boolean;
    }[]>([]);
    const [live, setLive] = useState(initialLive);
    const loaded = useRef('');
    useEffect(() => { setLive(initialLive); }, [initialLive]);
    useEffect(() => { if (live) { setNumber(run.active_hand || run.hands_played || 1); setPlaying(false); } }, [live, run.active_hand, run.hands_played]);
    useEffect(() => { if (!live) setNumber(initialHand); }, [initialHand, run.id]);
    useEffect(() => { api(`/api/runs/${run.id}/hands`).then(setHands).catch(e => setError(e.message)); }, [run.id, run.hands_played, run.active_hand]);
    useEffect(() => {
        let active = true;
        const key = `${run.id}:${number}:${view}`;
        if (loaded.current !== key) {
            setPlaying(false);
            setEvents([]);
            setStep(0);
        }
        let sequence = 0;
        const fetchHand = () => { const request = ++sequence; return api(`/api/runs/${run.id}/hands/${number}?view=${view === 'omniscient' ? 'omniscient' : 'public'}${view !== 'omniscient' && view !== 'public' ? '&hero=' + view : ''}`).then(r => { if (active && request === sequence) {
            setEvents(r.events);
            setError('');
            loaded.current = key;
        } }).catch(e => { if (active && request === sequence)
            setError(e.message); }); };
        void fetchHand();
        const timer = run.active_hand === number ? setInterval(fetchHand, 2500) : undefined;
        return () => { active = false; if (timer)
            clearInterval(timer); };
    }, [run.id, number, view, run.hands_played, run.active_hand]);
    useEffect(() => { if (!playing)
        return; const id = setInterval(() => setStep(v => { if (v >= events.length - 1) {
        setPlaying(false);
        return v;
    } return v + 1; }), 2000 / speed); return () => clearInterval(id); }, [playing, speed, events.length]);
    const displayStep = live ? Math.max(0, events.length - 1) : step;
    const follow = (enabled: boolean) => {
        if (!enabled) setStep(displayStep);
        setPlaying(false);
        setLive(enabled);
        history.replaceState(null, '', `#replay/${run.id}/${number}${enabled ? '/live' : ''}`);
    };
    const current = events[displayStep];
    const action = current?.action;
    const probs = action?.decision?.answers.action.probabilities;
    const actorName = run.entries.find(e => e.id === action?.player)?.name;
    const nextName = run.entries.find(e => e.id === current?.snapshot.actor)?.name;
    const recent: Record<string, NonNullable<GameEvent['action']>> = {};
    for (const e of events.slice(0, displayStep + 1))
        if (e.action)
            recent[e.action.player] = e.action;
    return <section><div className="section-head"><div><p className="eyebrow">{t("HAND REPLAY / 每一个动作，清楚看见")}</p><h2>{t("牌桌回放")}</h2>{run.proxy && <p className="fine">{t("席位当前使用 DeepSeek 代理")}</p>}</div><div className="inline-controls"><label className="live-follow"><input type="checkbox" role="switch" aria-label={t("实时跟随")} checked={live} onChange={e => follow(e.target.checked)}/>{t("实时跟随")}</label><select aria-label={t("选择手牌")} value={number} onChange={e => { follow(false); setNumber(+e.target.value); history.replaceState(null, "", `#replay/${run.id}/${e.target.value}`); }}>{hands.map(h => <option key={h.number} value={h.number}>{t("第 {0} 手", h.number)}{h.complete ? '' : t(" · 进行中")}</option>)}</select><select aria-label={t("回放视角")} value={view} onChange={e => setView(e.target.value)}><option value="omniscient">{t("赛后上帝视角")}</option><option value="public">{t("公开信息视角")}</option>{run.entries.map(e => <option key={e.id} value={e.id}>{e.name}{t("视角")}</option>)}</select></div></div>
    {live && <div className="live-status" role="status"><i className={run.status === 'running' ? 'live-dot' : ''}/><strong>{run.status === 'running' ? t("实时跟随中") : t("实时跟随 · 比赛未运行")}</strong><span>{t(run.message)}</span><small>{t("自动跟随最新动作和下一手；操作时间轴可退出。")}</small></div>}
    {error ? <div role="alert" className="notice">{t(error)}</div> : current ? <><div className="action-spotlight" key={`${run.id}-${number}-${displayStep}`} aria-live="polite"><div className="action-speaker"><span>{action ? t("刚刚行动") : current.type === 'board' ? t("公共牌发出") : current.type === 'settlement' ? t("本手结束") : t("准备开局")}</span><strong>{actorName || t(streetName[current.snapshot.street])}</strong></div><div className={`action-verdict ${action?.all_in ? 'all-in' : ''}`}><b>{action ? actionText(action, run.mode) : t(current.label)}</b><span>{action ? `${t(current.label)}${action.pay ? t(" · 本次投入 ") + money(action.pay, run.mode) : ''}` : current.type === 'settlement' ? t("筹码已完成结算") : t("跟随牌局逐步展开")}</span></div><div className="next-player"><span>{nextName ? t("接下来") : t('HAND') + ' ' + String(number).padStart(3, '0')}</span><strong>{nextName || t("结算 / 发牌")}</strong></div></div>
    <div className="replay-layout"><div className="table-stage"><div className="table-meta"><span>{t("HAND")} {String(number).padStart(3, '0')} · {t(streetName[current.snapshot.street])}</span><span>{t("BLINDS")} {money(current.snapshot.small_blind, run.mode)} / {money(current.snapshot.big_blind, run.mode)}</span></div><PokerTable soundScope={run.id} state={current.snapshot} entries={run.entries} mode={run.mode} event={current} recentActions={recent}/><div className="playbar"><button aria-label={t("上一步")} disabled={displayStep === 0} onClick={() => { follow(false); setStep(Math.max(0, displayStep - 1)); }}><ArrowLeft size={17}/></button><button className="play" aria-label={playing ? t("暂停回放") : t("播放回放")} onClick={() => { follow(false); if (displayStep === events.length - 1)
            setStep(0); setPlaying(!playing); }}>{playing ? <Pause size={17}/> : <Play size={17}/>}</button><button aria-label={t("下一步")} disabled={displayStep === events.length - 1} onClick={() => { follow(false); setStep(Math.min(events.length - 1, displayStep + 1)); }}><ArrowRight size={17}/></button><input aria-label={t("牌局时间轴")} type="range" min="0" max={events.length - 1} value={displayStep} onChange={e => { follow(false); setStep(+e.target.value); }}/><span>{displayStep + 1}/{events.length}</span><select aria-label={t("播放速度")} value={speed} onChange={e => setSpeed(+e.target.value)}>{[.5, 1, 2, 4].map(n => <option key={n} value={n}>{n}×</option>)}</select></div></div>
    <aside className="action-panel"><p className="eyebrow">{t("DECISION / 模型动作偏好")}</p><h3>{actorName || t(current.label)}</h3>{probs ? <div className="prob-list">{Object.entries(probs).sort((a, b) => b[1] - a[1]).map(([id, p]) => <div className="prob-row" key={id}><div><span>{id === 'fold' ? t("弃牌") : id === 'check' ? t("过牌") : id === 'call' ? t("跟注") : t("加注到 ") + money(+id.replace('raise_to_', ''), run.mode)}</span><b>{(p * 100).toFixed(1)}%</b></div><div className="prob-track"><i style={{ width: `${p * 100}%`, background: action?.id === id ? '#176b54' : '#bec7c1' }}/></div></div>)}<p className="fine">{t("动作偏好不代表获胜概率。实际执行最高概率动作。")}</p>{action?.decision?.answers.action.legal_action_projection&&<p className="notice">{t("按合法动作条件化；原始弃权概率 {0}%。", (action.decision.answers.action.legal_action_projection.abstention_probability*100).toFixed(1))}</p>}<p className="fine">{action?.decision?.model} · {action?.decision?.latency.toFixed(2)}s · {t("思考强度：{0}", action?.decision?.thinking === "low" ? "low" : t("默认"))}{action?.decision?.retry_count ? t(" · 自动重试 {0} 次", action.decision.retry_count) : ''}</p></div> : <p className="muted">{run.active_hand === number ? t("本手尚未结束，只展示公开动作。") : t("该步骤为发牌或结算，没有模型决策。")}</p>}</aside></div>
    <div className="timeline">{events.map((e, i) => <button className={i === displayStep ? 'selected' : ''} key={i} onClick={() => { follow(false); setStep(i); }}><span>{String(i + 1).padStart(2, '0')}</span><b>{e.action ? run.entries.find(p => p.id === e.action?.player)?.name : t(streetName[e.snapshot.street])}</b><small>{e.action ? actionText(e.action, run.mode) : t(e.label)}</small></button>)}</div></> : <div className="empty-state">{t("等待第一手牌的行动记录。")}</div>}
  </section>;
}

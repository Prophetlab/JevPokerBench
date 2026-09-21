import {t} from './i18n';
import { useState } from 'react';
import { money, Run } from './types';
import {clampRange, zoomRange, ChartRange} from './chartWindow';
export function EquityChart({ run, onHand }: {
    run: Run;
    onHand: (n: number) => void;
}) {
    const [focus, setFocus] = useState<string | null>(null);
    const [showEV, setShowEV] = useState(true);
    const [viewport, setViewport] = useState<{runId:string;range:ChartRange} | null>(null);
    const last = Math.max(0, run.history.length - 1);
    const range = clampRange(viewport?.runId === run.id ? viewport.range : [0, last], run.history.length);
    const [start, end] = range;
    const changeRange = (next:ChartRange) => {
        const bounded = clampRange(next, run.history.length);
        setViewport(bounded[0] === 0 && bounded[1] === last ? null : {runId:run.id,range:bounded});
    };
    const points = run.history.slice(start, end + 1);
    const all = points.flatMap(p => [...Object.values(p.values), ...(showEV ? Object.values(p.ev_values || {}) : [])]);
    let low = all.length ? all.reduce((a, b) => Math.min(a, b)) : 0;
    let high = all.length ? all.reduce((a, b) => Math.max(a, b)) : 1;
    const padding = Math.max(1, (high - low) * .15);
    low -= padding;
    high += padding;
    const width = 880, height = 300, left = 65, right = 25, top = 20, bottom = 40;
    const x = (i: number) => left + i / Math.max(1, points.length - 1) * (width - left - right);
    const y = (v: number) => top + (high - v) / (high - low) * (height - top - bottom);
    const ticks = Array.from({ length: 5 }, (_, i) => low + (high - low) * i / 4);
    return <div className="chart"><div className="chart-heading"><h3>{run.mode === 'cash' ? t("累计净盈利") : t("比赛筹码")}</h3><span>{run.mode === 'cash' ? t("虚拟单位 · 不含补码转入") : t("筹码 · 独立 SNG 排名")}</span></div>
    <div className="ev-toolbar"><label className="checkbox"><input type="checkbox" checked={showEV} onChange={e => setShowEV(e.target.checked)}/>{t("全下权益调整对比")}</label><span>{t("实线：实际 · 虚线：调整后")}</span></div>
    <div className="chart-zoom" role="group" aria-label={t("图表缩放")}>
      <button className="secondary" disabled={end-start<=1} onClick={()=>changeRange(zoomRange(range,run.history.length,.5))}>{t("放大")}</button>
      <button className="secondary" disabled={start===0&&end===last} onClick={()=>changeRange(zoomRange(range,run.history.length,2))}>{t("缩小")}</button>
      <button className="secondary" disabled={!viewport||viewport.runId!==run.id} onClick={()=>setViewport(null)}>{t("查看全部")}</button>
      <span className="small-label" aria-live="polite">{t("第 {0}–{1} 手",points[0]?.hand??0,points.at(-1)?.hand??0)}</span>
    </div>
    <div className="chart-range">
      <label>{t("起始手牌")}<input type="range" min={0} max={Math.max(0,end-1)} value={start} disabled={last<1} aria-valuetext={t("第 {0} 手",points[0]?.hand??0)} onChange={e=>changeRange([+e.target.value,end])}/></label>
      <label>{t("结束手牌")}<input type="range" min={Math.min(last,start+1)} max={last} value={end} disabled={last<1} aria-valuetext={t("第 {0} 手",points.at(-1)?.hand??0)} onChange={e=>changeRange([start,+e.target.value])}/></label>
    </div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("所有参赛席位的资金变化曲线")}>
      {ticks.map((v, i) => <g key={i}><line x1={left} x2={width - right} y1={y(v)} y2={y(v)} stroke="#e6e7df"/><text x={left - 12} y={y(v) + 4} textAnchor="end" fill="#899088" fontSize="11">{money(v, run.mode)}</text></g>)}
      {run.mode === 'cash' && low<=0 && high>=0 && <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} stroke="#a6afa5" strokeDasharray="3 5"/>}
      {run.entries.map(e => <g key={e.id} opacity={!focus || focus === e.id ? 1 : .13}><polyline points={points.map((p, i) => `${x(i)},${y(p.values[e.id] || 0)}`).join(' ')} fill="none" stroke={e.color} strokeWidth={focus === e.id ? 3 : 2} strokeLinejoin="round"/>{showEV && <polyline points={points.map((p, i) => `${x(i)},${y((p.ev_values || p.values)[e.id] || 0)}`).join(' ')} fill="none" stroke={e.color} strokeWidth={focus === e.id ? 2.5 : 1.5} strokeDasharray="6 5"/>}{points.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.values[e.id] || 0)} r={points.length < 15 ? 3 : 1.5} fill={e.color} onClick={() => p.hand && onHand(p.hand)} style={{ cursor: p.hand ? 'pointer' : 'default' }}><title>{e.name} · {t("第 {0} 手", p.hand)} · {money(p.values[e.id] || 0, run.mode)}</title></circle>)}</g>)}
      <text x={left} y={height - 10} fill="#899088" fontSize="11">{points[0]?.hand?t("第 {0} 手",points[0].hand):t("开局")}</text><text x={width - right} y={height - 10} textAnchor="end" fill="#899088" fontSize="11">{t("第 {0} 手", points.at(-1)?.hand??0)}</text>
    </svg><div className="chart-legend">{run.entries.map(e => <button className={focus === e.id ? 'active' : ''} key={e.id} onClick={() => setFocus(focus === e.id ? null : e.id)}><i style={{ background: e.color }}/>{e.name}</button>)}</div><details className="ev-method"><summary>{t("权益调整说明")}</summary><p className="fine ev-note">{t("已调整")}{run.ev_summary?.covered_hands || 0}{t("手。仅去除整桌停止下注后全下发牌的运气；非全下手保持实际盈亏。两线重合可能表示没有可调整全下，不等于决策无误。")}{run.mode === 'sng' ? t("SNG 为筹码权益，不代表名次 EV。") : ''}</p></details>
    {focus && <div className="ev-comparison"><span>{run.entries.find(e => e.id === focus)?.name}</span><b>{t("实际")}{money(run.history.at(-1)?.values[focus] || 0, run.mode)}</b><b>{t("调整后")}{money((run.history.at(-1)?.ev_values || run.history.at(-1)?.values || {})[focus] || 0, run.mode)}</b><span>{t("运气差额")}{money((run.history.at(-1)?.values[focus] || 0) - (run.history.at(-1)?.ev_values || run.history.at(-1)?.values || {})[focus], run.mode)}</span></div>}
  </div>;
}

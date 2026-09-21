import {t} from './i18n';
import { useState } from 'react';
import { ArrowUpRight, Trophy, Hourglass, Spade } from 'lucide-react';
import { money, Run, Series, statusName, seriesPoints } from './types';
import { EquityChart } from './Chart';
export const latestMatch = (runs: Run[], mode: string) => runs.filter(r => r.mode === mode && !r.human_player_id).sort((a, b) => (b.created || 0) - (a.created || 0))[0];
function Award({ run, mode, series }: {
    run?: Run;
    series?: Series;
    mode: 'cash' | 'sng';
}) {
    const finished = series ? series.status === 'complete' : run?.status === 'complete';
    const rows = series ? series.standings.slice(0, 3).map(s => ({...s, rank:s.rank || 1, stack:0, profit:0})) : run?.standings.slice(0, 3) || [];
    const hasHands = series ? series.completed > 0 : !!run?.hands_played;
    const title = mode === 'cash' ? t("现金桌") : t("SNG 锦标赛");
    const tied = rows.length > 1 && rows[0].rank === rows[1].rank;
    return <section className={`award ${finished ? 'award-final' : 'award-pending'}`} aria-label={t("{0}颁奖结果", title)}>
    <div className="award-heading"><span>{mode === 'cash' ? t('01 / CASH GAME') : t('02 / SIT & GO')}</span><span className="award-status">{finished ? (run?.proxy ? t("代理试跑 · 已结束") : t("比赛已结束")) : hasHands ? t("当前排名 · 冠军待定") : t("等待开赛")}</span></div>
    <div className="award-title">{finished ? <Trophy size={26}/> : <Hourglass size={24}/>}<h2>{title}</h2></div>
    {hasHands ? <><div className="podium" aria-label={t("{0}前三个席位，按实际名次标注", title)}>
      {[1, 0, 2].map((index) => {
                const player = rows[index];
                if (!player)
                    return <div key={index}/>;
                const first = player.rank === 1;
                return <div key={player.id} className={`podium-player podium-place-${index} ${first ? 'podium-first' : ''}`}>
        <div className="podium-medallion" style={{ color: run?.entries.find(e => e.id === player.id)?.color }}><span>{first ? '♠' : player.rank.toString().padStart(2, '0')}</span>{first && finished && <svg viewBox="0 0 100 100" aria-hidden="true"><path d="M30 78C8 62 8 36 23 19M70 78C92 62 92 36 77 19"/><path d="M20 60l-10-6m10-3-11-10m14 0-10-10m15 2-7-12m59 39 10-6m-10-3 11-10m-14 0 10-10m-15 2 7-12"/></svg>}</div>
        <strong>{player.name}</strong><span className="podium-value">{mode === 'cash' && player.profit > 0 ? '+' : ''}{series ? seriesPoints(series, series.standings.find(s => s.id === player.id)!) : money(mode === 'cash' ? player.profit : player.stack, mode)}<small>{series ? t("累计积分") : mode === 'cash' ? t("净盈利") : t("筹码")}</small></span>
        <div className="podium-step"><span>{player.rank.toString().padStart(2, '0')}</span><small>{finished ? (first ? (tied ? t("并列第一") : t("第一名")) : t("第 {0} 名", player.rank)) : t("暂列第 {0}", player.rank)}</small></div>
      </div>;
            })}
    </div><div className="award-caption"><span>{series ? t("已完成 {0} / {1} 场", series.completed, series.target) : t("{0} 手 · {1} 席", run?.hands_played || 0, run?.entries.length || 0)}</span><span>{finished ? t("完整赛果见下方") : series ? t("系列赛进行中，冠军待定") : mode === 'sng' ? t("尚未淘汰至一人，不提前颁冠军") : t("比赛尚未结束")}</span></div></> : <div className="award-empty"><Spade size={40}/><p>{series ? t("等待首场完赛 · 目标 {0} 场", series.target) : t("奖台已就位，等待第一手牌。")}</p></div>}
  </section>;
}
export function Overview({ runs, series, onReplay }: {
    runs: Run[];
    series?: Series;
    onReplay: (id: string, n: number, live?: boolean) => void;
}) {
    const cash = latestMatch(runs, 'cash'), sng = runs.find(r => r.id === series?.current_run_id) || latestMatch(runs, 'sng');
    return <><div className="page-heading"><div><p className="eyebrow">{t("TWO TABLES. TWO RESULTS.")}</p><h1>{t("两场较量，各有胜负。")}</h1><p className="muted">{t("现金桌看净盈利，SNG 看最终名次。两场结果，一眼看见。")}</p></div><span className="tag">{t("独立排名 · 不设综合冠军")}</span></div>
    {(cash?.proxy || sng?.proxy) && <div className="proxy-banner awards-proxy"><span>{t("接入测试")}</span>{t("当前席位使用 DeepSeek 代理，结果不代表所列模型的真实实力。")}</div>}
    <div className="awards-stage"><Award run={cash} mode="cash"/><Award run={sng} mode="sng" series={series}/></div>
    <MatchResults runs={runs.filter(r => r.mode === 'cash' && !r.human_player_id)} mode="cash" onReplay={onReplay}/>
    {series && <SeriesResults series={series} onReplay={()=>sng&&onReplay(sng.id,sng.active_hand||sng.hands_played||1,true)}/>}
    {sng&&<details className="tournament-details"><summary>{t("当前锦标赛详情")}{series&&<span>{t("当前第 {0} 场",series.current_number)}</span>}</summary><MatchResults runs={runs.filter(r => r.mode === 'sng' && !r.human_player_id)} mode="sng" onReplay={onReplay}/></details>}
  </>;
}
function MatchResults({ runs, mode, onReplay }: {
    runs: Run[];
    mode: 'cash' | 'sng';
    onReplay: (id: string, n: number, live?: boolean) => void;
}) {
    const [historic, setHistoric] = useState('');
    const run = runs.find(r => r.id === historic) || latestMatch(runs, mode);
    const replay = (n: number) => run && onReplay(run.id, n);
    if (!run)
        return <section className="match-results"><div className="section-head"><h2>{mode === 'cash' ? t("现金桌") : t("SNG 锦标赛")}</h2></div><p className="empty-state">{t("尚无比赛，开赛后自动展示结果。")}</p></section>;
    return <section className="match-results" id={`results-${mode}`}><div className="section-head"><div><p className="eyebrow">{mode === 'cash' ? t('01 / CASH GAME RESULTS') : t('02 / SNG RESULTS')}</p><h2>{mode === 'cash' ? t("现金桌 · 盈利与排名") : t("SNG · 筹码与名次")}</h2></div><span className={`status ${run.status === 'running' ? 'live' : ''}`}>{t(statusName[run.status] || run.status)}</span></div>
    <div className="run-toolbar"><div><strong>{t("已完成 {0} / {1} 手",run.hands_played,run.max_hands)}</strong></div><div className="inline-controls">{runs.length > 1 && <select aria-label={t("{0}历史比赛", mode === 'cash' ? t("现金桌") : 'SNG')} value={historic} onChange={e => setHistoric(e.target.value)}><option value="">{t("最新比赛")}</option>{runs.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select>}<button className="secondary" disabled={!run.hands_played && !run.active_hand} onClick={() => onReplay(run.id, run.active_hand || run.hands_played || 1, true)}>{t("看牌桌")}<ArrowUpRight size={14}/></button></div></div>
    <div className="analysis-layout"><EquityChart run={run} onHand={replay}/><aside className="scoreboard"><div className="chart-heading"><h3>{mode === 'cash' ? t("净盈利排名") : run.status === 'complete' ? t("最终名次") : t("当前筹码排名")}</h3><span>{run.entries.length}{t("席")}{run.proxy ? t(" · 代理测试") : ''}</span></div><table><thead><tr><th>#</th><th>{t("参赛席位")}</th><th>{mode === 'cash' ? t("净盈利") : t("筹码")}</th></tr></thead><tbody>{run.standings.map(s => <tr key={s.id}><td className={s.rank === 1 ? 'first' : ''}>{String(s.rank).padStart(2, '0')}</td><td><span className="model-name"><i style={{ background: run.entries.find(e => e.id === s.id)?.color }}/>{s.name}</span>{s.retired && mode === 'cash' && <small className="place-label">{t("资金不足 · 已退出")}</small>}{s.place && mode === 'sng' && <small className="place-label">{t("最终第 {0} 名", s.place)}</small>}</td><td className={mode === 'cash' ? (s.profit >= 0 ? 'positive' : 'negative') : ''}>{mode === 'cash' && s.profit > 0 ? '+' : ''}{money(mode === 'cash' ? s.profit : s.stack, mode)}</td></tr>)}</tbody></table></aside></div>
    <Performance run={run} replay={replay}/>
    <div className="run-note"><span className="tiny-dot"/>{t(run.message)}</div>
    <details className="audit-table"><summary>{t("比赛规则与账本")}</summary>{mode === 'cash' && run.settle_every && <p className="fine">{t("每 {0} 手结码，重新买入 {1}；需要买入时资金不足者退出，其余玩家继续。累计盈亏保留。", run.settle_every, money(run.buy_in || 0))}</p>}
    {mode==='cash'&&<p className="fine chip-history-note">{t("历史实际盈亏保持原值；新下注以 0.5 为单位，权益调整值可能含小数。")} {run.chip_rule_start_hand&&t("从第 {0} 手起采用 0.5 下注单位。",run.chip_rule_start_hand)}</p>}<div className="scroll-table"><table><thead><tr><th>{t("席位")}</th><th>{t("桌上筹码")}</th><th>{t("账外资金")}</th><th>{t("总资产")}</th><th>{t("补码次数")}</th><th>bb/100</th></tr></thead><tbody>{run.standings.map(s => <tr key={s.id}><td>{s.name}</td><td>{money(s.stack, mode)}</td><td>{mode === 'cash' ? money(s.reserve) : '—'}</td><td>{money(s.equity, mode)}</td><td>{mode === 'cash' ? s.rebuys : '—'}</td><td>{s.bb100?.toFixed(2) ?? '—'}</td></tr>)}</tbody></table></div><p className="fine">{t("补码不是盈利。SNG 只看名次。当前少量试跑不构成统计显著的模型排名。")}</p></details>
  </section>;
}


function Performance({run,replay}:{run:Run;replay:(hand:number)=>void}) {
  const reason:Record<string,string>={all_in:'全下对抗',elimination:'淘汰',side_pots:'多边池',big_pot:'大底池'};
  const seconds=(value:number|null)=>value===null?'—':value.toFixed(3)+'s';
  return <div className="performance-report"><details className="audit-table"><summary>{t('模型决策用时')}</summary><p className="fine">{t('仅统计已执行的成功决策；排除重试。每模型满 8 个样本后，按 1.5×IQR 剔除异常值。包含网络与上游排队，不含比赛并发队列等待。')}</p><div className="scroll-table"><table><thead><tr><th>{t('参赛席位')}</th><th>{t('有效 / 总数')}</th><th>{t('平均用时')}</th><th>{t('中位用时')}</th><th>P90</th><th>{t('重试 / 异常')}</th></tr></thead><tbody>{run.timing?.models.map(row=><tr key={row.id}><td>{row.name}</td><td>{row.included} / {row.decisions}</td><td>{seconds(row.mean_seconds)}</td><td>{seconds(row.median_seconds)}</td><td>{seconds(row.p90_seconds)}</td><td>{row.retry_excluded} / {row.outlier_excluded+row.invalid_excluded}</td></tr>)}</tbody></table></div></details>
  {!!run.highlights?.length&&<section className="selected-hands"><h3>{t('精选关键场面')}</h3><div className="highlight-list">{run.highlights?.slice(0,3).map(h=><button className="highlight-item" key={h.hand} onClick={()=>replay(h.hand)}><span>{t('第 {0} 手',h.hand)}</span><strong>{h.reasons.map(r=>t(reason[r]||r)).join(' · ')}</strong><span>{money(h.pot,run.mode)} · {h.pot_bb} BB</span><ArrowUpRight size={16}/></button>)}</div></section>}</div>;
}

function SeriesResults({series,onReplay}:{series:Series;onReplay:()=>void}) {
    return <section className="match-results" aria-label={t("SNG 系列赛总榜")}>
      <div className="section-head"><div><p className="eyebrow">SNG SERIES</p><h2>{t("SNG 系列赛总榜")}</h2></div><div className="inline-controls"><span className={`status ${series.status === 'running' ? 'live' : ''}`}>{t(statusName[series.status] || series.status)}</span><button className="secondary" onClick={onReplay}>{t("看牌桌")}<ArrowUpRight size={14}/></button></div></div>
      <div className="run-toolbar"><div><strong>{t("已完成 {0} / {1} 场",series.completed,series.target)}</strong><p className="fine">{t("当前第 {0} 场",series.current_number)}</p></div></div>
      <p className="fine">{t("每场第 1 名 {0} 分，依次递减，第 {1} 名 0 分。仅计完赛；累计积分最高者夺冠，同分并列。",series.standings.length-1,series.standings.length)}</p>
      <div className="scroll-table"><table><thead><tr><th>#</th><th>{t("参赛席位")}</th><th>{t("累计积分")}</th><th>{t("夺冠次数")}</th></tr></thead><tbody>{series.standings.map(s=><tr key={s.id}><td>{s.rank ?? '—'}</td><td><span className="model-name"><i style={{background:s.color}}/>{s.name}</span></td><td><strong>{seriesPoints(series,s) ?? '—'}</strong></td><td>{s.wins}</td></tr>)}</tbody></table></div>
      {series.message && <p className="notice">{t(series.message)}</p>}
    </section>;
}
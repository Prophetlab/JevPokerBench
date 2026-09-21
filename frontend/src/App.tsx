import {t,useLanguage} from './i18n';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { BarChart3, ChevronRight, FlaskConical, Layers3, Spade, Users, X } from 'lucide-react';
import { api, Run, Series } from './types';
import { Overview, latestMatch } from './Overview';
import { Replay } from './Table';
import { Advisor } from './Advisor';
import { Rooms } from './Rooms';
import {SoundControls} from './SoundControls';
import {personalSnapshot,subscribePersonal} from './personalSession';
type Page = 'overview' | 'replay' | 'advisor' | 'rooms';
export default function App() {
    const account=useSyncExternalStore(subscribePersonal,personalSnapshot).user?.id;
    const [language, setLanguage] = useLanguage();
    useEffect(() => { document.documentElement.lang = language === "zh" ? "zh-CN" : "en"; document.title = "ProphetLab · " + t("JevPokerBench — Every decision on the record"); }, [language]);
    const [series, setSeries] = useState<Series[]>([]);
    const [runs, setRuns] = useState<Run[]>([]), [selected, setSelected] = useState(''), [page, setPage] = useState<Page>('overview'), [hand, setHand] = useState(1), [error, setError] = useState('');
    const run = runs.find(r => r.id === selected);
    useEffect(()=>{
        setRuns(old=>old.filter(r=>!r.human_player_id));
        setSelected(id=>runs.find(r=>r.id===id)?.human_player_id?runs.find(r=>!r.human_player_id)?.id||'':id);
    },[account]);
    const refresh = async () => { const [r, seriesData] = await Promise.all([api<Run[]>('/api/runs'), api<Series[]>('/api/series')]); setSeries(seriesData); setRuns(r); return r; };
    useEffect(() => { refresh().then(r => { const parts = location.hash.slice(1).split('/'); const chosen = r.find(v => v.id === parts[1]) || r.find(v => v.mode === 'cash') || r[0]; if (chosen) {
        setSelected(chosen.id);
    } if (['overview', 'replay', 'advisor', 'rooms'].includes(parts[0]))
        setPage(parts[0] as Page); if (parts[0] === 'replay' && chosen)
        setHand(+parts[2] || 1); }).catch(e => setError(e.message)); }, []);
    useEffect(() => {
        let cancelled = false, pending = false;
        const poll = async () => {
            if (pending) return;
            pending = true;
            try {
                const next = await api<Series[]>('/api/series');
                const missing = next.filter(s => !runs.some(r => r.id === s.current_run_id));
                const fresh = await Promise.all(missing.map(s => api<Run>(`/api/runs/${s.current_run_id}`)));
                if (!cancelled) {
                    setSeries(next);
                    if (fresh.length) setRuns(old => [...fresh.filter(r => !old.some(v => v.id === r.id)), ...old]);
                }
            } catch (e) { if (!cancelled) setError((e as Error).message); }
            finally { pending = false; }
        };
        const timer = setInterval(poll, 5000);
        return () => { cancelled = true; clearInterval(timer); };
    }, [runs.map(r => r.id).sort().join(',')]);
    useEffect(()=>{
        if(page!=='replay'||!location.hash.endsWith('/live')||!run?.series_id||run.status!=='complete')return;
        const current=series.find(s=>s.id===run.series_id)?.current_run_id;
        const next=runs.find(r=>r.id===current);
        if(next&&next.id!==run.id){
            const number=next.active_hand||next.hands_played||1;
            setSelected(next.id);setHand(number);location.hash=`replay/${next.id}/${number}/live`;
        }
    },[page,run,series,runs]);
    const streamIds = [...new Set((page === 'overview' ? [latestMatch(runs, 'cash')?.id, latestMatch(runs, 'sng')?.id, ...runs.filter(r => r.status === 'running' && !r.human_player_id).map(r => r.id)] : [selected]).filter(Boolean))].join(',');
    useEffect(() => { const streams = streamIds.split(',').filter(Boolean).map(id => { const stream = new EventSource(`/api/runs/${id}/stream`); stream.onmessage = e => { const r = JSON.parse(e.data); setRuns(old => old.map(v => v.id === r.id ? r : v)); }; return stream; }); return () => streams.forEach(s => s.close()); }, [streamIds]);
    useEffect(() => { const readHash = () => { const parts = location.hash.slice(1).split('/'); if (['overview', 'replay', 'advisor', 'rooms'].includes(parts[0]))
        setPage(parts[0] as Page); if (parts[0] === 'replay') {
        const r = runs.find(v => v.id === parts[1]);
        if (r) {
            setSelected(r.id);
            setHand(+parts[2] || 1);
        }
    } }; window.addEventListener('hashchange', readHash); return () => window.removeEventListener('hashchange', readHash); }, [runs]);
    useEffect(() => { window.scrollTo({ top: 0, left: 0 }); }, [page]);
    const navigate = (next: Page) => { setPage(next); location.hash = next === 'replay' ? `replay/${selected}/${hand}` : next; };
    return <div className="app"><aside className="sidebar"><button className="brand" onClick={() => navigate('overview')} aria-label={t("JevPokerBench 首页")}><Spade size={29} fill="currentColor"/><span>ProphetLab<span>JEV POKERBENCH</span></span></button><div className="sidebar-label">{t("THE DECISION ARENA")}</div><nav>{([{ id: 'overview', name: t("比赛总览"), icon: BarChart3 }, { id: 'replay', name: t("牌桌回放"), icon: Layers3 }, { id: 'rooms', name: t("自己组局"), icon: Users }, { id: 'advisor', name: t("牌局辅助器"), icon: FlaskConical }] as const).map(p => <button key={p.id} aria-label={p.name} title={p.name} className={page === p.id ? 'active' : ''} onClick={() => navigate(p.id)}><p.icon size={18}/><span>{p.name}</span>{page === p.id && <ChevronRight size={14}/>}</button>)}</nav><div className="sidebar-bottom"><div className="tiny-dot"/> <span>{t("可复查 · 可重放")}</span><p>{t("十个决策系统，同一套规则。")}<br />{t("每一次行动都留下证据。")}</p><small>PROPHETLAB / JEV POKERBENCH</small></div></aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb">ProphetLab<span>/</span> {page === 'advisor' ? t("牌局辅助器") : t("德州扑克基准")}</div><div className="topbar-right"><SoundControls/><label className="language-picker"><span>Language</span><select aria-label="Language" value={language} onChange={e => setLanguage(e.target.value as "en" | "zh")}><option value="en">English</option><option value="zh">中文</option></select></label></div></header>
    <main>{error && <div className="notice error" role="alert">{t(error)}<button onClick={() => setError('')} aria-label={t("关闭错误")}><X size={16}/></button></div>}
    {page === 'overview' && <Overview runs={runs.filter(r=>!r.human_player_id)} series={series[0]} onReplay={(id, n, live = false) => { setSelected(id); setHand(n); setPage('replay'); location.hash = `replay/${id}/${n}${live ? "/live" : ""}`; }}/>}
    {page === 'replay' && (run ? <><label className="replay-match-picker">{t("回放比赛")}<select value={selected} onChange={e => { setSelected(e.target.value); setHand(1); location.hash = `replay/${e.target.value}/1`; }}>{runs.map(r => <option key={r.id} value={r.id}>{r.mode === 'cash' ? t("现金桌") : 'SNG'} · {r.name}</option>)}</select></label><Replay key={run.id} run={run} initialHand={hand} initialLive={location.hash.endsWith("/live")}/></> : <div className="empty-state">{t("比赛完成第一手后即可查看回放。")}</div>)}
    {page === 'rooms' && <Rooms runs={runs} onUpdate={r=>setRuns(old=>old.some(v=>v.id===r.id)?old.map(v=>v.id===r.id?r:v):[r,...old])}/>}
    {page === 'advisor' && <Advisor/>}
    <footer><a className="source-link" href="https://github.com/Prophetlab/JevPokerBench" target="_blank" rel="noopener noreferrer">{t("开源代码与文档")} ↗</a><span>PROPHETLAB · JEV POKERBENCH</span><p>{t("记录真实动作，区分数学权益与模型判断。")}</p><small>{t("Cash & SNG \u00b7 Separate rankings")}</small></footer>
    </main></div></div>;
}

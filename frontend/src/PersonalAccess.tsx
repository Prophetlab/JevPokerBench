import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {Account} from './Account';
import {api} from './api';
import {t} from './i18n';
import {Budget} from './modelAccess';
import {personalSnapshot,savePersonalKeys,subscribePersonal} from './personalSession';

export function usePersonalAccess() {
    const {user,keys,agents,agentKeys}=useSyncExternalStore(subscribePersonal,personalSnapshot);
    const [accountBudget,setAccountBudget]=useState<{id:string;value:Budget}|null>(null),[error,setError]=useState('');
    const budget=accountBudget?.id===user?.id?accountBudget?.value||null:null;
    const version=useRef(0);
    const refreshBudget=async()=>{
        const id=user?.id,request=++version.current;
        if(!id){setAccountBudget(null);return;}
        try{const next=await api<Budget>('/api/auth/budget');if(request===version.current&&personalSnapshot().user?.id===id){setAccountBudget({id,value:next});setError('');}}
        catch(e){if(request===version.current&&personalSnapshot().user?.id===id){setAccountBudget(null);setError((e as Error).message);}}
    };
    useEffect(()=>{
        setAccountBudget(null);setError('');void refreshBudget();
        if(!user)return;
        const timer=setInterval(()=>void refreshBudget(),15000);
        const focus=()=>void refreshBudget();window.addEventListener('focus',focus);
        return()=>{version.current++;clearInterval(timer);window.removeEventListener('focus',focus);};
    },[user?.id]);
    return {user,keys,agents,agentKeys,budget,error,refreshBudget};
}
export type PersonalAccessState=ReturnType<typeof usePersonalAccess>;
export function PersonalAccess({access}:{access:PersonalAccessState}) {
    const {user,keys,budget,error,refreshBudget}=access;
    const [code,setCode]=useState(''),[draft,setDraft]=useState(keys),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[failure,setFailure]=useState('');
    useEffect(()=>{setDraft(keys);},[keys]);
    useEffect(()=>{setCode('');setNotice('');setFailure('');},[user?.id]);
    const redeem=async()=>{
        if(!user||busy||!code.trim())return;
        setBusy(true);setFailure('');setNotice('');
        try{await api<Budget>('/api/auth/invite','POST',{code:code.trim()});setCode('');await refreshBudget();setNotice('邀请码已兑换。');}
        catch(e){setFailure((e as Error).message);}finally{setBusy(false);}
    };
    return <section className="personal-access" aria-label={t('个人使用权限')}>
      <Account user={user}/>
      {user?<div className="personal-access-grid">
        <div><h3>{t('我的账户额度')}</h3>{budget?<><p className="access-balance">¥{budget.remaining_cny.toFixed(2)} <small>{t('剩余额度')}</small></p><p className="fine">{t('已用 ¥{0} / 额度 ¥{1}',budget.used_cny.toFixed(2),budget.limit_cny.toFixed(2))}</p><p className="fine">{t(budget.invited?'已获邀请':'尚未兑换邀请码')}</p></>:<p className="fine">{t('账户额度暂不可用。')}</p>}
        <form onSubmit={e=>{e.preventDefault();void redeem();}} className="invite-form"><label>{t('邀请码（可选）')}<input autoComplete="off" maxLength={200} value={code} onChange={e=>setCode(e.target.value)}/></label><button className="secondary" disabled={busy||!code.trim()}>{t(busy?'请稍候…':'兑换邀请码')}</button></form><p className="fine">{t('托管 DeepSeek 需要邀请且账户仍有余额。')}</p></div>
        <form className="personal-key-form" onSubmit={e=>{e.preventDefault();setFailure('');try{savePersonalKeys(draft);setNotice('个人密钥已保存到当前标签页会话。');}catch(e){setFailure((e as Error).message);}}}>
          <h3>{t('自备 API 密钥（可选）')}</h3><p className="fine">{t('支持自备 API 密钥，包括 Jev Official 和 DeepSeek。建议先在服务商设置消费上限。')}</p><p className="fine">{t('仅在当前浏览器会话保存；经服务器安全发送至服务商。费用由你的服务商收取。')}</p>
          <div className="form-grid">{(['jev','deepseek'] as const).map(provider=><label key={provider}>{t(provider==='jev'?'Jev API key':'DeepSeek API key')}<input type="password" autoComplete="off" spellCheck={false} value={draft[provider]} onChange={e=>setDraft({...draft,[provider]:e.target.value})}/></label>)}</div>
          <p className="fine">{t('仅用于你授权的请求，不写入服务器文件、数据库或日志，也不供其他用户使用。')} {t('每个账号独立保存，退出登录即清除。')}</p><div className="inline-controls"><button className="secondary">{t('保存个人密钥')}</button><button className="text-button" type="button" onClick={()=>{savePersonalKeys({jev:'',deepseek:''});setNotice('个人密钥已清除。');}}>{t('清除密钥')}</button></div>
        </form>
      </div>:<p className="fine">{t('登录后可兑换邀请码、使用个人密钥、获取建议和组局。')}</p>}
      {(failure||error)&&<p className="notice error" role="alert">{t(failure||error)}</p>}{notice&&<p className="fine" role="status">{t(notice)}</p>}
    </section>;
}

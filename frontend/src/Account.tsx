import {useEffect, useState} from 'react';
import {api} from './types';
import {t} from './i18n';
import type {Player} from './personalSession';

export function Account({user}:{user:Player|null|undefined}) {
    const [register,setRegister]=useState(false),[id,setId]=useState('');
    const [password,setPassword]=useState(''),[confirmation,setConfirmation]=useState('');
    const [busy,setBusy]=useState(false),[error,setError]=useState('');
    useEffect(()=>{
        let active=true;
        const refresh=()=>api<{user:Player|null}>('/api/auth/me').then(data=>{if(active){if(data.user)setId(data.user.id);}}).catch(e=>{if(active)setError(e.message);});
        void refresh();window.addEventListener('focus',refresh);
        return()=>{active=false;window.removeEventListener('focus',refresh);};
    },[]);
    const submit=async()=>{
        if(busy)return;
        if(register&&password!==confirmation){setError('两次密码不一致');return;}
        setBusy(true);setError('');
        try {
            await api<{user:Player}>(`/api/auth/${register?'register':'login'}`,'POST',{id,password});
            await api('/api/auth/me');setPassword('');setConfirmation('');setRegister(false);
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    const logout=async()=>{
        setBusy(true);setError('');
        try{await api('/api/auth/logout','POST',{});setPassword('');setConfirmation('');}
        catch(e){setError((e as Error).message);}finally{setBusy(false);}
    };
    return <section className="player-account" aria-label={t('玩家账号')}>
      {user?<div className="account-signed-in"><div><strong>{t('已登录：{0}',user.id)}</strong><p className="fine">{t('最近 3 局随账号保存，换浏览器登录也能继续。')}</p></div><button className="secondary" disabled={busy} onClick={()=>void logout()}>{t('退出登录')}</button></div>:<>
        <div className="account-tabs"><button className={!register?'selected':''} disabled={busy} onClick={()=>{setRegister(false);setError('');}}>{t('登录')}</button><button className={register?'selected':''} disabled={busy} onClick={()=>{setRegister(true);setError('');}}>{t('注册账号')}</button></div>
        <h2>{t(register?'创建你的玩家账号':'登录后使用个人功能')}</h2><p className="fine">{t('用 ID 和密码保存你的牌桌。每个账号独立管理自己的进度。')}</p>
        <form className="account-form" onSubmit={e=>{e.preventDefault();void submit();}}>
          <label>{t('玩家 ID')}<input required maxLength={40} autoComplete="username" value={id} onChange={e=>setId(e.target.value)}/></label>
          <label>{t('密码')}<input required type="password" minLength={register?8:1} maxLength={128} autoComplete={register?'new-password':'current-password'} value={password} onChange={e=>setPassword(e.target.value)}/></label>
          {register&&<label>{t('确认密码')}<input required type="password" minLength={8} maxLength={128} autoComplete="new-password" value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label>}
          <button className="primary" disabled={busy||user===undefined}>{t(busy?'请稍候…':register?'注册并登录':'登录')}</button>
        </form>{register&&<p className="fine">{t('ID 不区分大小写；密码至少 8 位。')}</p>}
      </>}{error&&<p className="notice error" role="alert">{t(error)}</p>}
      
    </section>;
}

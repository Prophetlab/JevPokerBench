import {clearPersonalSession, personalSnapshot, resolvePersonalUser} from './personalSession';
import type {BillingMode,Provider} from './modelAccess';
export type PersonalRequest={billingMode:BillingMode;providers:Provider[];agentIds?:string[]};
let authVersion=0;
let identityRequest=0;
export async function api<T=any>(path:string,method='GET',body?:unknown,headers:Record<string,string>={},personal?:PersonalRequest):Promise<T>{
    method=method.toUpperCase();
    if(method==='POST'&&/^\/api\/auth\/(login|register|logout)$/.test(path))authVersion++;
    const version=authVersion;
    const identity=path==='/api/auth/me'?++identityRequest:0;
    if(path==='/api/auth/logout'&&method==='POST')clearPersonalSession();
    const requestHeaders:Record<string,string>={'Content-Type':'application/json'};
    if(headers['X-Player-Token']&&/^\/api\/(rooms|runs)\/[^/]+\//.test(path))requestHeaders['X-Player-Token']=headers['X-Player-Token'];
    const keyRoute=path==='/api/rooms'||path==='/api/advisor/advise'||/^\/api\/rooms\/[^/]+\/(action|start)$/.test(path)||/^\/api\/runs\/[^/]+\/start$/.test(path);
    if(method==='POST'&&keyRoute&&personal?.billingMode==='personal'){
        const account=personalSnapshot().user?.id;
        if(!account)throw new Error('登录后才能使用个人密钥。');
        const verified=await api('/api/auth/me');
        const {user,keys,agentKeys}=personalSnapshot();
        if(version!==authVersion||verified.user?.id!==account||user?.id!==account)throw new Error('账号已更改，请重新确认个人密钥。');
        if(personal.providers.some(provider=>provider!=='systemone'&&!keys[provider]))throw new Error('请先保存所选模型的个人密钥。');
        if(personal.providers.includes('jev')&&keys.jev)requestHeaders['X-Jev-Key']=keys.jev;
        if(personal.providers.includes('deepseek')&&keys.deepseek)requestHeaders['X-DeepSeek-Key']=keys.deepseek;
        if(personal.agentIds?.length&&path!=='/api/advisor/advise'){
            const ids=[...new Set(personal.agentIds)];
            if(ids.length>9||ids.some(id=>!agentKeys[id]||agentKeys[id].length>1024))throw new Error('请先保存所选 Agent 的个人密钥。');
            requestHeaders['X-Agent-Keys']=JSON.stringify(Object.fromEntries(ids.map(id=>[id,agentKeys[id]])));
        }
    }
    const r=await fetch(path,{method,credentials:'same-origin',headers:requestHeaders,...(body===undefined?{}:{body:JSON.stringify(body)})});
    if(r.status===401&&version===authVersion){authVersion++;clearPersonalSession();}
    const raw=await r.text();
    let data:any;
    try{data=raw?JSON.parse(raw):null;}catch{throw Object.assign(new Error(`请求失败（HTTP ${r.status}）`),{status:r.status});}
    if(!r.ok){
        const detail=data?.detail;
        const message=typeof detail==='string'?detail:Array.isArray(detail)?detail.map(e=>[e.loc?.filter((part:string|number)=>part!=='body').join('.'),String(e.msg||'输入无效').replace(/^Value error, /,'')].filter(Boolean).join('：')).join('；'):`请求失败（HTTP ${r.status}）`;
        throw Object.assign(new Error(message),{status:r.status});
    }
    if(path==='/api/auth/me'&&method==='GET'&&version===authVersion&&identity===identityRequest)resolvePersonalUser(data.user);
    return data;
}

import {CustomAgent,validateCustomAgent,validateAgentKey} from './customAgents';
export type Player = {id:string};
export type PersonalKeys = {jev:string;deepseek:string};
const emptyKeys = ():PersonalKeys => ({jev:'',deepseek:''});
const namespace = (id:string) => `pokerbench-personal-keys:${encodeURIComponent(id)}`;
let session:{user:Player|null|undefined;keys:PersonalKeys;agents:CustomAgent[];agentKeys:Record<string,string>} = {user:undefined,keys:emptyKeys(),agents:[],agentKeys:{}};
const listeners = new Set<()=>void>();
export const personalSnapshot = () => session;
export const subscribePersonal = (listener:()=>void) => {listeners.add(listener);return()=>{listeners.delete(listener);};};
const changed = () => listeners.forEach(listener=>listener());

// Called only after /auth/me resolves the cookie-authenticated account.
export function resolvePersonalUser(user:Player|null) {
    if(!user){clearPersonalSession();return;}
    if(session.user?.id===user?.id && session.user!==undefined)return;
    if(session.user)sessionStorage.removeItem(namespace(session.user.id));
    let keys=emptyKeys();
    let agents:CustomAgent[]=[],agentKeys:Record<string,string>={};
    if(user){
        try{const saved=JSON.parse(sessionStorage.getItem(namespace(user.id))||'{}');keys={jev:typeof saved.jev==='string'?saved.jev:'',deepseek:typeof saved.deepseek==='string'?saved.deepseek:''};
            for(const [id,key] of Object.entries(saved.agentKeys||{}).slice(0,9)){validateAgentKey(id,key as string);agentKeys[id]=key as string;}
            for(const agent of (Array.isArray(saved.agents)?saved.agents:[]).slice(0,9)){
                const key=saved.agentKeys?.[agent.id];validateCustomAgent(agent,key||'');agents.push(agent);agentKeys[agent.id]=key;
            }
        }
        catch{sessionStorage.removeItem(namespace(user.id));}
    }
    session={user,keys,agents,agentKeys};changed();
}
function persist(){
    if(!session.user)return;
    if(session.keys.jev||session.keys.deepseek||Object.keys(session.agentKeys).length)sessionStorage.setItem(namespace(session.user.id),JSON.stringify({...session.keys,agents:session.agents,agentKeys:session.agentKeys}));
    else sessionStorage.removeItem(namespace(session.user.id));
    changed();
}
export function savePersonalKeys(keys:PersonalKeys) {
    if(!session.user)throw new Error('登录后才能保存个人密钥。');
    const clean={jev:keys.jev.trim(),deepseek:keys.deepseek.trim()};
    session={...session,keys:clean};persist();
}
export function saveCustomAgent(agent:CustomAgent,key:string){
    if(!session.user)throw new Error('登录后才能保存个人密钥。');
    validateCustomAgent(agent,key);
    const existing=session.agents.find(a=>a.id===agent.id);
    if(existing&&(existing.endpoint!==agent.endpoint||existing.model!==agent.model||(existing.api_format||'chat_completions')!==(agent.api_format||'chat_completions')))throw new Error('修改端点、模型或协议时，请添加为新 Agent。');
    if(!session.agentKeys[agent.id]&&Object.keys(session.agentKeys).length>=9)throw new Error('最多保存 9 个自定义 Agent。');
    session={...session,agents:existing?session.agents.map(a=>a.id===agent.id?agent:a):[...session.agents,agent],agentKeys:{...session.agentKeys,[agent.id]:key}};persist();
}
export function removeCustomAgent(id:string){
    const agentKeys={...session.agentKeys};delete agentKeys[id];
    session={...session,agents:session.agents.filter(a=>a.id!==id),agentKeys};persist();
}
export function saveRoomAgentKey(id:string,key:string){
    if(!session.user)throw new Error('登录后才能保存个人密钥。');
    validateAgentKey(id,key);
    if(!session.agentKeys[id]&&Object.keys(session.agentKeys).length>=9)throw new Error('最多保存 9 个自定义 Agent。');
    session={...session,agentKeys:{...session.agentKeys,[id]:key}};persist();
}
export function clearPersonalSession() {
    // Also clear namespaces restored in this tab before identity was resolved.
    for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('pokerbench-personal-keys:'))sessionStorage.removeItem(key);}
    session={user:null,keys:emptyKeys(),agents:[],agentKeys:{}};changed();
}

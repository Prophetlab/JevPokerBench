import type {Entry} from './types';
import type {PersonalKeys} from './personalSession';
export type BillingMode='hosted'|'personal';
export type Budget={invited:boolean;limit_cny:number;used_cny:number;remaining_cny:number;exhausted?:boolean};
export type Provider='jev'|'deepseek'|'systemone';
export function providerOf(entry:Entry):Provider|null {
    if(entry.proxy||/luna/i.test(`${entry.id} ${entry.name} ${entry.model||''}`))return null;
    return ['jev','deepseek','systemone'].includes(entry.provider)?entry.provider as Provider:null;
}
export function modelAvailable(entry:Entry,mode:BillingMode,budget:Budget|null,keys:PersonalKeys) {
    const provider=providerOf(entry);
    if(!provider||entry.enabled===false)return false;
    if(mode==='personal')return provider!=='systemone'&&!!keys[provider];
    if(entry.ready!==true)return false;
    const invited=budget?.invited===true&&budget.remaining_cny>0;
    return (provider!=='deepseek'&&!entry.requires_invitation&&entry.access!=='requires_invitation')||invited;
}

export type ApiFormat='chat_completions'|'responses'|'anthropic';
export type CustomAgent={id:string;name:string;endpoint:string;model:string;api_format?:ApiFormat};
export function validateAgentKey(id:string,key:string){
    if(!/^custom-[a-z0-9_-]{1,32}$/.test(id)||!key||key.length>1024||/[^\x21-\x7e]/.test(key))throw new Error('Agent ID 或密钥格式无效。');
}
export function validateCustomAgent(agent:CustomAgent,key:string) {
    validateAgentKey(agent.id,key);
    if(!agent.name.trim()||agent.name.length>60||!agent.model.trim()||agent.model.length>160)throw new Error('请填写有效的名称和模型名称。');
    let url:URL;
    try{url=new URL(agent.endpoint);}catch{throw new Error('端点必须是公开的 HTTPS URL。');}
    const kind=agent.api_format||'chat_completions';
    if(!['chat_completions','responses','anthropic'].includes(kind))throw new Error('请选择支持的 API 协议。');
    const suffixes={chat_completions:'/chat/completions',responses:'/responses',anthropic:'/messages'};
    for(const [protocol,suffix] of Object.entries(suffixes))if(url.pathname.replace(/\/+$/,'').endsWith(suffix)&&protocol!==kind)throw new Error('端点路径与所选 API 协议不一致。');
    const host=url.hostname.toLowerCase(),parts=host.split('.').map(Number);
    const privateIP=parts.length===4&&parts.every(Number.isFinite)&&(parts[0]===0||parts[0]===10||parts[0]===127||parts[0]>=224||parts[0]===169&&parts[1]===254||parts[0]===172&&parts[1]>=16&&parts[1]<=31||parts[0]===192&&parts[1]===168||parts[0]===100&&parts[1]>=64&&parts[1]<=127);
    if(agent.endpoint.length>2048||url.protocol!=='https:'||url.username||url.password||url.hash||url.search||!host.includes('.')||host.includes(':')||/(^|\.)(localhost|local|internal|test|invalid|example)$/.test(host)||privateIP)throw new Error('端点必须是公开的 HTTPS URL。');
}

import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';

const bundle=await build({stdin:{contents:"export * from './src/api';export * from './src/personalSession';export * from './src/modelAccess';export * from './src/tableAudio';export * from './src/handResults';export * from './src/customAgents';",resolveDir:process.cwd()},bundle:true,write:false,format:'esm',platform:'node'});
const app=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
class Storage {
  data=new Map(); get length(){return this.data.size;} key(i){return [...this.data.keys()][i]??null;}
  getItem(k){return this.data.get(k)??null;}setItem(k,v){this.data.set(k,String(v));}removeItem(k){this.data.delete(k);}
}
globalThis.sessionStorage=new Storage();
let currentUser={id:'Alice'},requests=[];
const response=(value,status=200)=>new Response(JSON.stringify(value),{status});
function setup(){sessionStorage=new Storage();app.clearPersonalSession();currentUser={id:'Alice'};requests=[];globalThis.fetch=async(path,options)=>{requests.push({path,...options});return response(path==='/api/auth/me'?{user:currentUser}:{});};}
async function signIn(){await app.api('/api/auth/me');app.savePersonalKeys({jev:'jev-test-only',deepseek:'ds-test-only'});}
const personal={billingMode:'personal',providers:['jev','deepseek']};

test('Keys require resolved identity, are scoped in sessionStorage, and clear across accounts/logout',async()=>{
  setup();assert.throws(()=>app.savePersonalKeys({jev:'x',deepseek:''}));
  await signIn();assert.equal(sessionStorage.length,1);assert.match(sessionStorage.key(0),/:Alice$/);
  currentUser={id:'Bob'};await app.api('/api/auth/me');assert.deepEqual(app.personalSnapshot().keys,{jev:'',deepseek:''});assert.equal(sessionStorage.length,0);
  app.savePersonalKeys({jev:'bob-test-only',deepseek:''});await app.api('/api/auth/logout','POST',{});assert.equal(sessionStorage.length,0);assert.equal(app.personalSnapshot().user,null);
});
test('Only personal POST create, action, start and advise operations receive matching provider keys',async()=>{
  setup();await signIn();sessionStorage.setItem('pb-token','must-never-send');
  const allowed=['/api/rooms','/api/rooms/r/action','/api/rooms/r/start','/api/runs/r/start','/api/advisor/advise'];
  for(const path of allowed){await app.api(path,'POST',{billing_mode:'personal'},{},personal);const req=requests.at(-1);assert.equal(req.headers['X-Jev-Key'],'jev-test-only');assert.equal(req.headers['X-DeepSeek-Key'],'ds-test-only');assert.equal(req.headers.Authorization,undefined);}
  await app.api('/api/advisor/advise','POST',{}, {},{billingMode:'personal',providers:['jev']});assert.equal(requests.at(-1).headers['X-DeepSeek-Key'],undefined);
  const blocked=['/api/rooms/r/state','/api/rooms/mine','/api/rooms/models','/api/rooms/r/visit','/api/rooms/r/claim','/api/rooms/r/kick/seat','/api/rooms/r/pause','/api/runs/r/pause','/api/runs/r/limit','/api/runs','/api/entries','/api/advisor/preview','/api/auth/budget','/api/auth/invite','https://other.invalid/api/advisor/advise'];
  for(const path of blocked){await app.api(path,'POST',{}, {'X-Jev-Key':'injected','Authorization':'Bearer injected'},personal);assert.equal(requests.at(-1).headers['X-Jev-Key'],undefined,path);assert.equal(requests.at(-1).headers['X-DeepSeek-Key'],undefined,path);assert.equal(requests.at(-1).headers.Authorization,undefined,path);}
  for(const method of ['GET','PATCH','PUT','DELETE']){await app.api('/api/advisor/advise',method,undefined,{},personal);assert.equal(requests.at(-1).headers['X-Jev-Key'],undefined);}
  await app.api('/api/advisor/advise','POST',{billing_mode:'hosted'},{},{billingMode:'hosted',providers:['jev']});assert.equal(requests.at(-1).headers['X-Jev-Key'],undefined);
});
test('Clearing a personal key fails closed; action/start resend keys at every boundary',async()=>{
  setup();await signIn();
  for(let i=0;i<3;i++)for(const path of ['/api/runs/room/start','/api/rooms/room/action'])await app.api(path,'POST',{}, {},personal);
  assert.equal(requests.filter(r=>r.method==='POST'&&r.headers['X-Jev-Key']).length,6);
  app.savePersonalKeys({jev:'',deepseek:'ds-test-only'});const count=requests.filter(r=>r.method==='POST').length;
  await assert.rejects(app.api('/api/advisor/advise','POST',{billing_mode:'personal'}, {},{billingMode:'personal',providers:['jev']}),/个人密钥/);
  assert.equal(requests.filter(r=>r.method==='POST').length,count);
});
test('Changed identity cancels a keyed request and clears the previous account key',async()=>{
  setup();await signIn();currentUser={id:'Bob'};
  await assert.rejects(app.api('/api/rooms','POST',{}, {},personal),/账号已更改/);
  assert.equal(requests.filter(r=>r.method==='POST').length,0);assert.equal(sessionStorage.length,0);
});
test('A stale /me cannot restore identity or keys after logout',async()=>{
  setup();await signIn();let finish;
  globalThis.fetch=async(path)=>path==='/api/auth/me'?new Promise(resolve=>{finish=resolve;}):response({});
  const pending=app.api('/api/auth/me');await app.api('/api/auth/logout','POST',{});finish(response({user:{id:'Alice'}}));await pending;
  assert.equal(app.personalSnapshot().user,null);assert.equal(sessionStorage.length,0);
});
test('Signed-out /me clears stored namespaces even before this tab resolved an identity',async()=>{
  setup();sessionStorage.setItem('pokerbench-personal-keys:Alice',JSON.stringify({jev:'stale',deepseek:''}));currentUser=null;await app.api('/api/auth/me');assert.equal(sessionStorage.length,0);assert.equal(app.personalSnapshot().user,null);
});
test('Out-of-order /me results cannot overwrite the newest identity',async()=>{
  setup();let finish;globalThis.fetch=()=>new Promise(resolve=>{finish=resolve;});
  const first=app.api('/api/auth/me'),finishFirst=finish;const second=app.api('/api/auth/me');
  finish(response({user:{id:'Bob'}}));await second;finishFirst(response({user:{id:'Alice'}}));await first;assert.equal(app.personalSnapshot().user.id,'Bob');
});
test('Logout during personal-key preflight prevents the mutation',async()=>{
  setup();await signIn();let finish;globalThis.fetch=async(path,opts)=>{requests.push({path,...opts});return path==='/api/auth/me'?new Promise(resolve=>{finish=resolve;}):response({});};
  const pending=app.api('/api/rooms/r/action','POST',{}, {},personal);await app.api('/api/auth/logout','POST',{});finish(response({user:{id:'Alice'}}));await assert.rejects(pending,/账号已更改/);assert.ok(!requests.some(r=>r.path==='/api/rooms/r/action'));
});
test('Hosted and personal access enforce provider, invitation, balance and matching key rules',()=>{
  const entry=provider=>({id:provider,name:provider,provider,ready:true});const empty={jev:'',deepseek:''};const active={invited:true,remaining_cny:1};
  for(const p of ['systemone','jev'])assert.equal(app.modelAvailable(entry(p),'hosted',null,empty),true);
  for(const b of [null,{invited:false,remaining_cny:5},{invited:true,remaining_cny:0}])assert.equal(app.modelAvailable(entry('deepseek'),'hosted',b,empty),false);
  assert.equal(app.modelAvailable(entry('deepseek'),'hosted',active,empty),true);
  assert.equal(app.modelAvailable({...entry('jev'),requires_invitation:true},'hosted',null,empty),false);
  assert.equal(app.modelAvailable(entry('deepseek'),'personal',null,{jev:'key',deepseek:''}),false);
  assert.equal(app.modelAvailable({...entry('deepseek'),ready:false},'personal',null,{jev:'',deepseek:'own'}),true);
  assert.equal(app.modelAvailable(entry('systemone'),'personal',active,{jev:'key',deepseek:'key'}),false);
  for(const bad of [{...entry('jev'),name:'Luna'},entry('openai_compatible'),{...entry('jev'),proxy:true},{...entry('jev'),enabled:false}])for(const mode of ['hosted','personal'])assert.equal(app.modelAvailable(bad,mode,active,{jev:'key',deepseek:'key'}),false);
});
test('Sounds are quiet by default and deduplicate polling, replay seeks and remounts',()=>{
  assert.equal(app.audioSnapshot().volume,0.12);assert.equal(app.audioSnapshot().unlocked,false);
  const seen=new app.SoundProgress();assert.equal(seen.advance('room',1,0),false);assert.equal(seen.advance('room',1,1),true);assert.equal(seen.advance('room',1,1),false);assert.equal(seen.advance('room',1,0),false);assert.equal(seen.advance('room',1,1),false);assert.equal(seen.advance('room',1,2),true);assert.equal(seen.advance('room',2,0),false);
});
test('Winner treatment includes split/side-pot recipients with zero or negative net payoff',()=>{
  const state={complete:true,seats:[{id:'winner',folded:false,committed:100,payoff:200},{id:'split',folded:false,committed:100,payoff:0},{id:'side',folded:false,committed:100,payoff:-50},{id:'loser',folded:false,committed:100,payoff:-100},{id:'folded',folded:true,committed:100,payoff:-10}]};
  assert.deepEqual(app.handWinners(state).map(s=>s.id),['winner','split','side']);assert.deepEqual(app.handWinners({...state,complete:false}),[]);
});
test('Custom agents validate public HTTPS, model/name/key lengths',()=>{
  const agent={id:'custom-test',name:'My GPT',endpoint:'https://api.openai.com/v1',model:'gpt-4.1'};
  assert.doesNotThrow(()=>app.validateCustomAgent(agent,'fake-key'));
  for(const endpoint of ['http://api.openai.com','https://localhost','https://127.1','https://10.0.0.1/v1','https://172.20.1.1','https://192.168.1.2','https://169.254.1.1','https://[::1]','https://name:secret@api.openai.com','https://api.openai.com?key=secret','https://api.openai.com/'+ 'x'.repeat(2048)])assert.throws(()=>app.validateCustomAgent({...agent,endpoint},'fake-key'),undefined,endpoint);
  for(const change of [{id:'other'},{name:'x'.repeat(61)},{model:'x'.repeat(161)}])assert.throws(()=>app.validateCustomAgent({...agent,...change},'fake-key'));
  for(const key of ['', 'bad key','x'.repeat(1025)])assert.throws(()=>app.validateCustomAgent(agent,key));
});
test('Custom definitions and keys are account-scoped, limited to 9, and endpoint identity stays frozen',async()=>{
  setup();await signIn();const agent={id:'custom-1',name:'My model',endpoint:'https://api.openai.com/v1',model:'gpt-4.1'};
  app.saveCustomAgent(agent,'custom-test-key');app.saveCustomAgent(agent,'replacement-key');assert.equal(app.personalSnapshot().agents.length,1);
  assert.throws(()=>app.saveCustomAgent({...agent,endpoint:'https://api.deepseek.com'},'new-key'));
  for(let n=2;n<=9;n++)app.saveCustomAgent({...agent,id:`custom-${n}`},'test-key');assert.throws(()=>app.saveCustomAgent({...agent,id:'custom-10'},'test-key'));
  currentUser={id:'Bob'};await app.api('/api/auth/me');assert.equal(app.personalSnapshot().agents.length,0);assert.deepEqual(app.personalSnapshot().agentKeys,{});assert.equal(sessionStorage.length,0);
});
test('Custom keys resend by credential_id on room writes only, never bodies, hosted mode, or Advisor',async()=>{
  setup();await signIn();app.saveRoomAgentKey('custom-1','custom-test-key');
  const options={billingMode:'personal',providers:[],agentIds:['custom-1','custom-1']};
  for(const path of ['/api/rooms','/api/runs/r/start','/api/rooms/r/action']){
    await app.api(path,'POST',{billing_mode:'personal'},{},options);assert.deepEqual(JSON.parse(requests.at(-1).headers['X-Agent-Keys']),{'custom-1':'custom-test-key'});assert.ok(!requests.at(-1).body.includes('custom-test-key'));
  }
  for(const [path,method] of [['/api/rooms/r/state','GET'],['/api/rooms/r/kick/seat','POST'],['/api/runs/r/pause','POST'],['/api/advisor/advise','POST']]){await app.api(path,method,{}, {},options);assert.equal(requests.at(-1).headers['X-Agent-Keys'],undefined);}
  await app.api('/api/rooms','POST',{billing_mode:'hosted'},{},{...options,billingMode:'hosted'});assert.equal(requests.at(-1).headers['X-Agent-Keys'],undefined);
  app.removeCustomAgent('custom-1');await assert.rejects(app.api('/api/rooms/r/action','POST',{}, {},options));
});

test('API formats accept matching complete routes and prevent changing a saved protocol',async()=>{
  setup();await signIn();const base={id:'custom-protocol',name:'Model',model:'model'};
  for(const [api_format,endpoint] of [['chat_completions','https://api.openai.com/v1/chat/completions'],['responses','https://api.openai.com/v1/responses'],['anthropic','https://api.anthropic.com/v1/messages']])assert.doesNotThrow(()=>app.validateCustomAgent({...base,api_format,endpoint},'test-key'));
  assert.throws(()=>app.validateCustomAgent({...base,api_format:'anthropic',endpoint:'https://api.openai.com/v1/responses'},'test-key'),/协议/);
  const agent={...base,endpoint:'https://api.openai.com/v1'};app.saveCustomAgent(agent,'test-key');
  assert.throws(()=>app.saveCustomAgent({...agent,api_format:'responses'},'test-key'),/协议/);
});

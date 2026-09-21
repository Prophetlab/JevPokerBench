import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';

// All API traffic is mocked: this suite cannot run games or bill a provider.
const base=process.env.PB_BASE_URL||'http://127.0.0.1:8819';
const browser=await chromium.launch({headless:true,...(process.env.PB_BROWSER_EXECUTABLE?{executablePath:process.env.PB_BROWSER_EXECUTABLE}:{})});
const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
await context.addInitScript(()=>{
  window.__audio={contexts:0,tones:0};const Native=window.AudioContext;
  window.AudioContext=class extends Native{constructor(...args){super(...args);window.__audio.contexts++;}createOscillator(){window.__audio.tones++;return super.createOscillator();}};
});
const page=await context.newPage(),errors=[],requests=[],unexpected=[];
page.on('pageerror',e=>errors.push(e.message));
const model=(id,provider=id,extra={})=>({id,name:id==='jev'?'Jev Official':id==='deepseek'?'DeepSeek':id==='systemone'?'System One':id,provider,model:id,color:'#347456',ready:true,proxy:false,...extra});
const models=[model('jev'),model('systemone'),model('deepseek','deepseek',{requires_invitation:true}),model('gpt-luna','jev'),model('unready','jev',{ready:false})];
const entries=models.slice(0,3);
const seat=(id,i,extra={})=>({id,name:id,seat:i,stack:20000,bet:0,committed:100,folded:false,all_in:false,cards:['??','??'],position:i===0?'BTN':i===1?'SB':'BB',payoff:null,...extra});
const state={hand_number:1,street:'preflop',button:0,actor:'jev',complete:false,board:[],seats:entries.map((e,i)=>seat(e.id,i)),pot:300,pots:[{amount:300,eligible:entries.map(e=>e.id)}],small_blind:50,big_blind:100,ante:0,to_call:0,legal_actions:[{id:'check',kind:'check',label:'过牌',pay:0,to:0,all_in:false}]};
const settled={...state,actor:null,complete:true,street:'complete',board:['As','7h','2c','Kd','3s'],legal_actions:[],seats:[seat('jev',0,{payoff:150}),seat('systemone',1,{payoff:-50}),seat('deepseek',2,{payoff:-100})]};
const events=[{index:0,type:'deal',label:'准备开局',snapshot:state},{index:1,type:'action',label:'过牌',action:{player:'jev',id:'check',kind:'check',pay:0,to:0,all_in:false,label:'过牌'},snapshot:state},{index:2,type:'board',label:'公共牌 As 7h 2c',snapshot:{...state,street:'flop',board:['As','7h','2c']}},{index:3,type:'settlement',label:'本手结算',snapshot:settled}];
const run={llm_adapter:{version:'0.2.0'},provider_retries:17,id:'benchmark',name:'Public benchmark',mode:'cash',created:1,status:'hand_limit',message:'已完成',hands_played:1,max_hands:1,active_hand:null,orbit:1,proxy:false,champion:null,entries,standings:entries.map((e,i)=>({id:e.id,name:e.name,stack:20000,profit:100-i*100,reserve:100000,equity:120000,rank:i+1,place:null,bb100:1,rebuys:0})),events:[],history:Array.from({length:15},(_,i)=>({hand:i,values:{jev:i*100,systemone:i*-60,deepseek:i*-40},ev_values:{jev:i*80,systemone:i*-40,deepseek:i*-40}})),big_blind:100,timing:{models:[]},highlights:[]};
const series={id:'series',status:'paused',message:'',target:5,completed:1,current_run_id:'benchmark',current_number:2,standings:entries.map((e,i)=>({...e,rank:i+1,mean_place:i+1,completed:1,wins:i===0?1:0}))};
let user=null,invited=false,remaining=5,room=null,roomEvents=[],hand=1,retired=false;
const budget=()=>({invited,limit_cny:5,used_cny:5-remaining,remaining_cny:remaining,exhausted:remaining<=0});
await page.route('**/api/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.replace(/^\/pokerbench(?=\/)/,''),method=req.method(),body=req.postData()?JSON.parse(req.postData()):null;
  requests.push({path,method,body,headers:req.headers()});
  const send=(json,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(json)});
  if(path.endsWith('/stream'))return route.fulfill({contentType:'text/event-stream',body:': idle\n\n'});
  if(path==='/api/runs')return send([run]);
  if(path==='/api/series')return send([series]);
  if(path==='/api/rooms/models')return send(models);
  if(path==='/api/auth/me')return send({user});
  if(path==='/api/auth/login'||path==='/api/auth/register'){user={id:body.id};return send({user});}
  if(path==='/api/auth/logout'){user=null;return send({ok:true});}
  if(path==='/api/auth/budget')return send(budget());
  if(path==='/api/auth/invite'){invited=true;return send(budget());}
  if(path==='/api/advisor/preview')return send({state,can_advise:true,math:{available:true,equity:.5,win:.45,tie:.1,ci95:[.48,.52],pot_odds:.2,samples:3000,assumption:'测试'}});
  if(path==='/api/advisor/advise')return send({decision:{model:body.model_entry_id,latency:.2,answers:{action:{probabilities:{check:1},choice:'check',confidence:1}}}});
  if(path==='/api/runs/benchmark/hands')return send([{number:1,complete:true}]);
  if(path==='/api/runs/benchmark/hands/1')return send({events,complete:true});
  if(path==='/api/rooms/mine')return send(room?[room]:[]);
  if(path==='/api/rooms'&&method==='POST'){
    const opponents=body.model_ids.map((id,index)=>models.find(m=>m.id===id)||{...body.custom_agents.find(a=>a.id===id),id:`${id}-seat-${index}`,provider:'openai_compatible',credential_id:id,color:'#9a753a'});
    room={...run,id:'personal-room',name:body.name,human_player_id:'human',billing_mode:body.billing_mode,status:'ready',max_hands:100,hands_played:0,entries:[{id:'human',name:user.id,provider:'human',color:'#555'},...opponents],standings:[{...run.standings[0],id:'human',name:user.id},...opponents.map(e=>({...run.standings[0],id:e.id,name:e.name}))]};return send({run:room});
  }
  if(path==='/api/rooms/personal-room/visit')return send({ok:true});
  if(path==='/api/runs/personal-room/start'){
    room={...room,status:'waiting_human',active_hand:hand};roomEvents=[{...events[0],snapshot:{...state,hand_number:hand,actor:'human',seats:room.entries.map((e,i)=>seat(e.id,i))}}];return send(room);
  }
  if(path==='/api/rooms/personal-room/action'){
    room={...room,status:'waiting_next_hand',hands_played:hand};roomEvents=[...roomEvents,{...events[3],snapshot:{...settled,hand_number:hand,seats:room.entries.map((e,i)=>seat(e.id,i,{payoff:i===0?100:-100}))}}];hand++;return send(room);
  }
  if(path==='/api/runs/personal-room/pause'){room={...room,status:'paused'};return send(room);}
  if(path==='/api/rooms/personal-room/kick/jev')return send({ok:true});
  if(path==='/api/rooms/personal-room/state')return send({run:retired?{...room,standings:room.standings.map(s=>s.id==='deepseek'?{...s,retired:true,retired_reason:'timeout_limit'}:s)}:room,record:roomEvents.length?{events:roomEvents,complete:room.status==='waiting_next_hand'}:null,action_count:roomEvents.length,can_act:room.status==='waiting_human'});
  unexpected.push({path,method});return send({detail:'Unexpected test request'},404);
});
await fs.mkdir('test-results',{recursive:true});
const nav=async name=>page.getByRole('button',{name,exact:true}).click();
const setMode=async value=>page.getByLabel('Funding mode',{exact:true}).selectOption(value);
const setKey=async(provider,value)=>{await page.getByLabel(`${provider} API key`,{exact:true}).fill(value);await nav('Save personal keys');};
const options=()=>page.getByLabel('Advice model',{exact:true}).locator('option').allTextContents();
const lastPost=path=>requests.filter(r=>r.path===path&&r.method==='POST').at(-1);
try{
  await page.goto(base);await page.getByRole('heading',{name:'Two formats. Two podiums.'}).waitFor();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');assert.equal(await page.locator('nav button').count(),4);
  assert.equal(await page.locator('dialog,.modal,.registry,.player-directory').count(),0);
  assert.equal(await page.locator('a[href*="/export"]').count(),0);
  assert.equal(await page.locator('.audit-table[open]').count(),0);
  assert.ok(!/Adapter 0.2.0|17 retries|1.5×IQR/.test(await page.locator('main').innerText()));
  assert.ok(!/API ¥|New match|Models & endpoints|Pause series|Continue series/.test(await page.locator('main').innerText()));
  assert.equal(await page.evaluate(()=>window.__audio.contexts),0);assert.equal(await page.getByLabel('Volume',{exact:true}).inputValue(),'12');
  assert.equal(requests.filter(r=>r.method!=='GET').length,0);
  await page.screenshot({animations:'disabled',path:'test-results/public-overview.png',fullPage:true});
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();await page.getByRole('button',{name:'Reset view',exact:true}).click();
  await nav('Hand replay');await page.locator('.action-spotlight').waitFor();await nav('Next step');
  const tones=await page.evaluate(()=>window.__audio.tones);assert.ok(tones>0);
  await nav('Previous step');await nav('Next step');assert.equal(await page.evaluate(()=>window.__audio.tones),tones);
  await page.getByLabel('Hand timeline').fill('3');await page.locator('.hand-result').waitFor();
  assert.equal(await page.locator('.hand-winner').count(),2);
  assert.ok(await page.locator('.dealer-button').evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=22));
  assert.equal(requests.filter(r=>r.method!=='GET').length,0);
  await page.screenshot({animations:'disabled',path:'test-results/public-replay-winners.png',fullPage:true});
  await nav('Mute sounds');const mutedTones=await page.evaluate(()=>window.__audio.tones);await page.getByLabel('Hand timeline').fill('0');await page.getByLabel('Hand timeline').fill('3');assert.equal(await page.evaluate(()=>window.__audio.tones),mutedTones);
  await nav('Hand advisor');await page.getByText('Current input validated',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Advice model',{exact:true}).inputValue(),'jev');assert.ok(!(await options()).some(x=>/Luna|DeepSeek|unready/i.test(x)));
  assert.equal(await page.getByRole('button',{name:'Get next-move advice',exact:true}).isDisabled(),true);
  await page.getByLabel('Player ID',{exact:true}).fill('Alice');await page.getByLabel('Password',{exact:true}).fill('password123');await page.locator('.account-form').getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByText('Signed in as Alice',{exact:true}).waitFor();
  await page.getByLabel('Invite code (optional)').fill('test-invite');await nav('Redeem invite');await page.getByRole('option',{name:'DeepSeek',exact:true}).waitFor({state:'attached'});assert.deepEqual(lastPost('/api/auth/invite').body,{code:'test-invite'});
  await setKey('Jev','jev-browser-test');await setKey('DeepSeek','ds-browser-test');
  assert.equal(await page.getByLabel('Jev API key').getAttribute('type'),'password');
  assert.ok(await page.getByText('Kept for this browser session; sent securely to the provider through the server. Your provider bills you.',{exact:true}).isVisible());
  await setMode('personal');await page.getByText('Current input validated',{exact:true}).waitFor();await nav('Get next-move advice');await page.locator('.model-advice .prob-list').waitFor();
  assert.equal(lastPost('/api/advisor/advise').body.billing_mode,'personal');assert.equal(lastPost('/api/advisor/advise').headers['x-jev-key'],'jev-browser-test');assert.equal(lastPost('/api/advisor/advise').headers['x-deepseek-key'],undefined);
  await nav('Clear keys');await page.getByText('Current input validated',{exact:true}).waitFor();assert.equal(await page.getByLabel('Funding mode').inputValue(),'personal');assert.equal(await page.getByRole('button',{name:'Get next-move advice',exact:true}).isDisabled(),true);
  await setKey('Jev','jev-browser-test');await setKey('DeepSeek','ds-browser-test');await setMode('hosted');await page.getByText('Current input validated',{exact:true}).waitFor();await nav('Get next-move advice');await page.locator('.model-advice .prob-list').waitFor();assert.equal(lastPost('/api/advisor/advise').headers['x-jev-key'],undefined);assert.equal(lastPost('/api/advisor/advise').body.billing_mode,'hosted');
  await nav('Play with models');await page.getByLabel('Funding mode').waitFor();await setMode('personal');await page.waitForFunction(()=>document.querySelector('output[aria-label="Jev Official seat count"]')?.textContent==='1');await page.getByRole('button',{name:'Add a DeepSeek seat',exact:true}).click();
  await page.getByLabel('Agent display name').fill('My own GPT');await page.getByLabel('Model name',{exact:true}).fill('gpt-4.1');await page.getByLabel('Public HTTPS endpoint').fill('http://localhost');await page.getByLabel('Agent API key',{exact:true}).fill('custom-browser-key');await nav('Save agent');await page.getByRole('alert').filter({hasText:'public HTTPS'}).waitFor();
  await page.getByLabel('Public HTTPS endpoint').fill('https://api.openai.com/v1');await nav('Save agent');await page.getByRole('button',{name:'Add a My own GPT seat',exact:true}).click();
  await nav('Create table & take a seat');await page.getByRole('button',{name:'Start game',exact:true}).waitFor();
  const create=lastPost('/api/rooms');assert.equal(create.body.custom_agents.length,1);const customId=create.body.custom_agents[0].id;assert.equal(create.body.model_ids.filter(id=>id===customId).length,2);assert.deepEqual(JSON.parse(create.headers['x-agent-keys']),{[customId]:'custom-browser-key'});assert.ok(!JSON.stringify(create.body).includes('custom-browser-key'));assert.equal(create.body.billing_mode,'personal');assert.equal(create.body.run_budget_cny,20);assert.equal(create.headers['x-jev-key'],'jev-browser-test');assert.equal(create.headers['x-deepseek-key'],'ds-browser-test');
  await nav('Unmute sounds');await nav('Start game');await page.getByRole('button',{name:'Check',exact:true}).waitFor();await nav('Check');
  await page.getByRole('button',{name:'Next hand',exact:true}).waitFor();await nav('Next hand');await page.getByRole('button',{name:'Check',exact:true}).waitFor();await nav('Check');
  for(const req of requests.filter(r=>['/api/runs/personal-room/start','/api/rooms/personal-room/action'].includes(r.path))){assert.equal(req.headers['x-jev-key'],'jev-browser-test');assert.equal(req.headers['x-deepseek-key'],'ds-browser-test');assert.deepEqual(JSON.parse(req.headers['x-agent-keys']),{[customId]:'custom-browser-key'});}
  await page.getByRole('button',{name:'Next hand',exact:true}).waitFor();await page.waitForTimeout(100);const beforePoll=await page.evaluate(()=>window.__audio.tones);await page.waitForTimeout(1300);assert.equal(await page.evaluate(()=>window.__audio.tones),beforePoll);
  retired=true;await page.getByText('DeepSeek timed out 3 times · Left table',{exact:true}).last().waitFor();
  await page.screenshot({animations:'disabled',path:'test-results/personal-room.png',fullPage:true});
  await nav('Sign out');assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).filter(k=>k.startsWith('pokerbench-personal-keys:')).length),0);
  assert.equal(await page.locator('.rooms-page').count(),0);
  await page.getByLabel('Player ID',{exact:true}).fill('Bob');await page.getByLabel('Password',{exact:true}).fill('password123');await page.locator('.account-form').getByRole('button',{name:'Sign in',exact:true}).click();await page.getByText('Signed in as Bob',{exact:true}).waitFor();assert.equal(await page.getByLabel('Jev API key').inputValue(),'');
  await page.setViewportSize({width:390,height:844});
  for(const [name,slug] of [['Overview','overview'],['Hand replay','replay'],['Hand advisor','advisor'],['Play with models','rooms']]){
    await nav(name);await page.waitForTimeout(400);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${slug}: mobile overflow`);await page.screenshot({animations:'disabled',path:`test-results/${slug}-mobile.png`,fullPage:true});
  }
  await page.getByLabel('Language').selectOption('zh');await page.getByText('已登录：Bob',{exact:true}).waitFor();assert.ok(await page.getByLabel('Jev API 密钥').isVisible());await page.screenshot({animations:'disabled',path:'test-results/rooms-zh.png',fullPage:true});
  const forbiddenHeaders=requests.filter(r=>r.headers['x-jev-key']||r.headers['x-deepseek-key']||r.headers['x-agent-keys']);assert.ok(forbiddenHeaders.every(r=>r.method==='POST'&&['/api/rooms','/api/advisor/advise','/api/runs/personal-room/start','/api/rooms/personal-room/action'].includes(r.path)));
  assert.ok(requests.every(r=>!r.headers.authorization));assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
  console.log('Public browser smoke passed: read-only benchmarks/replay, invites, billing, session isolation, repeated key delivery, sounds, winners, timeout retirement, English/Chinese and mobile.');
}finally{await browser.close();}

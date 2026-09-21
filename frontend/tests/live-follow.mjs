import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const base=process.env.PB_BASE_URL||'http://127.0.0.1:5179';
const source=process.env.PB_DATA_URL||'http://127.0.0.1:8097';
const runs=await (await fetch(source+'/api/runs')).json();
const actual=runs.find(r=>r.hands_played>=1);assert.ok(actual,'Needs a saved hand');
const saved=await (await fetch(`${source}/api/runs/${actual.id}/hands/1`)).json();
const sample=saved.events.find(e=>e.type==='action');assert.ok(sample);
let phase=0;
const fixture={...actual,status:'running',hands_played:0,active_hand:1};
function events(number,count){return Array.from({length:count},(_,i)=>({...structuredClone(sample),index:i,label:'event '+i,snapshot:{...structuredClone(sample.snapshot),hand_number:number,complete:false}}));}
const browser=await chromium.launch({headless:true,channel:process.env.PB_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{window.__streams=[];window.EventSource=class{constructor(){window.__streams.push(this);}close(){window.__streams=window.__streams.filter(s=>s!==this);}};window.__emit=r=>window.__streams.forEach(s=>s.onmessage?.({data:JSON.stringify(r)}));});
await page.route('**/api/**',async route=>{
 const req=route.request(),path=new URL(req.url()).pathname;assert.equal(req.method(),'GET','No mutation or paid calls');
 let value;
 if(path==='/api/series') value=[];
 else if(path==='/api/runs') value=[fixture];
 else if(path===`/api/runs/${actual.id}/hands`) value=[1,2,3].map(number=>({number,complete:false}));
 else if(path.startsWith(`/api/runs/${actual.id}/hands/`)){const number=+path.split('/').at(-1);value={complete:false,events:events(number,phase===0?2:3)};}
 else value=await (await fetch(source+path)).json();
 await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
});
try {
 await page.goto(base);
 await page.getByRole('button',{name:'Watch hand',exact:true}).click();
 const live=page.getByRole('switch',{name:'Follow live'}),slider=page.getByRole('slider',{name:'Hand timeline'});
 await live.waitFor();assert.equal(await live.isChecked(),true);
 await page.waitForFunction(()=>document.querySelector('input[type=range]')?.value==='1');
 phase=1;
 await page.waitForFunction(()=>document.querySelector('input[type=range]')?.value==='2',{},{timeout:8000});
 fixture.active_hand=2;fixture.hands_played=1;await page.evaluate(r=>window.__emit(r),fixture);
 await page.waitForFunction(()=>document.querySelector('select[aria-label="Select hand"]')?.value==='2');
 assert.equal(await slider.inputValue(),'2');
 await page.getByRole('button',{name:'Previous step',exact:true}).click();
 assert.equal(await live.isChecked(),false);assert.equal(await slider.inputValue(),'1');
 fixture.active_hand=3;fixture.hands_played=2;await page.evaluate(r=>window.__emit(r),fixture);
 assert.equal(await page.getByRole('combobox',{name:'Select hand'}).inputValue(),'2');
 await live.check();await page.waitForFunction(()=>document.querySelector('select[aria-label="Select hand"]')?.value==='3');
 await page.getByRole('combobox',{name:'Language'}).selectOption('zh');
 assert.equal(await page.getByRole('switch',{name:'实时跟随'}).isChecked(),true);
 await fs.mkdir('test-results',{recursive:true});await page.screenshot({path:'test-results/live-follow-zh.png',fullPage:true});
 await page.reload();await page.waitForFunction(()=>document.querySelector('select[aria-label="选择手牌"]')?.value==='3');
 assert.equal(await page.getByRole('switch',{name:'实时跟随'}).isChecked(),true);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,newActions:true,nextHand:true,manualExit:true,languagePreserved:true,liveLinkReload:true,paidRequests:0}));
}finally{await browser.close();}

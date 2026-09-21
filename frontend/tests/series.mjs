import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const base=process.env.PB_BASE_URL||'http://127.0.0.1:5179';
const source=process.env.PB_DATA_URL||'http://127.0.0.1:8097';
const saved=await (await fetch(source+'/api/runs')).json();
const sng=saved.find(r=>r.mode==='sng'),cash=saved.find(r=>r.mode==='cash');
assert.ok(sng&&cash);
const entries=sng.entries;
const fixture={id:'test-series',status:'running',message:'',target:50,completed:2,current_run_id:sng.id,current_number:3,run_ids:[sng.id],cost_cny_estimate:1,
 standings:entries.map((e,i)=>({id:e.id,name:e.name,color:e.color,rank:i+1,completed:2,mean_place:i+1,wins:i===0?2:0}))};
const next={...structuredClone(sng),id:'next-tournament',created:Date.now()/1000,name:'Series tournament 4',series_id:fixture.id,series_number:4,status:'running'};
const browser=await chromium.launch({headless:true,channel:process.env.PB_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],mutations=[];
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{window.EventSource=class{close(){}};});
await page.route('**/api/**',async route=>{
 const request=route.request(),path=new URL(request.url()).pathname;
 let value;
 if(request.method()!=='GET') {
   assert.match(path,/^\/api\/series\/test-series\/(pause|start)$/);mutations.push(path);
   fixture.status=path.endsWith('/pause')?'paused':'running';value={ok:true};
 } else if(path==='/api/series') value=[fixture];
 else if(path==='/api/runs') value=[{...cash,settle_every:500,buy_in:20000},sng];
 else if(path==='/api/runs/next-tournament') value=next;
 else value=await (await fetch(source+path)).json();
 await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
});
try {
 await page.goto(base);
 const panel=page.getByRole('region',{name:'SNG series standings',exact:true});
 await panel.waitFor();assert.match(await panel.innerText(),/Completed 2 \/ 50 tournaments/);
 assert.match(await page.locator('.award').nth(1).innerText(),/Total points/);
 assert.match(await page.locator('#results-cash').innerText(),/Cash out every 500 hands/);
 await panel.getByRole('button',{name:'Pause series',exact:true}).click();
 await panel.getByRole('button',{name:'Resume series',exact:true}).waitFor();
 await panel.getByRole('button',{name:'Resume series',exact:true}).click();
 fixture.current_run_id=next.id;fixture.current_number=4;fixture.completed=3;
 await page.locator('#results-sng strong').filter({hasText:'Series tournament 4'}).waitFor({timeout:12000});
 assert.match(await panel.innerText(),/Completed 3 \/ 50 tournaments/);
 await fs.mkdir('test-results',{recursive:true});
 await page.screenshot({path:'test-results/series-en.png',fullPage:true});
 await page.getByRole('combobox',{name:'Language'}).selectOption('zh');
 await page.getByRole('heading',{name:'SNG 系列赛总榜',exact:true}).waitFor();
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.screenshot({path:'test-results/series-mobile-zh.png',fullPage:true});
 assert.equal(mutations.length,2);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,aggregatePodium:true,autoNextTournament:true,seriesControls:true,bilingual:true,mobile:true,paidRequests:0}));
} finally {await browser.close();}

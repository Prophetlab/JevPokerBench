let language='en',snapshot=null,pending=false;
const zh={private:'仅管理员可见',eyebrow:'了解访问与玩家活动',title:'网站统计',subtitle:'独立采集访问与组局数据，不干扰正在进行的比赛。',loading:'正在加载统计…',views:'今日页面加载',loadsNote:'成功打开页面及刷新次数',visitors:'今日估算访客',visitorsNote:'按每日 IP＋浏览器标识去重',registrations:'今日注册',rooms:'今日创建牌桌',trend:'最近 14 天',pv:'页面加载',uv:'估算访客',daily:'每日活动',timezone:'北京时间 · 最近 30 天',date:'日期',signup:'注册人数',tables:'创建牌桌',players:'组局人数',method:'统计口径与隐私',method1:'访问数据来自保留的服务器日志，只计成功加载网页；排除接口轮询、实时流、静态资源及已识别机器人。页面内切换栏目而未重新加载，不增加浏览量。',method2:'访客数是估算值，不等于实际人数；共享网络、浏览器变化都会影响去重。统计库仅保存按天加盐的标识，不保存原始 IP、浏览器请求头、密码或 API Key。',method3:'注册数按当前仍存在账号的创建日期统计。牌桌和组局人数按有账号归属的牌桌创建日期统计，包含之后删除的牌桌，不含未认领旧牌桌。各日访客数、组局人数相加，不等于跨日去重人数。',method4:'统计每分钟独立更新；比赛数据仅做有短超时限制的只读查询。数据源暂不可用时，保留上次成功结果并提示。',footer:'私有运营统计'};
const en={};document.querySelectorAll('[data-i]').forEach(el=>en[el.dataset.i]=el.textContent);
const text=(en,zh)=>language==='zh'?zh:en;
const number=v=>v===null||v===undefined?'—':Number(v).toLocaleString(language==='zh'?'zh-CN':'en-US');
function node(tag,attrs={}){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,String(v));return e;}
function render(){
 if(!snapshot)return;const d=snapshot,today=d.days?.[0];document.querySelector('#loading').hidden=!!d.updated;
 for(const k of ['views','visitors','registrations','rooms'])document.getElementById(k).textContent=number(today?.[k]);
 document.getElementById('accountsTotal').textContent=text('Total accounts: ','累计账号：')+number(d.account_total);
 document.getElementById('roomsTotal').textContent=text('Total tables: ','累计牌桌：')+number(d.room_total);
 document.getElementById('updated').textContent=d.updated?text('Updated ','更新于 ')+new Date(d.updated*1000).toLocaleString(language==='zh'?'zh-CN':'en-GB',{timeZone:'Asia/Shanghai',hour12:false}):'';
 const warning=document.getElementById('error');warning.hidden=!(d.traffic_error||d.accounts_error||d.catching_up);
 warning.textContent=[d.traffic_error&&text(d.traffic_error,'访问日志暂不可读，正在展示已采集数据。'),d.accounts_error&&text(d.accounts_error,'账号统计暂不可用，保留最近一次成功结果。'),d.catching_up&&text('Importing retained logs; visit counts are still updating.','正在补录保留日志，访问数据尚未完整。')].filter(Boolean).join(' ');
 document.getElementById('coverage').textContent=text('Retained visit data starts: ','已采集访问数据始于：')+(d.first_visit_day||'—');
 const tbody=document.getElementById('rows');tbody.replaceChildren();
 for(const day of d.days||[]){const tr=document.createElement('tr');for(const key of ['day','views','visitors','registrations','rooms','players']){const td=document.createElement('td');td.textContent=key==='day'?day[key]:number(day[key]);tr.append(td);}tbody.append(tr);}
 const chart=document.getElementById('chart');chart.replaceChildren();const days=(d.days||[]).slice(0,14).reverse(),max=Math.max(1,...days.map(r=>r.views));
 for(let i=0;i<4;i++){const y=15+i*55;chart.append(node('line',{x1:38,y1:y,x2:998,y2:y}));const label=node('text',{x:0,y:y+4});label.textContent=number(Math.round(max*(3-i)/3));chart.append(label);}
 days.forEach((r,i)=>{const x=45+i*68;for(const [key,dx,cls]of [['views',0,'bar-views'],['visitors',23,'bar-visitors']]){const height=r[key]/max*165,rect=node('rect',{x:x+dx,y:180-height,width:19,height,rx:2,class:cls});const title=node('title');title.textContent=r.day+' · '+(key==='views'?text('Page loads','页面加载'):text('Estimated visitors','估算访客'))+': '+number(r[key]);rect.append(title);chart.append(rect);}const label=node('text',{x:x+2,y:208});label.textContent=r.day.slice(5);chart.append(label);});
}
document.getElementById('language').addEventListener('click',()=>{language=language==='en'?'zh':'en';document.documentElement.lang=language==='zh'?'zh-CN':'en';document.getElementById('language').textContent=language==='en'?'中文':'English';document.querySelectorAll('[data-i]').forEach(el=>el.textContent=(language==='zh'?zh:en)[el.dataset.i]);render();});
async function load(){if(pending)return;pending=true;try{const response=await fetch('api/summary',{cache:'no-store',credentials:'same-origin'});if(!response.ok)throw Error('HTTP '+response.status);snapshot=await response.json();render();}catch{const e=document.getElementById('error');e.hidden=false;e.textContent=text('Statistics unavailable. Check administrator access; retrying automatically.','暂时无法获取统计，请检查管理员登录；页面会自动重试。');}finally{pending=false;}}
void load();setInterval(load,30000);

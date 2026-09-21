import asyncio,json
from pokerbench.config import Settings,Entry
from pokerbench.provider import Provider
from pokerbench.store import Store
from pokerbench.engine import Hand
async def main():
 s=Settings();st=Store(s.pokerbench_data_dir+'/pokerbench.sqlite');p=Provider(s,st)
 h=Hand([{'id':f'p{i}','name':f'player{i}','stack':20000} for i in range(10)],0,(50,100,0),41)
 e=Entry(id='jev-direct',name='Jev',provider='jev',model='jev-1.13.0',base_url='https://api.typesafe.ai',key_env='JEV_API_KEY',proxy=False,input_cny_per_million=.3024,output_cny_per_million=0)
 try:
  result=await p.call(e,h.request(e.model),decision_id='jev-compatibility-v1',run_id='compatibility',run_budget=2)
  report={'ok':True,'model':result['model'],'answers':result['answers'],'usage':result['usage'],'latency':result['latency']}
 except Exception as exc:report={'ok':False,'error':str(exc)}
 open('data/jev-smoke.json','w').write(json.dumps(report,indent=2));print(json.dumps(report))
asyncio.run(main())

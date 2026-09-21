"""Run with all matches paused; rebuild post-hand EV analysis without API calls."""
from copy import deepcopy
from pokerbench.config import Settings
from pokerbench.ev import attach_ev
from pokerbench.store import Store
s=Settings();store=Store(s.pokerbench_data_dir+'/pokerbench.sqlite')
for run in store.runs():
    if run['status']=='running':raise RuntimeError('Pause matches before backfill')
    history=deepcopy(run['history']);run['history']=history[:1];run['ev_summary']={'covered_hands':0}
    for point in history[1:]:
        run['history'].append(point)
        record=store.hand(run['id'],point['hand'])
        record['analysis']=attach_ev(run,record)
        store.save_run(run,record)
    store.save_run(run)
    print(run['id'],run['ev_summary'])

"""Repair recorded public-card visibility without changing actions or results."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from pokerbench.engine import Hand


def repair(record):
    restored=Hand.restore(record).record()
    result=deepcopy(record)
    if len(result['events'])!=len(restored['events']):
        raise ValueError('Replay event count changed; refusing visibility-only repair')
    for old,new in zip(result['events'],restored['events']):
        if [s['id'] for s in old['snapshot']['seats']]!=[s['id'] for s in new['snapshot']['seats']]:
            raise ValueError('Replay seats changed')
        for old_seat,new_seat in zip(old['snapshot']['seats'],new['snapshot']['seats']):
            old_seat['public_cards']=new_seat['public_cards']
        if old!=new:
            raise ValueError('Replay changed beyond public cards; refusing repair')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    db=sqlite3.connect(args.database)
    changes=[]
    for run_id,number,body in db.execute('SELECT run_id,number,body FROM hands'):
        record=json.loads(body)
        if record['spec'].get('manual'):continue
        fixed=repair(record)
        if fixed!=record:changes.append((json.dumps(fixed),run_id,number))
    if args.apply:
        with db:db.executemany('UPDATE hands SET body=? WHERE run_id=? AND number=?',changes)
    print(json.dumps({'changed_hands':len(changes),'applied':args.apply}))


if __name__=='__main__':main()

"""Post-hand all-in runout adjustment, never part of a model's observation."""
from itertools import combinations
import math
import random

from treys import Card, Evaluator


def all_in_adjustment(record: dict, samples: int = 5000) -> dict:
    if not record['complete']:
        raise ValueError('只能在结算后计算全下权益')
    ids=[s['id'] for s in record['spec']['seats']]
    empty={'covered':False,'adjustments':dict.fromkeys(ids,0),'pots':[]}
    lock=None
    for event in record['events']:
        s=event['snapshot']
        live=[p for p in s['seats'] if not p['folded'] and p['position']!='OUT']
        # All decisions have ended, so no future fold can change pot eligibility.
        if not s['complete'] and s['actor'] is None and len(s['board'])<5 and len(live)>1 and sum(p['stack']>0 for p in live)<=1:
            lock=s
            break
    if lock is None:
        return empty
    live=[p for p in lock['seats'] if not p['folded'] and p['position']!='OUT']
    holes={p['id']:[Card.new(c) for c in p['cards']] for p in live}
    board=[Card.new(c) for c in lock['board']]
    known=set(board+[c for cards in holes.values() for c in cards])
    deck=[Card.new(r+s) for r in '23456789TJQKA' for s in 'cdhs' if Card.new(r+s) not in known]
    missing=5-len(board)
    exact=math.comb(len(deck),missing)<=20000
    count=math.comb(len(deck),missing) if exact else samples
    rng=random.Random(8128+record['spec']['number'])
    runouts=combinations(deck,missing) if exact else (rng.sample(deck,missing) for _ in range(samples))
    evaluator=Evaluator()
    pots=[p for p in lock['pots'] if p['amount']>0]
    totals=[dict.fromkeys(p['eligible'],0.0) for p in pots]
    for runout in runouts:
        scores={pid:evaluator.evaluate(board+list(runout),cards) for pid,cards in holes.items()}
        for pot,total in zip(pots,totals):
            eligible={pid:scores[pid] for pid in pot['eligible'] if pid in scores}
            if not eligible:
                raise ValueError('全下底池参与资格无效')
            best=min(eligible.values())
            winners=[pid for pid,score in eligible.items() if score==best]
            for pid in winners:
                total[pid]+=pot['amount']/len(winners)
    expected=dict.fromkeys(ids,0.0)
    details=[]
    for pot,total in zip(pots,totals):
        payouts={pid:v/count for pid,v in total.items()}
        for pid,value in payouts.items():
            expected[pid]+=value
        details.append({**pot,'expected_payout':payouts})
    final={s['id']:s['stack'] for s in record['events'][-1]['snapshot']['seats']}
    adjustments={s['id']:expected[s['id']]-(final[s['id']]-s['stack']) for s in lock['seats']}
    if abs(sum(adjustments.values()))>0.01:
        raise ValueError('全下权益调整未守恒')
    return {'covered':True,'board_at_lock':lock['board'],'method':'exact' if exact else 'Monte Carlo',
            'samples':count,'adjustments':adjustments,'pots':details,
            'scope':'整桌下注结束后的全下 runout；非全下手保持实际盈亏；不是策略 EV'}


def attach_ev(run: dict, record: dict) -> dict:
    """Attach analysis to the already committed hand's final history point."""
    analysis=all_in_adjustment(record)
    previous=run['history'][-2] if len(run['history'])>1 else {'values':{},'ev_values':{}}
    current=run['history'][-1]
    current['ev_values']={pid:value+previous.get('ev_values',previous['values']).get(pid,0)-previous['values'].get(pid,0)+analysis['adjustments'].get(pid,0)
                          for pid,value in current['values'].items()}
    current['all_in_ev_covered']=analysis['covered']
    run.setdefault('ev_summary',{'covered_hands':0})['covered_hands']+=int(analysis['covered'])
    return analysis

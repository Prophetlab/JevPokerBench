"""Timing summaries and replay highlights without altering model observations."""
import math
import statistics
from pokerkit import ChipsPushing


def percentile(values, p):
    index=(len(values)-1)*p
    low=int(index);high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(index-low)


def timing_summary(run):
    rows=[]
    for entry in run['config']['entries']:
        samples=run.get('timing_samples',{}).get(entry['id'],[])
        good=[];retries=invalid=0
        for sample in samples:
            if sample.get('retries',0):retries+=1;continue
            value=sample.get('seconds')
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<=0:
                invalid+=1;continue
            good.append(value)
        good.sort();kept=good
        bounds=None
        if len(good)>=8:
            q1,q3=percentile(good,.25),percentile(good,.75)
            bounds=[max(0,q1-1.5*(q3-q1)),q3+1.5*(q3-q1)]
            kept=[v for v in good if bounds[0]<=v<=bounds[1]]
        rows.append({'id':entry['id'],'name':entry['name'],'decisions':len(samples),'included':len(kept),
                     'retry_excluded':retries,'invalid_excluded':invalid,'outlier_excluded':len(good)-len(kept),
                     'mean_seconds':statistics.mean(kept) if kept else None,
                     'median_seconds':statistics.median(kept) if kept else None,
                     'p90_seconds':percentile(kept,.9) if kept else None,'bounds_seconds':bounds})
    return {'method':'Successful executed decisions; exclude retries and invalid times; 1.5 IQR fences at n >= 8. Includes transport and upstream queuing, excludes the benchmark concurrency queue.',
            'models':rows}


def record_timing(run, player, decision, attempt_count=0):
    if 'latency' not in decision:return
    run.setdefault('timing_samples',{}).setdefault(player,[]).append({
        'seconds':decision['latency'],'retries':max(decision.get('retry_count',0),attempt_count-1)})


def selected_highlights(run):
    candidates=[h for h in run.get('highlights',[]) if h.get('eliminations') or h.get('pot_bb',0)>=100]
    best=sorted(candidates,key=lambda h:(bool(h.get('eliminations')),h.get('pot_bb',0),h['hand']),reverse=True)[:5]
    return sorted(best,key=lambda h:h['hand'],reverse=True)


def hand_highlight(hand, eliminations):
    snapshots=[e['snapshot'] for e in hand.events]
    # Net contributions exclude uncalled bets returned at settlement.
    pot=sum(sum(op.amounts) for op in hand.state.operations if isinstance(op,ChipsPushing))
    largest_win=max(max(0,s['payoff'] or 0) for s in snapshots[-1]['seats'])
    reasons=[]
    if eliminations:reasons.append('elimination')
    if any(a['all_in'] for a in hand.actions) or any(s['all_in'] for snap in snapshots for s in snap['seats']):reasons.append('all_in')
    if max(len(s['pots']) for s in snapshots)>1:reasons.append('side_pots')
    if pot>=40*hand.bb:reasons.append('big_pot')
    if not reasons:return None
    return {'hand':hand.number,'reasons':reasons,'pot':pot,'pot_bb':round(pot/hand.bb,2),
            'eliminations':eliminations,'largest_win':largest_win}

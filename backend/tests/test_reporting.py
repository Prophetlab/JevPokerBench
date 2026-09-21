from copy import deepcopy
import pytest
from pokerbench.reporting import timing_summary,hand_highlight,record_timing,selected_highlights
from pokerbench.provider import condition_legal_actions,validate_answer,ProviderError
from pokerbench.engine import Hand
from pokerbench.store import Store


def run_with(values):
    return {'config':{'entries':[{'id':'m','name':'Model'}]},'timing_samples':{'m':values}}


def test_highlights_select_five_largest_and_prioritize_eliminations_without_mutating_history():
    run={'highlights':[{'hand':i,'pot_bb':i*100,'eliminations':[]} for i in range(1,10)]}
    run['highlights'].extend([{'hand':10,'pot_bb':3.5,'eliminations':[]},{'hand':11,'pot_bb':2,'eliminations':['a']}])
    before=deepcopy(run)
    assert [h['hand'] for h in selected_highlights(run)]==[11,9,8,7,6]
    assert run==before


def test_small_uncalled_all_ins_do_not_fill_the_highlight_list():
    assert selected_highlights({'highlights':[{'hand':1,'pot_bb':4.5,'reasons':['all_in'],'eliminations':[]}]})==[]


def test_outliers_retries_and_missing_values_are_counted_separately():
    samples=[{'seconds':v,'retries':0} for v in [1,1,1,1,1,1,1,50]]
    samples.extend([{'seconds':80,'retries':2},{'seconds':float('nan')},{'seconds':-1}])
    r=timing_summary(run_with(samples))['models'][0]
    assert (r['decisions'],r['included'],r['retry_excluded'],r['outlier_excluded'],r['invalid_excluded'])==(11,7,1,1,2)
    assert r['mean_seconds']==r['median_seconds']==r['p90_seconds']==1


def test_small_samples_not_trimmed_and_model_metrics_are_separate():
    run=run_with([{'seconds':1},{'seconds':100}]);run['config']['entries'].append({'id':'other','name':'Other'})
    rows=timing_summary(run)['models']
    assert rows[0]['mean_seconds']==50.5 and rows[0]['outlier_excluded']==0
    assert rows[1]['included']==0 and rows[1]['mean_seconds'] is None
    before=deepcopy(run);timing_summary(run);assert run==before
    record_timing(run,'other',{'latency':.25,'retry_count':0})
    assert timing_summary(run)['models'][1]['mean_seconds']==.25


def test_resuming_after_failed_batch_does_not_count_as_first_attempt(tmp_path):
    store=Store(str(tmp_path/'calls.sqlite'))
    for did in ('r:1:0','r:1:0','r:1:0','r:1:0','r:1:1'):
        cid=store.reserve('r',0,100,100)
        store.finish_call(cid,0,{'decision_id':did})
    store.db.close()
    store=Store(str(tmp_path/'calls.sqlite'))
    run=run_with([])
    record_timing(run,'m',{'latency':.2,'retry_count':0},store.decision_attempt_count('r','r:1:0'))
    record_timing(run,'m',{'latency':.3,'retry_count':0},store.decision_attempt_count('r','r:1:1'))
    row=timing_summary(run)['models'][0]
    assert row['retry_excluded']==1 and row['included']==1 and row['mean_seconds']==.3
    store.db.close()


def test_highlight_pot_does_not_include_uncalled_bet():
    h=Hand([{'id':'a','stack':1000},{'id':'b','stack':100}],0,(5,10,0),7)
    h.apply('raise_to_1000');h.apply('call')
    result=hand_highlight(h,[])
    assert result['pot']==200 and result['pot_bb']==20
    assert 'all_in' in result['reasons'] and 'big_pot' not in result['reasons']


def test_blind_only_all_in_elimination_is_saved_without_model_actions():
    h=Hand([{'id':'a','stack':1},{'id':'b','stack':1}],0,(5,10,0),7)
    assert not h.state.status and not h.actions
    eliminated=[seat['id'] for seat in h.events[-1]['snapshot']['seats'] if not seat['stack']]
    result=hand_highlight(h,eliminated)
    assert result['pot']==2 and result['eliminations']==eliminated
    assert 'all_in' in result['reasons'] and 'elimination' in result['reasons']


def test_abstention_projection_is_explicit_and_preserves_original_distribution():
    request={'questions':{'action':{'type':'choice','criteria':{'fold':'Fold','call':'Call'}}}}
    response={'answers':{'action':{'type':'choice','choice':'__insufficient_evidence__','is_abstention':True,'confidence':.2,
                                  'probabilities':{'fold':.1,'call':.2,'__insufficient_evidence__':.7}}}}
    with pytest.raises(ProviderError):validate_answer(deepcopy(response),request)
    projected=condition_legal_actions(response,request)
    validate_answer(projected,request)
    a=projected['answers']['action']
    assert a['choice']=='call' and a['probabilities']['call']==pytest.approx(2/3)
    assert a['legal_action_projection']['original_probabilities']['__insufficient_evidence__']==.7
    assert a['legal_action_projection']['reported_abstention'] and a['legal_action_projection']['choice_changed']


@pytest.mark.parametrize('probs',[
    {'fold':.1,'__insufficient_evidence__':.9},
    {'fold':.1,'call':.2,'extra':.1,'__insufficient_evidence__':.6},
    {'fold':0,'call':0,'__insufficient_evidence__':1},
    {'fold':-.1,'call':.2,'__insufficient_evidence__':.9},
])
def test_projection_does_not_fill_missing_probabilities_or_fabricate_zero_mass(probs):
    with pytest.raises(ProviderError):condition_legal_actions({'answers':{'action':{'probabilities':probs}}},{'questions':{'action':{'criteria':{'fold':'','call':''}}}})

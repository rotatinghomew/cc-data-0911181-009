from .coverage import default_budgets, propose
from .domain import playback_summary
from .estimation import ESTIMANDS, ESTIMAND_VERSION, USAGE_LIMITS, build_estimates, comparison_view
from .storage import integer, single


def playback(store,query,journal=None):
    course=single(query,'course')
    return {'streams':[playback_summary(store,row) for row in store.table('players')
                       if course is None or row['course']==course],
            'unit':'device_stream_observations',
            'gap_note':'缺心跳区间计入可行秒数但未计入确认秒数，两者之差为未核实部分，不补满也不计零。'}


def probes(store,query,journal=None):
    rows=store.table('probes')
    result={'sampled':len(rows),'delivered':sum(row['delivered'] for row in rows),
            'responses':sum(row['responded'] is True for row in rows),
            'design':store.table('sampling'),'events':rows}
    result['executed']=result['delivered']
    result['not_executed']=result['sampled']-result['delivered']
    result['pending']=sum(1 for row in rows if row['delivered'] and row.get('responded') is None)
    if journal is not None:
        deliveries=journal.all('probe_delivery')
        receipts=journal.all('probe_receipt')
        result['workflow']={'planned':sum(len(plan['streams']) for plan in journal.all('probe_plan')),
                            'delivered':sum(1 for row in deliveries if row['status']=='delivered'),
                            'skipped':sum(1 for row in deliveries if row['status']=='skipped'),
                            'receipts':len(receipts),
                            'pending':sum(1 for row in deliveries if row['status']=='delivered')-len(receipts)}
    return result


def engagement(store,query,journal=None):
    estimates=build_estimates(store,journal)
    return {'estimand_version':ESTIMAND_VERSION,'estimands':ESTIMANDS,'usage_limits':USAGE_LIMITS,
            'unit':'person_seconds_and_design_based_shares',
            'learners':estimates['learners'],'courses':estimates['courses']}


def comparison(store,query,journal=None):
    return comparison_view(store,build_estimates(store,journal))


def coverage(store,query,journal=None):
    estimates=build_estimates(store,journal)
    budgets=default_budgets(store,journal)
    individual=integer(query,'individual',budgets['individual'],0,500)
    group=integer(query,'group',budgets['group'],0,500)
    return propose(store,estimates,individual,group,journal)


ROUTES={'/api/playback':playback,'/api/probes':probes,'/api/engagement':engagement,
        '/api/comparison':comparison,'/api/coverage':coverage}

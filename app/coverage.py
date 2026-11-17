from hashlib import sha256
import json

from .estimation import MIN_VIABLE_OPPORTUNITIES, ht_block, ht_projection


def remaining_budgets(store, journal):
    policy = store.find('contact_policy', policy='current')
    deliveries = journal.all('probe_delivery') if journal is not None else []
    personal = {}
    for player in store.table('players'):
        learner = player.get('learner')
        if learner:
            personal.setdefault(learner, policy['maximum_per_person'])
    for delivery in deliveries:
        if delivery['status'] == 'delivered' and delivery.get('kind', 'individual') == 'individual':
            learner = delivery.get('learner')
            if learner in personal:
                personal[learner] = max(0, personal[learner] - 1)
    group_cap = policy.get('maximum_per_group_session')
    group = {}
    for session in store.table('group_sessions'):
        used = sum(1 for delivery in deliveries
                   if delivery['status'] == 'delivered' and delivery.get('kind') == 'group'
                   and delivery['stream'] == session['stream'])
        group[session['stream']] = max(0, group_cap - used) if group_cap is not None else None
    return personal, group


def default_budgets(store, journal):
    personal, group = remaining_budgets(store, journal)
    individual = min(10, sum(personal.values()))
    group_budget = sum(remaining for remaining in group.values() if remaining is not None)
    return dict(individual=individual, group=group_budget)


def propose(store, estimates, individual_budget, group_budget, journal=None):
    personal, group_remaining = remaining_budgets(store, journal)
    players = {row['stream']: row for row in store.table('players')}
    cells = []
    for (course, kind, stratum), records in sorted(estimates['cells'].items()):
        if kind == 'individual':
            eligible = []
            used = {}
            for stream, player in sorted(players.items()):
                learner = player.get('learner')
                if player['course'] != course or not learner or player['network'] != stratum:
                    continue
                if used.get(learner, 0) < personal.get(learner, 0):
                    eligible.append(stream)
                    used[learner] = used.get(learner, 0) + 1
        else:
            eligible = [stream for stream, player in sorted(players.items())
                        if player['course'] == course and player['network'] == stratum
                        and group_remaining.get(stream)]
        cells.append(dict(key=dict(course=course, kind=kind, stratum=stratum), records=records,
                          block=ht_block(records), eligible=eligible, proposed=0, kind=kind))
    budgets = {'individual': individual_budget, 'group': group_budget}
    while True:
        candidates = [cell for cell in cells
                      if cell['proposed'] < _capacity(cell, group_remaining)
                      and budgets[cell['kind']] > 0]
        if not candidates:
            break
        best = max(candidates, key=_priority)
        best['proposed'] += 1
        budgets[best['kind']] -= 1
    proposal = {}
    out_cells = []
    for cell in cells:
        key = cell['key']
        label = '%(course)s/%(kind)s/%(stratum)s' % key
        if cell['proposed']:
            proposal[label] = cell['proposed']
        out_cells.append(dict(course=key['course'], kind=key['kind'], stratum=key['stratum'],
                              executed=cell['block']['executed'], resolved=cell['block']['resolved'],
                              share=cell['block']['share'], ci=cell['block']['ci'],
                              insufficient_evidence=cell['block']['insufficient_evidence'],
                              eligible_streams=len(cell['eligible']),
                              proposed_opportunities=cell['proposed'],
                              suggested_streams=_suggested(cell, group_remaining),
                              projected_ci_half=ht_projection(cell['records'], cell['proposed'])))
    fingerprint_payload = dict(fingerprint=store.fingerprint(),
                               budgets=dict(individual=individual_budget, group=group_budget),
                               proposal=proposal)
    proposal_id = sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()[:16]
    policy = store.find('contact_policy', policy='current')
    return dict(budgets=dict(individual=dict(requested=individual_budget,
                                             per_person_cap=policy['maximum_per_person'],
                                             remaining_disturbances=sum(personal.values())),
                             group=dict(requested=group_budget,
                                        per_session_cap=policy.get('maximum_per_group_session'),
                                        remaining_opportunities=sum(v for v in group_remaining.values()
                                                                    if v is not None))),
                cells=out_cells, proposal_id=proposal_id,
                reconciliation=reconciliation(store, journal),
                notes=['估计不确定性驱动以上分配：证据不足或区间最宽的单元优先获得下一轮机会。',
                       '集体与个人机会分别记账，互不占用对方额度。',
                       '投放回执与计划可逐条核对；估计只消费真正执行的机会，跳过与未送达列在回执中。'])


def _capacity(cell, group_remaining):
    if cell['kind'] == 'group':
        return sum(group_remaining.get(stream) or 0 for stream in cell['eligible'])
    return len(cell['eligible'])


def _suggested(cell, group_remaining):
    suggested = {}
    if cell['kind'] == 'group':
        left = cell['proposed']
        for stream in cell['eligible']:
            take = min(left, group_remaining.get(stream) or 0)
            if take:
                suggested[stream] = take
                left -= take
        return suggested
    for stream in cell['eligible'][:cell['proposed']]:
        suggested[stream] = 1
    return suggested


def _priority(cell):
    if cell['block']['resolved'] + cell['proposed'] < MIN_VIABLE_OPPORTUNITIES:
        return (1, 0)
    half = ht_projection(cell['records'], cell['proposed'])
    return (0, half if half is not None else 0)


def reconciliation(store, journal):
    plans = []
    if journal is not None:
        deliveries = journal.all('probe_delivery')
        receipts = journal.all('probe_receipt')
        for plan in journal.all('probe_plan'):
            own = [row for row in deliveries if row['plan'] == plan['plan']]
            keys = {row['delivery'] for row in own}
            own_receipts = [row for row in receipts if row['delivery'] in keys]
            delivered = [row for row in own if row['status'] == 'delivered']
            plans.append(dict(plan=plan['plan'], kind=plan.get('kind', 'individual'),
                              probability=plan['probability'], proposal=plan.get('proposal'),
                              planned_streams=len(plan['streams']),
                              executed=len(delivered),
                              skipped=sum(1 for row in own if row['status'] == 'skipped'),
                              skip_reasons=sorted({row['reason'] for row in own if row.get('reason')}),
                              receipts=len(own_receipts),
                              responded=sum(1 for row in own_receipts if row['responded']),
                              pending=len(delivered) - len(own_receipts),
                              checks=dict(realized_within_plan=all(row['stream'] in plan['streams']
                                                                   for row in own),
                                          receipts_only_for_delivered=all(
                                              row['delivery'] in {d['delivery'] for d in delivered}
                                              for row in own_receipts))))
    sampled = store.table('probes')
    sampled_summary = dict(sampled=len(sampled),
                           executed=sum(1 for row in sampled if row['delivered']),
                           not_executed=sum(1 for row in sampled if not row['delivered']),
                           responded=sum(1 for row in sampled if row['responded'] is True),
                           pending=sum(1 for row in sampled
                                       if row['delivered'] and row.get('responded') is None))
    return dict(plans=plans, sampled_probes=sampled_summary,
                basis='估计只使用真正执行的抽样机会；未送达与跳过的机会列在此处，不进入回应分析。')

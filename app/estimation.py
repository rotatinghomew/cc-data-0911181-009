from math import sqrt

from .domain import merge_length, stream_evidence

ESTIMAND_VERSION = 'v1'
MIN_VIABLE_OPPORTUNITIES = 4
Z = 1.96

ESTIMANDS = {
    'device_playback': '设备播放：单条流的确认/可行播放秒数；缺心跳区间保留为两者之差，不补满也不计零。',
    'person_exposure': '个人可能接触：同一老人全部设备流的墙钟并集；重叠设备与重复播放不重复计时。',
    'content_distinct': '内容去重：同一老人在同一课程上各设备覆盖的内容位置并集，重复播放不重复累计。',
    'response_supported': '回应支持的投入：仅使用真正执行的提问机会，按抽样机会倒数加权；未回应不视为没在听，只保留为不确定。',
    'unidentified_group': '集体播放未签到部分：人数只有签到范围，以区间保留，不计入任何个人。',
}

USAGE_LIMITS = [
    '估计用于课程与时段层面的证据比较，不能用于评价个人是否认真学习。',
    '未回应不等于没在听：回应支持的投入是下限，未回应部分保留为不确定。',
    '集体播放的回应无法对应到个人，到场人数只有签到范围。',
    '缺心跳区间既不全额计入也不计零，以确认与可行区间保留。',
    '调整抽样机会或改用集体播放只改变证据强度，不改变口径；口径差异不会伪装成投入变化。',
]


def opportunities(store, journal):
    groups = {row['stream'] for row in store.table('group_sessions')}
    players = {row['stream']: row for row in store.table('players')}
    records = []
    for probe in store.table('probes'):
        player = players[probe['stream']]
        records.append(dict(source='sampled', id=probe['probe'], stream=probe['stream'],
                            course=player['course'],
                            kind='group' if probe['stream'] in groups else 'individual',
                            stratum=player['network'], pi=float(probe['inclusion_probability']),
                            executed=bool(probe['delivered']), responded=probe.get('responded'),
                            reason=None if probe['delivered'] else 'not_delivered'))
    if journal is not None:
        receipts = {row['delivery']: row for row in journal.all('probe_receipt')}
        for delivery in journal.all('probe_delivery'):
            player = players[delivery['stream']]
            receipt = receipts.get(delivery['delivery'])
            records.append(dict(source='workflow', id=delivery['delivery'], stream=delivery['stream'],
                                course=player['course'], kind=delivery.get('kind', 'individual'),
                                stratum=player['network'], pi=float(delivery['planned_probability']),
                                executed=delivery['status'] == 'delivered',
                                responded=receipt['responded'] if receipt else None,
                                reason=delivery.get('reason')))
    return records


def ht_block(records):
    executed = [row for row in records if row['executed']]
    resolved = [row for row in executed if row['responded'] is not None]
    responded = sum(1 for row in resolved if row['responded'])
    block = dict(executed=len(executed), resolved=len(resolved), responded=responded,
                 pending=len(executed) - len(resolved),
                 not_executed=sum(1 for row in records if not row['executed']))
    n = len(resolved)
    if n == 0:
        block.update(share=None, ci=None, ci_half=None, insufficient_evidence=True)
        return block
    total = sum(1.0 / row['pi'] for row in resolved)
    attested = sum(1.0 / row['pi'] for row in resolved if row['responded'])
    share = attested / total
    design_ss = sum((1.0 - row['pi']) / row['pi'] ** 2 *
                    ((1.0 if row['responded'] else 0.0) - share) ** 2 for row in resolved)
    design_half = Z * sqrt(design_ss) / total
    smoothed = (responded + 1.0) / (n + 2.0)
    floor_half = Z * sqrt(smoothed * (1.0 - smoothed) / n)
    half = max(design_half, floor_half)
    block.update(share=share, ci=[max(0.0, share - half), min(1.0, share + half)], ci_half=half,
                 insufficient_evidence=n < MIN_VIABLE_OPPORTUNITIES)
    return block


def ht_projection(records, added):
    resolved = [row for row in records if row['executed'] and row['responded'] is not None]
    n = len(resolved)
    if n + added == 0:
        return None
    responded = sum(1 for row in resolved if row['responded'])
    rate = responded / n if n else 0.5
    total = sum(1.0 / row['pi'] for row in resolved) + added
    design_ss = sum((1.0 - row['pi']) / row['pi'] ** 2 *
                    ((1.0 if row['responded'] else 0.0) - rate) ** 2 for row in resolved)
    design_ss += added * rate * (1.0 - rate)
    design_half = Z * sqrt(design_ss) / total if total else None
    projected_responded = responded + added * rate
    smoothed = (projected_responded + 1.0) / (n + added + 2.0)
    floor_half = Z * sqrt(smoothed * (1.0 - smoothed) / (n + added))
    if design_half is None:
        return floor_half
    return max(design_half, floor_half)


def exposure(store):
    sessions = {row['stream']: row for row in store.table('group_sessions')}
    persons = {}
    rooms = {}
    for player in store.table('players'):
        evidence = stream_evidence(store, player)
        course = player['course']
        session = sessions.get(player['stream'])
        if session is not None:
            room = rooms.setdefault(course, dict(confirmed=[], plausible=[], content=[],
                                                 min_people=0, max_people=0, roster=set(), streams=0))
            room['confirmed'] += evidence['confirmed']
            room['plausible'] += evidence['plausible']
            if evidence['content']:
                room['content'].append(evidence['content'])
            room['min_people'] += session['min_people']
            room['max_people'] += session['max_people']
            room['roster'] |= set(session['roster'])
            room['streams'] += 1
            for member in sorted(session['roster']):
                slot = persons.setdefault((member, course), _person_slot())
                _add_stream(slot, evidence, group=True)
        learner = player.get('learner')
        if learner:
            slot = persons.setdefault((learner, course), _person_slot())
            _add_stream(slot, evidence, group=False)
    return persons, rooms


def _person_slot():
    return dict(personal_confirmed=[], personal_plausible=[], group_confirmed=[],
                group_plausible=[], content=[], streams=0, group_streams=0)


def _add_stream(slot, evidence, group):
    if group:
        slot['group_confirmed'] += evidence['confirmed']
        slot['group_plausible'] += evidence['plausible']
        slot['group_streams'] += 1
    else:
        slot['personal_confirmed'] += evidence['confirmed']
        slot['personal_plausible'] += evidence['plausible']
        slot['streams'] += 1
    if evidence['content']:
        slot['content'].append(evidence['content'])


def build_estimates(store, journal=None):
    records = opportunities(store, journal)
    persons, rooms = exposure(store)
    courses = {row['course']: row for row in store.table('courses')}
    players = store.table('players')

    learners = []
    for (learner, course), slot in sorted(persons.items()):
        personal_records = [row for row in records
                            if row['course'] == course and row['kind'] == 'individual'
                            and _stream_learner(store, row['stream']) == learner]
        confirmed = merge_length(slot['personal_confirmed'] + slot['group_confirmed'])
        plausible = merge_length(slot['personal_plausible'] + slot['group_plausible'])
        learners.append(dict(
            learner=learner, course=course,
            streams=slot['streams'], group_sessions=slot['group_streams'],
            exposure_seconds=dict(confirmed=confirmed, plausible=plausible),
            content_distinct_seconds=merge_length(slot['content']),
            opportunities=dict(executed=sum(1 for row in personal_records if row['executed']),
                               responded=sum(1 for row in personal_records
                                             if row['executed'] and row['responded'] is True),
                               pending=sum(1 for row in personal_records
                                           if row['executed'] and row['responded'] is None)),
            individual_assessment='not_supported'))

    course_blocks = []
    cells = {}
    for course, info in sorted(courses.items()):
        streams = [row for row in players if row['course'] == course]
        course_records = [row for row in records if row['course'] == course]
        evidence_kinds = {}
        for kind in ('individual', 'group'):
            kind_records = [row for row in course_records if row['kind'] == kind]
            if not kind_records and not any(_kind_of(store, row['stream']) == kind for row in streams):
                continue
            block = ht_block(kind_records)
            strata = {}
            for stratum in sorted({row['network'] for row in streams}):
                stratum_records = [row for row in kind_records if row['stratum'] == stratum]
                if stratum_records or any(row['network'] == stratum and _kind_of(store, row['stream']) == kind
                                          for row in streams):
                    strata[stratum] = ht_block(stratum_records)
                    cells[(course, kind, stratum)] = stratum_records
            block['strata'] = strata
            evidence_kinds[kind] = block
        personal_confirmed = personal_plausible = 0
        combined_confirmed = combined_plausible = 0
        for (learner, learner_course), slot in persons.items():
            if learner_course != course:
                continue
            personal_confirmed += merge_length(slot['personal_confirmed'])
            personal_plausible += merge_length(slot['personal_plausible'])
            combined_confirmed += merge_length(slot['personal_confirmed'] + slot['group_confirmed'])
            combined_plausible += merge_length(slot['personal_plausible'] + slot['group_plausible'])
        room = rooms.get(course)
        unidentified = None
        if room is not None:
            low_people = max(0, room['min_people'] - len(room['roster']))
            high_people = max(0, room['max_people'] - len(room['roster']))
            unidentified = dict(people=[low_people, high_people],
                                seconds=[low_people * merge_length(room['confirmed']),
                                         high_people * merge_length(room['plausible'])])
        evidences = [stream_evidence(store, row) for row in streams]
        content_union = merge_length([ev['content'] for ev in evidences if ev['content']])
        unverified = sum(merge_length(ev['plausible']) - merge_length(ev['confirmed'])
                         for ev in evidences)
        individual = evidence_kinds.get('individual')
        group = evidence_kinds.get('group')
        supported_individual = _scale(individual, (personal_confirmed, personal_plausible))
        supported_group = _scale(group, tuple(unidentified['seconds'])) if unidentified else None
        supported_total = _add_ranges(supported_individual, supported_group)
        realized = dict(executed=sum(1 for row in course_records if row['executed']),
                        not_executed=sum(1 for row in course_records if not row['executed']),
                        pending=sum(1 for row in course_records
                                    if row['executed'] and row['responded'] is None),
                        inclusion_probabilities=sorted({row['pi'] for row in course_records
                                                        if row['executed']}))
        gaps = dict(unverified_heartbeat_seconds=unverified,
                    content_never_observed_seconds=max(0, info['duration'] - content_union),
                    cells_insufficient=['%s/%s/%s' % key for key, rows in cells.items()
                                        if key[0] == course and ht_block(rows)['insufficient_evidence']],
                    pending_opportunities=realized['pending'],
                    not_executed_opportunities=realized['not_executed'],
                    group_unidentified_people=unidentified['people'] if unidentified else None)
        limitations = []
        if individual and individual['resolved'] and individual['resolved'] < individual['executed']:
            limitations.append('存在已送达未回执的提问，未计入回应支持份额。')
        if individual and individual['executed']:
            limitations.append('未回应不等于没在听：回应支持份额是投入下限。')
        if room is not None:
            limitations.append('集体播放回应无法对应到个人；签到范围外人数以区间保留。')
        course_blocks.append(dict(
            course=course, topic=info['topic'],
            exposure=dict(identified_personal=[personal_confirmed, personal_plausible],
                          identified_with_roster=[combined_confirmed, combined_plausible],
                          unidentified_group=unidentified['seconds'] if unidentified else None,
                          total=[combined_confirmed + (unidentified['seconds'][0] if unidentified else 0),
                                 combined_plausible + (unidentified['seconds'][1] if unidentified else 0)]),
            content_distinct_seconds=content_union,
            evidence=evidence_kinds,
            supported_seconds=dict(individual=supported_individual,
                                   group_unidentified=supported_group,
                                   total=supported_total),
            realized_design=realized,
            evidence_gaps=gaps,
            limitations=limitations))
    return dict(learners=learners, courses=course_blocks, cells=cells, records=records,
                rooms=rooms, persons=persons)


def _stream_learner(store, stream):
    return store.find('players', stream=stream).get('learner')


def _kind_of(store, stream):
    return 'group' if store.select('group_sessions', stream=stream) else 'individual'


def _scale(block, bounds):
    if block is None or block['share'] is None:
        return None
    return [block['ci'][0] * bounds[0], block['ci'][1] * bounds[1]]


def _add_ranges(first, second):
    if first is None:
        return second
    if second is None:
        return first
    return [first[0] + second[0], first[1] + second[1]]


def comparison_view(store, estimates):
    blocks = []
    shares = {}
    for course in estimates['courses']:
        individual = course['evidence'].get('individual')
        group = course['evidence'].get('group')
        blocks.append(dict(course=course['course'], topic=course['topic'],
                           exposure_seconds_total=course['exposure']['total'],
                           supported_share=dict(individual=individual['share'] if individual else None,
                                                group=group['share'] if group else None),
                           supported_ci=dict(individual=individual['ci'] if individual else None,
                                             group=group['ci'] if group else None),
                           supported_seconds=course['supported_seconds'],
                           realized_design=course['realized_design'],
                           evidence_gaps=course['evidence_gaps']))
        if individual and individual['share'] is not None:
            shares[course['course']] = (individual['ci'], individual['insufficient_evidence'])
    overlap = {}
    names = sorted(shares)
    for position, left in enumerate(names):
        for right in names[position + 1:]:
            pair = (shares[left][0], shares[right][0])
            overlap['%s~%s' % (left, right)] = not (pair[0][1] < pair[1][0] or pair[1][1] < pair[0][0])
    notes = []
    grouped = [course for course in estimates['courses'] if course['evidence'].get('group')]
    if grouped:
        notes.append('含集体播放的课程（%s）：个人时间含签到范围区间，集体回应无法对应到个人。'
                     % '、'.join(course['course'] for course in grouped))
    notes.append('各层实际执行的抽样机会不同（弱网 0.15、正常 0.5），比较使用设计调整后的估计；原始回应率不可直接比较。')
    notes.append('估计口径 %s：调整抽样机会或改用集体播放只改变不确定性，不改变口径；口径变化不会被计入投入变化。'
                 % ESTIMAND_VERSION)
    return dict(estimand_version=ESTIMAND_VERSION, courses=blocks,
                pairwise_ci_overlap=overlap,
                ranking_supported=bool(overlap) and not any(overlap.values())
                and not any(flag for _, flag in shares.values()),
                caliber_notes=notes, usage_limits=USAGE_LIMITS)

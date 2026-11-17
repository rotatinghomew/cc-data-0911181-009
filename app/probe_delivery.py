from .storage import DataError


def deliver(store,journal,payload):
    with journal.transaction():
        plan=journal.get('probe_plan',payload['plan'])
        stream=store.find('players',stream=payload['stream'])
        if stream['stream'] not in plan['streams']:
            raise DataError('stream outside plan')
        key=plan['plan']+':'+stream['stream']
        existing=journal.all('probe_delivery')
        if any(row['delivery']==key for row in existing):
            return next(row for row in existing if row['delivery']==key)
        kind=plan.get('kind','individual')
        group_streams={row['stream'] for row in store.table('group_sessions')}
        policy=store.find('contact_policy',policy='current')
        reason=None
        if kind=='group':
            if stream['stream'] not in group_streams:
                raise DataError('group plan requires group stream')
            cap=policy.get('maximum_per_group_session')
            used=sum(row['stream']==stream['stream'] and row['status']=='delivered'
                     and row.get('kind')=='group' for row in existing)
            if cap is not None and used>=cap:
                status,reason='skipped','group_session_cap'
            else:
                status='delivered'
        else:
            if stream['stream'] in group_streams:
                raise DataError('individual plan cannot target group stream')
            learner=stream['learner']
            used=sum(row['learner']==learner and row['status']=='delivered'
                     and row.get('kind','individual')=='individual' for row in existing) if learner else 0
            if learner and used>=policy['maximum_per_person']:
                status,reason='skipped','person_cap'
            else:
                status='delivered'
        row=dict(delivery=key,plan=plan['plan'],stream=stream['stream'],learner=stream['learner'],
                 kind=kind,status=status,reason=reason,executed=status=='delivered',
                 planned_probability=plan['probability'])
        return journal.put('probe_delivery',key,row)

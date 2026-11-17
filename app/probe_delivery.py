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
        learner=stream['learner']
        policy=store.find('contact_policy',policy='current')
        used=sum(row['learner']==learner and row['status']=='delivered' for row in existing) if learner else 0
        status='skipped' if learner and used>=policy['maximum_per_person'] else 'delivered'
        row=dict(delivery=key,plan=plan['plan'],stream=stream['stream'],learner=learner,status=status,
                 planned_probability=plan['probability'])
        return journal.put('probe_delivery',key,row)

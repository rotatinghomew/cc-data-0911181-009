from .storage import DataError

KINDS=('individual','group')


def prepare(store,journal,payload):
    streams=sorted(set(payload['streams']))
    for stream in streams:
        store.find('players',stream=stream)
    probability=float(payload['probability'])
    if not 0<probability<=1:
        raise DataError('invalid inclusion probability')
    kind=payload.get('kind','individual')
    if kind not in KINDS:
        raise DataError('invalid opportunity kind')
    group_streams={row['stream'] for row in store.table('group_sessions')}
    for stream in streams:
        if kind=='group' and stream not in group_streams:
            raise DataError('group plan requires group stream: '+stream)
        if kind=='individual' and stream in group_streams:
            raise DataError('individual plan cannot target group stream: '+stream)
    row=dict(plan=payload['plan'],streams=streams,probability=probability,kind=kind,status='prepared')
    if payload.get('proposal') is not None:
        row['proposal']=str(payload['proposal'])
    with journal.transaction():
        return journal.put('probe_plan',row['plan'],row)

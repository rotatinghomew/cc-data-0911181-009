from .storage import DataError


def prepare(store,journal,payload):
    streams=sorted(set(payload['streams']))
    for stream in streams:
        store.find('players',stream=stream)
    probability=float(payload['probability'])
    if not 0<probability<=1:
        raise DataError('invalid inclusion probability')
    row=dict(plan=payload['plan'],streams=streams,probability=probability,status='prepared')
    with journal.transaction():
        return journal.put('probe_plan',row['plan'],row)

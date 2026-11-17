from .domain import playback_summary
from .storage import single


def playback(store,query):
    course=single(query,'course')
    return {'streams':[playback_summary(store,row) for row in store.table('players')
                       if course is None or row['course']==course],
            'unit':'device_stream_observations'}


def probes(store,query):
    rows=store.table('probes')
    return {'sampled':len(rows),'delivered':sum(row['delivered'] for row in rows),
            'responses':sum(row['responded'] is True for row in rows),
            'design':store.table('sampling'),'events':rows}


ROUTES={'/api/playback':playback,'/api/probes':probes}

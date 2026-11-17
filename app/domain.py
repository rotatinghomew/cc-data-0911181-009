def nominal_interval(walls):
    gaps=[b-a for a,b in zip(walls,walls[1:]) if b>a]
    return min(gaps) if gaps else 60


def confirmed_segments(walls,interval):
    return [(a,a+min(b-a,interval)) for a,b in zip(walls,walls[1:])]


def merge_length(segments):
    total=0
    start=end=None
    for a,b in sorted(segments):
        if start is None:
            start,end=a,b
        elif a<=end:
            end=max(end,b)
        else:
            total+=end-start
            start,end=a,b
    if start is not None:
        total+=end-start
    return total


def stream_evidence(store,player):
    rows=sorted(store.select('heartbeats',stream=player['stream']),key=lambda row:row['wall'])
    walls=[row['wall'] for row in rows]
    contents=[row['content'] for row in rows]
    evidence=dict(heartbeats=len(walls),confirmed=[],plausible=[],content=None,interval=None,
                  first_wall=walls[0] if walls else None,last_wall=walls[-1] if walls else None,
                  content_first=contents[0] if contents else None,
                  content_last=contents[-1] if contents else None,
                  content_max=max(contents) if contents else None)
    if len(walls)>=2:
        interval=nominal_interval(walls)
        evidence.update(confirmed=confirmed_segments(walls,interval),
                        plausible=[(walls[0],walls[-1])],interval=interval,
                        content=(min(contents),max(contents)))
    return evidence


def playback_summary(store,player):
    rows=sorted(store.select('heartbeats',stream=player['stream']),key=lambda row:row['wall'])
    walls=[row['wall'] for row in rows]
    evidence=stream_evidence(store,player)
    summary=dict(stream=player['stream'],learner=player['learner'],course=player['course'],
                heartbeats=len(rows),first_wall=evidence['first_wall'],
                last_wall=evidence['last_wall'],
                observed_wall_span=evidence['last_wall']-evidence['first_wall'] if walls else None,
                content_positions=[row['content'] for row in rows])
    if evidence['interval'] is not None:
        confirmed=merge_length(evidence['confirmed'])
        plausible=merge_length(evidence['plausible'])
        summary.update(nominal_interval=evidence['interval'],confirmed_seconds=confirmed,
                       plausible_seconds=plausible,unverified_seconds=plausible-confirmed,
                       content_unobserved_tail=max(0,player['duration']-evidence['content_max']))
    else:
        summary.update(nominal_interval=None,confirmed_seconds=None,plausible_seconds=None,
                       unverified_seconds=None,content_unobserved_tail=None)
    return summary

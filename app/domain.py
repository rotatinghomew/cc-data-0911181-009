def playback_summary(store,player):
    rows=store.select('heartbeats',stream=player['stream'])
    walls=[row['wall'] for row in rows]
    return dict(stream=player['stream'],learner=player['learner'],course=player['course'],
                heartbeats=len(rows),first_wall=min(walls) if walls else None,
                last_wall=max(walls) if walls else None,
                observed_wall_span=max(walls)-min(walls) if walls else None,
                content_positions=[row['content'] for row in rows])

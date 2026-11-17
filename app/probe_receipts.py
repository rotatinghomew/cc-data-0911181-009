from .storage import DataError


def record(store,journal,payload):
    with journal.transaction():
        delivery=journal.get('probe_delivery',payload['delivery'])
        if delivery['status']!='delivered':
            raise DataError('cannot respond to skipped delivery')
        response=payload['responded']
        if not isinstance(response,bool):
            raise DataError('boolean response required')
        return journal.put('probe_receipt',delivery['delivery'],dict(delivery=delivery['delivery'],
                           stream=delivery['stream'],responded=response,planned_probability=delivery['planned_probability']))

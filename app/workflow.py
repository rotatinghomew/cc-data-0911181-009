from . import sampling_plan
from . import probe_delivery
from . import probe_receipts

OPERATIONS={
    'sampling_plan.prepare':sampling_plan.prepare,
    'probe_delivery.deliver':probe_delivery.deliver,
    'probe_receipts.record':probe_receipts.record,
}

from pathlib import Path
import tempfile
import unittest
from app.journal import Journal
from app.storage import DataError,Store

DATA=Path(__file__).parents[1]/'data'


class BusinessChainTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(DATA)
        self.journal=Journal()

    def tearDown(self):
        self.journal.close()

    def test_business_chain(self):
        self.exercise(self.store,self.journal)

    def test_rollback_on_failure(self):
        with self.assertRaises(DataError):
            with self.journal.transaction():
                self.journal.put('test','a',{'value':1})
                raise DataError('aborted')
        self.assertEqual(self.journal.all('test'),[])

    def test_restart_retains_business_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'workflow.sqlite3'
            journal=Journal(path)
            try:
                self.exercise(self.store,journal)
                before={kind[0] for kind in journal.connection.execute('SELECT DISTINCT kind FROM documents')}
                snapshot={kind:journal.all(kind) for kind in before}
            finally:
                journal.close()
            reopened=Journal(path)
            try:
                self.assertEqual(snapshot,{kind:reopened.all(kind) for kind in before})
            finally:
                reopened.close()

    def exercise(self,store,journal):
        from app import sampling_plan,probe_delivery,probe_receipts
        sampling_plan.prepare(store,journal,dict(plan='s1',streams=['P0-0','P0-1'],probability=0.5))
        first=probe_delivery.deliver(store,journal,dict(plan='s1',stream='P0-0'))
        second=probe_delivery.deliver(store,journal,dict(plan='s1',stream='P0-1'))
        self.assertEqual(first['status'],'delivered')
        self.assertEqual(second['status'],'skipped')
        self.assertFalse(probe_receipts.record(store,journal,dict(delivery=first['delivery'],responded=False))['responded'])
        with self.assertRaises(DataError):
            probe_receipts.record(store,journal,dict(delivery=second['delivery'],responded=True))

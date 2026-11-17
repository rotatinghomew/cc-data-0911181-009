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


class OpportunityKindTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(DATA)
        self.journal=Journal()

    def tearDown(self):
        self.journal.close()

    def test_group_and_individual_budgets_are_separate(self):
        from app import sampling_plan,probe_delivery
        sampling_plan.prepare(self.store,self.journal,dict(plan='g1',streams=['Pgroup'],
                                                           probability=1.0,kind='group'))
        group=probe_delivery.deliver(self.store,self.journal,dict(plan='g1',stream='Pgroup'))
        self.assertEqual(group['status'],'delivered')
        self.assertEqual(group['kind'],'group')
        sampling_plan.prepare(self.store,self.journal,dict(plan='i1',streams=['P0-0','P0-1'],probability=1.0))
        first=probe_delivery.deliver(self.store,self.journal,dict(plan='i1',stream='P0-0'))
        second=probe_delivery.deliver(self.store,self.journal,dict(plan='i1',stream='P0-1'))
        self.assertEqual(first['status'],'delivered')
        self.assertEqual(second['status'],'skipped')
        self.assertEqual(second['reason'],'person_cap')
        self.assertFalse(second['executed'])

    def test_plan_kind_must_match_stream(self):
        from app import sampling_plan
        with self.assertRaises(DataError):
            sampling_plan.prepare(self.store,self.journal,dict(plan='bad1',streams=['Pgroup'],probability=0.5))
        with self.assertRaises(DataError):
            sampling_plan.prepare(self.store,self.journal,dict(plan='bad2',streams=['P1-0'],
                                                               probability=0.5,kind='group'))
        with self.assertRaises(DataError):
            sampling_plan.prepare(self.store,self.journal,dict(plan='bad3',streams=['P1-0'],
                                                               probability=0.5,kind='unknown'))

    def test_group_session_cap(self):
        from app import sampling_plan,probe_delivery
        policy=self.store.find('contact_policy',policy='current')
        for round_ in range(policy['maximum_per_group_session']):
            plan='g%d'%round_
            sampling_plan.prepare(self.store,self.journal,dict(plan=plan,streams=['Pgroup'],
                                                               probability=1.0,kind='group'))
            row=probe_delivery.deliver(self.store,self.journal,dict(plan=plan,stream='Pgroup'))
            self.assertEqual(row['status'],'delivered')
        sampling_plan.prepare(self.store,self.journal,dict(plan='gover',streams=['Pgroup'],
                                                           probability=1.0,kind='group'))
        over=probe_delivery.deliver(self.store,self.journal,dict(plan='gover',stream='Pgroup'))
        self.assertEqual(over['status'],'skipped')
        self.assertEqual(over['reason'],'group_session_cap')

    def test_estimates_reflect_executed_only(self):
        from app import sampling_plan,probe_delivery,probe_receipts
        from app.estimation import build_estimates
        sampling_plan.prepare(self.store,self.journal,dict(plan='s1',streams=['P0-0','P0-1'],probability=0.5))
        probe_delivery.deliver(self.store,self.journal,dict(plan='s1',stream='P0-0'))
        probe_delivery.deliver(self.store,self.journal,dict(plan='s1',stream='P0-1'))
        course=next(row for row in build_estimates(self.store,self.journal)['courses'] if row['course']=='C0')
        self.assertEqual(course['realized_design']['executed'],17)
        self.assertEqual(course['realized_design']['pending'],1)
        self.assertEqual(course['evidence']['individual']['resolved'],14)
        delivery='s1:P0-0'
        probe_receipts.record(self.store,self.journal,dict(delivery=delivery,responded=True))
        after=next(row for row in build_estimates(self.store,self.journal)['courses'] if row['course']=='C0')
        self.assertEqual(after['evidence']['individual']['resolved'],15)
        self.assertEqual(after['evidence']['individual']['pending'],0)

    def test_plan_proposal_receipts_cross_check(self):
        from app import sampling_plan,probe_delivery,probe_receipts
        from app.analysis import ROUTES
        proposal=ROUTES['/api/coverage'](self.store,{},None)['proposal_id']
        sampling_plan.prepare(self.store,self.journal,dict(plan='n1',streams=['P3-0'],probability=1.0,
                                                           proposal=proposal))
        probe_delivery.deliver(self.store,self.journal,dict(plan='n1',stream='P3-0'))
        probe_receipts.record(self.store,self.journal,dict(delivery='n1:P3-0',responded=True))
        coverage=ROUTES['/api/coverage'](self.store,{},self.journal)
        plan=next(row for row in coverage['reconciliation']['plans'] if row['plan']=='n1')
        self.assertEqual(plan['proposal'],proposal)
        self.assertEqual(plan['executed'],1)
        self.assertEqual(plan['receipts'],1)
        self.assertEqual(plan['responded'],1)
        self.assertEqual(plan['pending'],0)
        self.assertTrue(plan['checks']['realized_within_plan'])
        self.assertTrue(plan['checks']['receipts_only_for_delivered'])
        individual=coverage['budgets']['individual']
        self.assertEqual(individual['remaining_disturbances'],19)

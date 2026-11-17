from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from app.analysis import ROUTES
from app.reporting import report
from app.service import dispatch, make_server
from app.storage import DataError, Store, integer, single

DATA = Path(__file__).parents[1] / 'data'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)

    def test_every_table_has_data(self):
        self.assertTrue(all(count > 0 for count in self.store.inventory().values()))

    def test_isolated_reads(self):
        name = next(iter(self.store.schema))
        rows = self.store.table(name)
        rows[0]['accidental_edit'] = True
        self.assertNotIn('accidental_edit', self.store.table(name)[0])

    def test_stable_fingerprint(self):
        self.assertEqual(self.store.fingerprint(), Store(DATA).fingerprint())
        self.assertEqual(len(self.store.fingerprint()), 64)

    def test_duplicate_primary_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data'
            shutil.copytree(DATA, target)
            name = next(iter(self.store.schema))
            path = target / (name + '.json')
            rows = json.loads(path.read_text())
            rows.append(deepcopy(rows[0]))
            path.write_text(json.dumps(rows))
            with self.assertRaises(DataError):
                Store(target)

    def test_missing_required_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data'
            shutil.copytree(DATA, target)
            name, contract = next(iter(self.store.schema.items()))
            path = target / (name + '.json')
            rows = json.loads(path.read_text())
            del rows[0][contract['required'][0]]
            path.write_text(json.dumps(rows))
            with self.assertRaises(DataError):
                Store(target)

    def test_query_boundaries(self):
        self.assertEqual(integer({}, 'limit', 5), 5)
        for query in [{'limit': ['x']}, {'limit': ['-1']}, {'limit': ['1', '2']}]:
            with self.assertRaises(DataError):
                integer(query, 'limit', 5)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(DATA)
        cls.server = make_server(cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_health_and_routes_over_http(self):
        for path in ['/health', *ROUTES]:
            with self.subTest(path=path), urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn('application/json', response.headers['Content-Type'])
                self.assertIsInstance(json.load(response), dict)

    def test_unknown_route(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/does-not-exist', timeout=3)
        self.assertEqual(caught.exception.code, 404)

    def test_bad_table(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/api/records?table=unknown', timeout=3)
        self.assertEqual(caught.exception.code, 400)

    def test_report_uses_same_analysis(self):
        result = report(self.store)
        self.assertEqual(result['dataset_fingerprint'], self.store.fingerprint())
        for path in ROUTES:
            self.assertEqual(result['sections'][path], dispatch(self.store, path, {}))


from app.domain import playback_summary


class PlaybackTests(unittest.TestCase):
    def test_missing_heartbeat_is_not_filled(self):
        store=Store(DATA)
        row=playback_summary(store,store.find('players',stream='P0-0'))
        self.assertEqual(row['heartbeats'],10)
        self.assertEqual(row['observed_wall_span'],780)

    def test_group_has_no_invented_person(self):
        store=Store(DATA)
        row=playback_summary(store,store.find('players',stream='Pgroup'))
        self.assertIsNone(row['learner'])
        self.assertEqual(store.find('group_sessions',stream='Pgroup')['min_people'],7)

    def test_delivery_and_response_are_different(self):
        rows=dispatch(Store(DATA),'/api/probes',{})
        self.assertGreater(rows['sampled'],rows['delivered'])
        self.assertGreater(rows['delivered'],rows['responses'])

    def test_double_device_remains_two_streams(self):
        store=Store(DATA)
        streams=store.select('players',learner='L0')
        self.assertEqual(len(streams),2)
        self.assertEqual(len(report(store)['sections']['/api/playback']['streams']),26)


from app.estimation import build_estimates
from app.journal import Journal


class EstimationTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(DATA)

    def learner(self,estimates,learner,course):
        return next(row for row in estimates['learners']
                    if row['learner']==learner and row['course']==course)

    def course(self,estimates,course):
        return next(row for row in estimates['courses'] if row['course']==course)

    def test_heartbeat_gap_bounded_not_filled_or_zeroed(self):
        weak=playback_summary(self.store,self.store.find('players',stream='P0-0'))
        self.assertEqual(weak['confirmed_seconds'],540)
        self.assertEqual(weak['plausible_seconds'],780)
        self.assertEqual(weak['unverified_seconds'],240)
        normal=playback_summary(self.store,self.store.find('players',stream='P1-0'))
        self.assertEqual(normal['confirmed_seconds'],840)
        self.assertEqual(normal['plausible_seconds'],840)
        self.assertEqual(normal['unverified_seconds'],0)

    def test_overlapping_devices_do_not_inflate_person_time(self):
        estimates=build_estimates(self.store)
        row=self.learner(estimates,'L0','C0')
        self.assertEqual(row['exposure_seconds']['plausible'],1140)
        self.assertLess(row['exposure_seconds']['confirmed'],1140)
        self.assertEqual(row['content_distinct_seconds'],960)
        device_sum=sum(playback_summary(self.store,stream)['plausible_seconds']
                       for stream in self.store.select('players',learner='L0'))
        self.assertEqual(device_sum,1620)
        self.assertLess(row['exposure_seconds']['plausible'],device_sum)

    def test_group_identity_uncertainty_preserved(self):
        estimates=build_estimates(self.store)
        course=self.course(estimates,'C0')
        self.assertEqual(course['exposure']['unidentified_group'],[3360,7560])
        self.assertEqual(course['evidence_gaps']['group_unidentified_people'],[4,9])
        roster=self.learner(estimates,'L2','C0')
        self.assertEqual(roster['group_sessions'],1)
        self.assertEqual(roster['exposure_seconds']['plausible'],840)
        self.assertEqual(course['exposure']['identified_personal'],[5760,6360])
        self.assertEqual(course['exposure']['identified_with_roster'],[7740,8100])

    def test_estimates_use_only_executed_opportunities(self):
        estimates=build_estimates(self.store)
        course=self.course(estimates,'C0')
        weak=course['evidence']['individual']['strata']['weak']
        self.assertEqual(weak['executed'],4)
        self.assertEqual(weak['not_executed'],2)
        self.assertEqual(course['realized_design']['executed'],16)
        self.assertEqual(course['realized_design']['not_executed'],4)

    def test_response_supported_share_with_uncertainty(self):
        estimates=build_estimates(self.store)
        c0=self.course(estimates,'C0')['evidence']['individual']
        self.assertAlmostEqual(c0['share'],0.8286,places=4)
        c2=self.course(estimates,'C2')['evidence']['individual']
        self.assertEqual(c2['share'],1.0)
        self.assertGreater(c2['ci_half'],0)
        for course in estimates['courses']:
            for block in course['evidence'].values():
                if block['ci'] is not None:
                    self.assertGreaterEqual(block['ci'][0],0)
                    self.assertLessEqual(block['ci'][1],1)

    def test_nonresponse_and_pending_kept_as_limitations(self):
        estimates=build_estimates(self.store)
        c1=self.course(estimates,'C1')
        self.assertLess(c1['evidence']['individual']['share'],1.0)
        self.assertTrue(any('未回应' in note for note in c1['limitations']))
        journal=Journal()
        try:
            from app import sampling_plan,probe_delivery
            sampling_plan.prepare(self.store,journal,dict(plan='p1',streams=['P2-0'],probability=1.0))
            probe_delivery.deliver(self.store,journal,dict(plan='p1',stream='P2-0'))
            pending=self.course(build_estimates(self.store,journal),'C2')
            self.assertEqual(pending['evidence']['individual']['pending'],1)
            self.assertEqual(pending['evidence']['individual']['resolved'],12)
        finally:
            journal.close()

    def test_no_individual_verdict(self):
        estimates=build_estimates(self.store)
        for row in estimates['learners']:
            self.assertEqual(row['individual_assessment'],'not_supported')
            self.assertNotIn('score',row)
            self.assertNotIn('rank',row)
        result=dispatch(self.store,'/api/engagement',{})
        self.assertTrue(any('不能用于评价个人' in note for note in result['usage_limits']))

    def test_comparison_keeps_caliber_and_ranking_guard(self):
        result=dispatch(self.store,'/api/comparison',{})
        self.assertFalse(result['ranking_supported'])
        self.assertTrue(all(result['pairwise_ci_overlap'].values()))
        self.assertTrue(any('集体' in note for note in result['caliber_notes']))
        self.assertTrue(any('口径' in note for note in result['caliber_notes']))
        c0=next(row for row in result['courses'] if row['course']=='C0')
        self.assertEqual(c0['evidence_gaps']['unverified_heartbeat_seconds'],780)
        self.assertEqual(c0['evidence_gaps']['content_never_observed_seconds'],240)

    def test_coverage_driven_by_uncertainty_within_caps(self):
        result=dispatch(self.store,'/api/coverage',{})
        individual=sum(cell['proposed_opportunities'] for cell in result['cells']
                       if cell['kind']=='individual')
        group=sum(cell['proposed_opportunities'] for cell in result['cells']
                  if cell['kind']=='group')
        self.assertEqual(individual,result['budgets']['individual']['requested'])
        self.assertEqual(group,result['budgets']['group']['requested'])
        for cell in result['cells']:
            if cell['insufficient_evidence']:
                self.assertGreaterEqual(cell['proposed_opportunities'],1)
            self.assertLessEqual(cell['proposed_opportunities'],cell['eligible_streams']
                                 if cell['kind']=='individual' else
                                 result['budgets']['group']['per_session_cap'])
        tightest=min((cell for cell in result['cells'] if cell['ci']),
                     key=lambda cell: cell['ci'][1]-cell['ci'][0])
        self.assertEqual(tightest['proposed_opportunities'],0)
        zero=dispatch(self.store,'/api/coverage',{'individual':['0'],'group':['0']})
        self.assertEqual(sum(cell['proposed_opportunities'] for cell in zero['cells']),0)

    def test_comparison_coverage_receipts_cross_check(self):
        engagement=dispatch(self.store,'/api/engagement',{})
        comparison=dispatch(self.store,'/api/comparison',{})
        coverage=dispatch(self.store,'/api/coverage',{})
        probes=dispatch(self.store,'/api/probes',{})
        c0=next(row for row in engagement['courses'] if row['course']=='C0')
        c0c=next(row for row in comparison['courses'] if row['course']=='C0')
        self.assertEqual(c0['realized_design'],c0c['realized_design'])
        cell=next(row for row in coverage['cells']
                  if row['course']=='C0' and row['kind']=='individual' and row['stratum']=='normal')
        self.assertEqual(cell['executed'],c0['evidence']['individual']['strata']['normal']['executed'])
        sampled=coverage['reconciliation']['sampled_probes']
        self.assertEqual(sampled['executed'],probes['executed'])
        self.assertEqual(sampled['sampled'],probes['sampled'])
        self.assertEqual(probes['executed'],42)

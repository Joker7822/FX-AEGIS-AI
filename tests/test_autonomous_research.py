"""Research-cycle safety tests. All quotes are synthetic; no APIs or trading."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autonomous_research as cycle
from execution_lab import make_fixture, write_json
from h1_evolution_guard import fixture_catalog


class AutonomousResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.quotes, self.provenance = make_fixture(self.base / 'fixture', n=1250)
        self.catalog = self.base / 'universe.json'
        fixture_catalog(self.catalog)
        self.output = self.base / 'outputs'
        self.report = None

    def call_cycle(self, **overrides):
        opts = dict(quotes_path=self.quotes, provenance_path=self.provenance,
                    universe_path=self.catalog, out=self.output, allow_synthetic=True)
        opts.update(overrides)
        return cycle.run_cycle(**opts)

    def mocked_cycle(self, **overrides):
        from execution_lab import load_quotes
        q, _, audit = load_quotes(self.quotes, self.provenance, allow_synthetic=True)
        model_report = {
            'approved_for_live_trading': False,
            'quote_sha256': audit['quotes_sha256'],
            'model_families': ['LogisticRegression', 'HistGradientBoostingClassifier'],
            'sections': {'validation': {'trades': 0, 'retrain_events': 1},
                         'diagnostic_only': {'trades': 0, 'retrain_events': 1}},
        }
        evo_report = {'approved_for_live_trading': False,
                      'quote_sha256': audit['quotes_sha256'],
                      'statistically_screened_genes_for_RESEARCH_ONLY': [],
                      'status': 'SYNTHETIC_TEST_ONLY'}
        with (patch.object(cycle, 'ml_retrain', return_value=model_report) as ml,
              patch.object(cycle, 'research_audit', return_value=evo_report) as evo,
              patch.object(cycle, 'export_overlay', return_value={'trades': 0, 'predictions': 1}) as chart):
            result = self.call_cycle(**overrides)
        self.report = result
        return result, ml, evo, chart

    def test_full_cycle_is_research_only(self):
        result, ml, evo, chart = self.mocked_cycle()
        self.assertEqual(result['cycle_number'], 1)
        self.assertEqual(result['status'], 'SYNTHETIC_TEST_ONLY')
        self.assertFalse(result['approved_for_live_trading'])
        self.assertFalse(result['eligible_for_paper_promotion'])
        self.assertEqual(result['broker_access'], 'DISABLED')
        self.assertEqual(result['decision'], 'HOLD_RESEARCH_ONLY_NO_AUTO_PROMOTION')
        self.assertEqual(ml.call_count, 1)
        self.assertEqual(evo.call_count, 1)
        self.assertEqual(chart.call_count, 1)
        self.assertTrue((self.output / 'runs' / result['run_id'] / 'cycle_summary.json').exists())

    def test_same_snapshot_skips_training(self):
        self.mocked_cycle()
        with patch.object(cycle, 'ml_retrain', side_effect=AssertionError('must not refit')):
            skipped = self.call_cycle()
        self.assertEqual(skipped['status'], 'NO_NEW_H1_BARS')
        self.assertEqual(skipped['run_count'], 1)

    def test_small_growth_waits_for_minimum(self):
        self.mocked_cycle()
        self.quotes, self.provenance = make_fixture(self.base / 'fixture', n=1260)
        with patch.object(cycle, 'ml_retrain', side_effect=AssertionError('must not refit')):
            result = self.call_cycle()
        self.assertEqual(result['status'], 'WAIT_MORE_COMPLETED_H1_BARS')
        self.assertEqual(result['new_bars'], 10)

    def test_append_new_bars_advances_cycle(self):
        self.mocked_cycle()
        self.quotes, self.provenance = make_fixture(self.base / 'fixture', n=1275)
        result, ml, _, _ = self.mocked_cycle()
        self.assertEqual(result['cycle_number'], 2)
        self.assertEqual(result['new_observed_h1_bars'], 25)
        self.assertEqual(ml.call_count, 1)
        self.assertEqual(json.loads((self.output/'state.json').read_text())['run_count'], 2)

    def test_rewriting_history_is_rejected(self):
        self.mocked_cycle()
        self.quotes, self.provenance = make_fixture(self.base / 'fixture', n=1275, seed=12)
        with self.assertRaisesRegex(ValueError, 'revised'):
            self.call_cycle()

    def test_source_provenance_change_is_rejected(self):
        self.mocked_cycle()
        self.quotes, self.provenance = make_fixture(self.base / 'fixture', n=1275)
        meta=json.loads(self.provenance.read_text())
        meta['source_name']='changed broker source'
        write_json(self.provenance,meta)
        with self.assertRaisesRegex(ValueError, 'Data source changed'):
            self.call_cycle()

    def test_policy_change_is_rejected(self):
        self.mocked_cycle()
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            self.call_cycle(policy=cycle.CyclePolicy(min_new_h1_bars=48))

    def test_forged_state_fails_closed(self):
        self.mocked_cycle()
        path=self.output / 'state.json'
        state=json.loads(path.read_text())
        state['run_count']=9999
        write_json(path,state)
        with self.assertRaisesRegex(ValueError, 'modified'):
            self.call_cycle()

    def test_concurrent_lock_fails_closed(self):
        (self.output / '.cycle_lock').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'Another cycle'):
            self.call_cycle()

    def test_no_real_data_without_credentials_or_origin(self):
        with self.assertRaisesRegex(ValueError, 'Synthetic'):
            self.call_cycle(allow_synthetic=False)

    def test_unsafe_publish_location_rejected(self):
        with self.assertRaisesRegex(ValueError, 'public/site'):
            self.call_cycle(out=cycle.PROJECT / 'docs' / 'broker-report')

    def test_incomplete_catalog_rejected(self):
        meta=json.loads(self.catalog.read_text())
        meta['historical_trials_total']+=1
        # The hash has become invalid; fail closed before testing.
        write_json(self.catalog,meta)
        with self.assertRaisesRegex(ValueError, 'hash/schema'):
            self.call_cycle()

    def test_failure_never_publishes_or_advances(self):
        from execution_lab import load_quotes
        _, _, audit=load_quotes(self.quotes,self.provenance,allow_synthetic=True)
        with patch.object(cycle,'ml_retrain',side_effect=RuntimeError('simulated training error')):
            with self.assertRaisesRegex(RuntimeError,'training error'):
                self.call_cycle()
        self.assertFalse((self.output/'state.json').exists())
        self.assertEqual(list((self.output/'runs').iterdir()),[])
        self.assertFalse((self.output/'.cycle_lock').exists())

    def test_state_deletion_wont_silently_reset(self):
        self.mocked_cycle()
        (self.output / 'state.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Orphaned'):
            self.call_cycle()


if __name__ == '__main__':
    unittest.main()

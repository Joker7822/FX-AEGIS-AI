"""All tests are offline; no real market quote or real broker credentials."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from execution_lab import make_fixture, sha256_file, write_json
from forward_monitor import start
from forward_supervisor import run_once
from h1_ml_retrainer import RetrainConfig
from forward_dashboard_bridge import (
    _metrics, _validated_pred, build_report, write_export, _stable_inputs,
)


class PrivateDashboardBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        path = Path(cls.shared.name)
        q, m = make_fixture(path / 'all', n=540, seed=35)
        cls.frame = pd.read_csv(q)
        cls.metadata = json.loads(m.read_text(encoding='utf-8'))
        cls.asof = (pd.Timestamp(cls.frame.iloc[449]['time']) + pd.Timedelta(hours=1)).to_pydatetime()

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.monitor = self.root / 'private_monitor'
        self.quotes = self.root / 'snapshot.csv'
        self.provenance = self.root / 'provenance.json'
        self.snapshot(450)
        start(quotes_path=self.quotes, provenance_path=self.provenance, out=self.monitor,
              cfg=RetrainConfig(min_training_bars=300, max_training_bars=400, probability_threshold=.51),
              allow_synthetic=True, _test_asof=self.asof)

    def snapshot(self, n):
        self.frame.iloc[:n].to_csv(self.quotes, index=False, float_format='%.6f')
        m = dict(self.metadata)
        m['sha256'] = sha256_file(self.quotes)
        write_json(self.provenance, m)

    def poll(self, n=451, *, lag=5):
        self.snapshot(n)
        clock = (pd.Timestamp(self.frame.iloc[n - 1]['time']) +
                 pd.Timedelta(hours=1, minutes=lag)).to_pydatetime()
        return run_once(quotes_path=self.quotes, provenance_path=self.provenance,
                        out=self.monitor, allow_synthetic=True, _test_asof=clock)

    def test_refuses_to_export_before_first_poll(self):
        with self.assertRaisesRegex(ValueError, 'No supervised observation'):
            build_report(self.monitor)

    def test_offline_export_has_no_raw_price_or_broker_order_fields(self):
        self.poll()
        report = build_report(self.monitor)
        self.assertEqual(report['schema'], 'fx-aegis-private-forward-dashboard-v1')
        self.assertEqual(report['status'], 'SYNTHETIC_TEST_ONLY')
        self.assertEqual(report['metrics']['overall']['predictions'], 1)
        self.assertEqual(report['metrics']['overall']['pending'], 1)
        self.assertFalse(report['approved_for_live_trading'])
        payload = json.dumps(report)
        for forbidden in ('bid_open', 'ask_open', 'api_token', 'orders_sent', 'entry_fill_jpy_per_usd'):
            self.assertNotIn(forbidden, payload)
        result = write_export(self.monitor, self.monitor / 'forward_status.json')
        self.assertEqual(result['status'], 'PRIVATE_EXPORT_READY')
        self.assertEqual((self.monitor / 'forward_status.json').stat().st_mode & 0o777, 0o600)

    def test_idle_heartbeat_export_remains_possible(self):
        self.poll(451)
        self.poll(451)
        report = build_report(self.monitor)
        self.assertEqual(report['supervisor_status'], 'IDLE_NO_NEW_H1')
        self.assertEqual(report['metrics']['overall']['pending'], 1)

    def test_scoring_is_only_after_future_observation(self):
        self.poll(451)
        self.assertEqual(build_report(self.monitor)['metrics']['overall']['scored'], 0)
        self.poll(452)
        self.assertEqual(build_report(self.monitor)['metrics']['overall']['scored'], 0)
        self.poll(453)
        report = build_report(self.monitor)
        self.assertEqual(report['metrics']['overall']['scored'], 1)
        self.assertEqual(report['metrics']['overall']['pending'], 2)
        self.assertIsInstance(report['predictions'][0]['actual_up'], bool)

    def test_unequal_status_and_event_are_rejected(self):
        self.poll()
        path = self.monitor / 'operations_status.json'
        status = json.loads(path.read_text())
        status['seq'] += 1
        write_json(path, status)
        with self.assertRaisesRegex(ValueError, 'status and journal disagree'):
            build_report(self.monitor)

    def test_monitor_state_mutation_is_rejected(self):
        self.poll()
        path = self.monitor / 'monitor_state.json'
        state = json.loads(path.read_text())
        state['last_seen_rows'] += 1
        write_json(path, state)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            build_report(self.monitor)

    def test_reject_public_export_destination(self):
        self.poll()
        path = Path(__file__).resolve().parents[1] / 'docs' / 'forward_status.json'
        with self.assertRaisesRegex(ValueError, 'publish|inside the monitor'):
            write_export(self.monitor, path)

    def test_reject_random_export_destination(self):
        self.poll()
        with self.assertRaisesRegex(ValueError, 'inside the monitor'):
            write_export(self.monitor, self.root / 'not_monitored.json')

    def test_reject_export_while_locked(self):
        self.poll()
        (self.monitor / '.supervisor_lock').mkdir()
        with self.assertRaisesRegex(ValueError, 'locked'):
            build_report(self.monitor)

    def test_reject_backdated_or_duplicate_prediction(self):
        self.poll()
        state_path = self.monitor / 'monitor_state.json'
        state = json.loads(state_path.read_text())
        dup = state['predictions'][0].copy()
        state['predictions'].append(dup)
        from forward_monitor import _persist
        _persist(state_path, state)
        with self.assertRaisesRegex(ValueError, 'integrity|disagree'):
            build_report(self.monitor)

    def test_metrics_and_invalid_probabilities(self):
        rows = [dict(probability_up=.7, actual_up=True), dict(probability_up=.3, actual_up=False),
                dict(probability_up=.5, actual_up=None)]
        m = _metrics(rows)
        self.assertEqual((m['scored'], m['pending'], m['accuracy']), (2, 1, 1.0))
        self.assertAlmostEqual(m['brier'], .09)
        with self.assertRaisesRegex(ValueError, 'probability'):
            _validated_pred(dict(signal_index=1, signal_bar_open_utc='2026-01-01T00:00:00Z',
                                 predicted_at_utc='2026-01-01T01:05:00Z', probability_up=float('nan'),
                                 actual_up=None, scored_at_utc=None, side=0))

    def test_input_hash_changes_when_snapshot_changes(self):
        original = _stable_inputs((self.quotes, self.provenance))
        self.snapshot(451)
        self.assertNotEqual(original, _stable_inputs((self.quotes, self.provenance)))


if __name__ == '__main__':
    unittest.main()

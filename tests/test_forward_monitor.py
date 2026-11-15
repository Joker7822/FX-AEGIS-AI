"""All tests use artificial H1 quotes and a test-only simulated capture clock."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import pandas as pd

from execution_lab import make_fixture, sha256_file, write_json
from forward_monitor import MonitorPolicy, _utc_now, observe, start
from h1_ml_retrainer import RetrainConfig


class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        q, m = make_fixture(cls.root / "seed", n=620, seed=35)
        cls.all_quotes = pd.read_csv(q)
        cls.meta = json.loads(m.read_text())
        cls.freeze_count = 450
        cls.asof = (pd.Timestamp(cls.all_quotes.iloc[449]["time"]) + pd.Timedelta(hours=1)).to_pydatetime()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.case.cleanup)
        self.path = Path(self.case.name)
        self.out = self.path / "journal"
        self.q, self.m = self.snapshot(450)
        self.policy = MonitorPolicy(max_capture_lag_minutes=15)
        self.initialise()

    def snapshot(self, count, *, values=None):
        q, m = self.path / "quotes.csv", self.path / "provenance.json"
        df = self.all_quotes.iloc[:count].copy() if values is None else values
        df.to_csv(q, index=False, float_format="%.6f")
        data = dict(self.meta)
        data["sha256"] = sha256_file(q)
        write_json(m, data)
        return q, m

    def initialise(self, *, policy=None):
        return start(quotes_path=self.q, provenance_path=self.m, out=self.out,
                     cfg=RetrainConfig(min_training_bars=300, max_training_bars=400,
                                       probability_threshold=.51),
                     policy=policy or self.policy, allow_synthetic=True, _test_asof=self.asof)

    def advance(self, count, lag_minutes=5):
        self.snapshot(count)
        now = (pd.Timestamp(self.all_quotes.iloc[count-1]["time"]) +
               pd.Timedelta(hours=1, minutes=lag_minutes)).to_pydatetime()
        return observe(quotes_path=self.q, provenance_path=self.m, out=self.out,
                       allow_synthetic=True, _test_asof=now)

    def state(self):
        return json.loads((self.out / "monitor_state.json").read_text())

    def test_freeze_paper_only(self):
        r=self.state()
        self.assertEqual(r["last_seen_rows"], 450)
        self.assertEqual(r["monitor_status"], "ACTIVE")
        self.assertFalse(r["approved_for_live_trading"])
        self.assertTrue((self.out / "immutable_model_lock.json").is_file())

    def test_timely_bar_generates_prospective_prediction(self):
        r=self.advance(451)
        self.assertEqual(r["new_bars"],1)
        self.assertEqual(r["new_time_qualified_predictions"],1)
        self.assertEqual(r["late_unqualified_bars"],0)
        self.assertEqual(len(self.state()["predictions"]),1)
        self.assertIsNone(self.state()["predictions"][0]["actual_up"])

    def test_no_replay_on_same_snapshot(self):
        self.advance(451)
        self.assertEqual(self.advance(451)["status"], "NO_NEW_BARS")
        self.assertEqual(len(self.state()["observations"]),1)

    def test_backfilled_history_never_becomes_forward(self):
        self.snapshot(454)
        now = (pd.Timestamp(self.all_quotes.iloc[453]["time"]) +
               pd.Timedelta(hours=1, minutes=5)).to_pydatetime()
        result=observe(quotes_path=self.q,provenance_path=self.m,out=self.out,
                       allow_synthetic=True,_test_asof=now)
        self.assertEqual(result["new_bars"],4)
        self.assertEqual(result["late_unqualified_bars"],3)
        self.assertEqual(result["new_time_qualified_predictions"],1)
        self.assertEqual(len(self.state()["observations"]),4)

    def test_delayed_single_bar_is_never_a_signal(self):
        r=self.advance(451,lag_minutes=36)
        self.assertEqual(r["late_unqualified_bars"],1)
        self.assertEqual(r["new_time_qualified_predictions"],0)

    def test_future_outcome_not_scored_in_advance(self):
        self.advance(451)
        self.advance(452)
        self.assertIsNone(self.state()["predictions"][0]["actual_up"])
        self.advance(453)
        self.assertIsInstance(self.state()["predictions"][0]["actual_up"],bool)
        self.assertEqual(self.state()["predictions"][0]["signal_index"],450)

    def test_frozen_prefix_mutation_rejected(self):
        self.advance(451)
        df=self.all_quotes.iloc[:452].copy()
        for field in df.columns[1:]:df.loc[35,field]+=1
        self.snapshot(452,values=df)
        when=(pd.Timestamp(df.iloc[-1]["time"])+pd.Timedelta(hours=1,minutes=5)).to_pydatetime()
        with self.assertRaisesRegex(ValueError,"revised"):
            observe(quotes_path=self.q,provenance_path=self.m,out=self.out,
                    allow_synthetic=True,_test_asof=when)

    def test_state_tampering_rejected(self):
        s=self.state();s["last_seen_rows"]=500
        write_json(self.out/"monitor_state.json",s)
        with self.assertRaisesRegex(ValueError,"integrity"):
            self.advance(451)

    def test_source_change_rejected(self):
        m=json.loads(self.m.read_text());m["source_name"]="other quote feed"
        write_json(self.m,m)
        with self.assertRaisesRegex(ValueError,"provider/source/license"):
            observe(quotes_path=self.q,provenance_path=self.m,out=self.out,
                    allow_synthetic=True,_test_asof=self.asof)

    def test_actual_clock_cannot_be_overridden(self):
        with self.assertRaisesRegex(ValueError,"synthetic tests only"):
            _utc_now(self.asof,synthetic=False)

    def test_reject_incomplete_bar(self):
        self.snapshot(451)
        when=(pd.Timestamp(self.all_quotes.iloc[450]["time"])+pd.Timedelta(minutes=30)).to_pydatetime()
        with self.assertRaisesRegex(ValueError,"not-yet-completed"):
            observe(quotes_path=self.q,provenance_path=self.m,out=self.out,
                    allow_synthetic=True,_test_asof=when)

    def test_pauses_after_drift_and_never_restarts(self):
        # Freeze a fresh journal with stringent, fixed thresholds: any non-perfect
        # Brier over 8 scored observations triggers manual review.
        extra=self.path/"drift"
        start(quotes_path=self.q,provenance_path=self.m,out=extra,
              cfg=RetrainConfig(min_training_bars=300,max_training_bars=400,
                                probability_threshold=.51),
              policy=MonitorPolicy(max_capture_lag_minutes=15,drift_window=8,
                                   minimum_scored=8,min_direction_accuracy=1.0,
                                   max_brier_score=0.00001),
              allow_synthetic=True,_test_asof=self.asof)
        result=None
        for count in range(451,466):
            self.snapshot(count)
            when=(pd.Timestamp(self.all_quotes.iloc[count-1]["time"])+
                  pd.Timedelta(hours=1,minutes=5)).to_pydatetime()
            result=observe(quotes_path=self.q,provenance_path=self.m,out=extra,
                           allow_synthetic=True,_test_asof=when)
        s=json.loads((extra/"monitor_state.json").read_text())
        self.assertEqual(result["monitor_status"],"PAUSED_MODEL_DRIFT_REVIEW_REQUIRED")
        self.assertEqual(len(s["alerts"]),1)
        self.assertLess(len(s["predictions"]),15)
        # The newly observed bar that triggered drift cannot produce a signal.
        self.assertEqual(s["predictions"][-1]["signal_index"], 458)
        self.assertFalse(result["approved_for_live_trading"])

    def test_reject_output_to_public_docs(self):
        with self.assertRaisesRegex(ValueError,"publish"):
            start(quotes_path=self.q,provenance_path=self.m,
                  out=Path(__file__).resolve().parents[1]/"docs"/"forward",
                  allow_synthetic=True,_test_asof=self.asof)

    def test_no_implicit_reset(self):
        with self.assertRaisesRegex(ValueError,"already exists"):
            self.initialise()

    def test_concurrent_observation_rejected(self):
        (self.out/".observe_lock").mkdir()
        with self.assertRaisesRegex(ValueError,"Another"):
            self.advance(451)


if __name__=="__main__": unittest.main()

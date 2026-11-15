"""Local-only operational supervisor safety tests; all quote data is synthetic."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from execution_lab import make_fixture, sha256_file, write_json
from forward_monitor import SCHEMA as MONITOR_SCHEMA, _persist, start
from forward_supervisor import (EVENTS, STATUS, _read_events, health, run_once)
from h1_ml_retrainer import RetrainConfig


class SupervisorUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "monitor"
        self.out.mkdir()
        self.now = datetime(2024, 3, 1, 12, 5, tzinfo=timezone.utc)
        state = {"schema": MONITOR_SCHEMA, "mode": "RESEARCH_ONLY_NO_ORDERS",
                 "monitor_status": "ACTIVE", "approved_for_live_trading": False,
                 "eligible_for_paper_promotion": False}
        _persist(self.out / "monitor_state.json", state)
        self.q = self.root / "quotes.csv"
        self.m = self.root / "provenance.json"
        self.q.write_text("synthetic-only\n", encoding="utf-8")
        self.m.write_text("{}", encoding="utf-8")

    def tick(self, status="NO_NEW_BARS", *, expect=False, stamp=None, error=None):
        def fake(**_):
            if error:
                raise error
            return {"status": status,
                    "monitor_status": "PAUSED_MODEL_DRIFT_REVIEW_REQUIRED" if status == "PAUSED" else "ACTIVE",
                    "new_bars": 0 if status == "NO_NEW_BARS" else 1,
                    "new_time_qualified_predictions": 0 if status == "NO_NEW_BARS" else 1}
        with patch("forward_supervisor.observe", side_effect=fake):
            return run_once(quotes_path=self.q, provenance_path=self.m, out=self.out,
                            allow_synthetic=True, expect_new_bar=expect,
                            _test_asof=stamp or self.now)

    def test_idempotent_polls_keep_audit_heartbeat(self):
        a = self.tick()
        b = self.tick(stamp=self.now + timedelta(minutes=4))
        self.assertEqual(a["status"], "IDLE_NO_NEW_H1")
        self.assertEqual(b["seq"], 2)
        log = _read_events(self.out)
        self.assertEqual(len(log), 2)
        self.assertEqual(log[1]["previous_event_sha256"], log[0]["event_sha256"])
        self.assertFalse(b["approved_for_live_trading"])

    def test_health_checks_process_not_quote_freshness(self):
        self.tick()
        h = health(out=self.out, _test_asof=self.now + timedelta(minutes=20))
        self.assertTrue(h["healthy"])
        self.assertFalse(h["market_data_freshness_confirmed"])
        expired = health(out=self.out, _test_asof=self.now + timedelta(minutes=91))
        self.assertEqual(expired["status"], "STALE_SUPERVISOR_HEARTBEAT")
        self.assertFalse(expired["healthy"])

    def test_expected_h1_missing_latches_blocked(self):
        r = self.tick(expect=True)
        self.assertEqual(r["status"], "BLOCKED_EXPECTED_H1_MISSING")
        self.assertFalse(health(out=self.out, _test_asof=self.now)["healthy"])
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.tick(status="NEW", stamp=self.now + timedelta(hours=1))

    def test_drift_pauses_and_never_resumes(self):
        r = self.tick(status="PAUSED")
        self.assertEqual(r["status"], "BLOCKED_MODEL_DRIFT_REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.tick(status="NEW", stamp=self.now + timedelta(hours=1))

    def test_error_records_class_not_message_and_blocks(self):
        r = self.tick(error=RuntimeError("private token should not appear"))
        self.assertEqual(r["status"], "BLOCKED_OPERATIONAL_ERROR")
        log = (self.out / EVENTS).read_text(encoding="utf-8")
        self.assertIn('"error_type":"RuntimeError"', log)
        self.assertNotIn("private token", log)
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.tick()

    def test_hash_chain_detects_mutation(self):
        self.tick()
        file = self.out / EVENTS
        content = file.read_text(encoding="utf-8")
        file.write_text(content.replace("IDLE_NO_NEW_H1", "OBSERVED_SYNTHETIC_TEST_ONLY"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash chain"):
            health(out=self.out, _test_asof=self.now)

    def test_status_mismatch_blocks_continuation(self):
        self.tick()
        path = self.out / STATUS
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["seq"] = 999
        write_json(path, doc)
        with self.assertRaisesRegex(ValueError, "status and journal"):
            self.tick()

    def test_monitor_mutation_outside_runner_detected(self):
        self.tick()
        state = json.loads((self.out / "monitor_state.json").read_text(encoding="utf-8"))
        state.pop("state_sha256")
        state["monitor_status"] = "PAUSED_MODEL_DRIFT_REVIEW_REQUIRED"
        _persist(self.out / "monitor_state.json", state)
        with self.assertRaisesRegex(ValueError, "differs"):
            health(out=self.out, _test_asof=self.now)
        with self.assertRaisesRegex(ValueError, "outside supervised"):
            self.tick()

    def test_backwards_clock_refused(self):
        self.tick()
        with self.assertRaisesRegex(ValueError, "moved backwards"):
            self.tick(stamp=self.now - timedelta(minutes=1))

    def test_missing_poll_and_init_are_not_mistaken_for_healthy(self):
        h = health(out=self.out, _test_asof=self.now)
        self.assertEqual(h["status"], "NOT_YET_POLLED")
        self.assertFalse(h["healthy"])
        h2 = health(out=self.root/"absent", _test_asof=self.now)
        self.assertEqual(h2["status"], "NOT_INITIALIZED")

    def test_concurrent_lock_rejected(self):
        (self.out / ".supervisor_lock").mkdir()
        with self.assertRaisesRegex(ValueError, "already active"):
            self.tick()
        self.assertEqual(health(out=self.out)["status"], "SUPERVISOR_RUNNING_OR_STALE_LOCK")

    def test_public_output_refused(self):
        project = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "publish"):
            run_once(quotes_path=self.q, provenance_path=self.m, out=project/"docs"/"forward",
                     allow_synthetic=True, _test_asof=self.now)

    def test_preexisting_journal_without_status_fails_closed(self):
        self.tick()
        (self.out / STATUS).unlink()
        with self.assertRaisesRegex(ValueError, "status is absent"):
            self.tick()


class SupervisorIntegrationTests(unittest.TestCase):
    def test_genuine_frozen_monitor_pipeline_synthetic_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            quotes, provenance = make_fixture(root / "source", n=580, seed=35)
            complete = pd.read_csv(quotes)
            path = root / "progress.csv"
            meta = json.loads(provenance.read_text(encoding="utf-8"))
            mp = root / "progress_provenance.json"

            def snapshot(n):
                complete.iloc[:n].to_csv(path, index=False, float_format="%.6f")
                new = dict(meta)
                new["sha256"] = sha256_file(path)
                write_json(mp, new)

            snapshot(450)
            asof = (pd.Timestamp(complete.iloc[449]["time"]) + pd.Timedelta(hours=1)).to_pydatetime()
            directory = root / "ops"
            start(quotes_path=path, provenance_path=mp, out=directory,
                  cfg=RetrainConfig(min_training_bars=300, max_training_bars=400, probability_threshold=.51),
                  allow_synthetic=True, _test_asof=asof)
            snapshot(451)
            next_time = (pd.Timestamp(complete.iloc[450]["time"]) + pd.Timedelta(hours=1, minutes=5)).to_pydatetime()
            r = run_once(quotes_path=path, provenance_path=mp, out=directory,
                         allow_synthetic=True, _test_asof=next_time)
            self.assertEqual(r["status"], "OBSERVED_SYNTHETIC_TEST_ONLY")
            self.assertEqual(r["new_bars"], 1)
            self.assertEqual(r["new_predictions"], 1)
            state = json.loads((directory/"monitor_state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["predictions"]), 1)
            self.assertIsNone(state["predictions"][0]["actual_up"])
            self.assertTrue(health(out=directory, _test_asof=next_time)["healthy"])


if __name__ == "__main__":
    unittest.main()

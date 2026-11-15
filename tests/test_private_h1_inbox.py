"""Local-only fixture tests for atomic licensed-data inbox; never touch a broker."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from execution_lab import make_fixture, sha256_file, write_json
from forward_monitor import start
from h1_ml_retrainer import RetrainConfig
from private_h1_inbox import publish, poll, QUOTES, PROVENANCE, READY, SCHEMA


class InputFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        root = Path(cls.shared.name)
        q, m = make_fixture(root / "all", n=540, seed=35)
        cls.complete = pd.read_csv(q)
        cls.meta = json.loads(m.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.q, self.p = self.root / "source.csv", self.root / "source.json"
        self.inbox = self.root / "private_inbox"
        self.monitor = self.root / "monitor"
        self.snapshot(450)
        self.asof = (pd.Timestamp(self.complete.iloc[449]["time"]) +
                     pd.Timedelta(hours=1, minutes=5)).to_pydatetime()

    def snapshot(self, n, *, altered=False):
        df = self.complete.iloc[:n].copy()
        if altered:
            for col in df.columns[1:]:
                df.loc[200, col] += 1
        df.to_csv(self.q, index=False, float_format="%.6f")
        meta = dict(self.meta)
        meta["sha256"] = sha256_file(self.q)
        write_json(self.p, meta)

    def pub(self, **kwargs):
        return publish(quotes_path=self.q, provenance_path=self.p, inbox=self.inbox,
                       allow_synthetic=True, _test_asof=self.asof, **kwargs)

    def mock_monitor(self):
        self.monitor.mkdir(exist_ok=True)
        (self.monitor / "monitor_state.json").write_text("{}")

    def fake_poll(self, **kwargs):
        with patch("private_h1_inbox.run_once", return_value={"status": "OBSERVED_SYNTHETIC_TEST_ONLY"}) as run, \
             patch("private_h1_inbox.write_export", return_value={"monitor_status": "ACTIVE"}) as exp:
            result = poll(inbox=self.inbox, out=self.monitor, allow_synthetic=True,
                          _test_asof=self.asof, **kwargs)
        return result, run, exp


class PublicationTests(InputFixture):
    def test_publish_creates_ready_with_matching_digest(self):
        result = self.pub()
        self.assertEqual(result["status"], "READY_PRIVATE_SNAPSHOT")
        folder = self.inbox / result["snapshot_id"]
        self.assertEqual({x.name for x in folder.iterdir()}, {QUOTES, PROVENANCE, READY})
        marker = json.loads((folder / READY).read_text())
        self.assertEqual(marker["schema"], SCHEMA)
        self.assertEqual(marker["quotes_sha256"], sha256_file(self.q))
        self.assertFalse(marker["approved_for_live_trading"])
        self.assertFalse(marker["external_timestamp_attested"])
        self.assertEqual(list(self.inbox.glob(".pending-*")), [])

    def test_identical_publish_is_idempotent(self):
        first = self.pub()
        again = self.pub()
        self.assertEqual(again["status"], "ALREADY_PUBLISHED")
        self.assertEqual(again["snapshot_id"], first["snapshot_id"])

    def test_price_hash_mismatch_rejected(self):
        meta = json.loads(self.p.read_text())
        meta["sha256"] = "0" * 64
        write_json(self.p, meta)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.pub()
        self.assertFalse(any(self.inbox.glob("[0-9a-f]*")))

    def test_uncompleted_last_bar_rejected(self):
        too_early = pd.Timestamp(self.complete.iloc[449]["time"]).to_pydatetime()
        with self.assertRaisesRegex(ValueError, "not yet completed"):
            publish(quotes_path=self.q, provenance_path=self.p, inbox=self.inbox,
                    allow_synthetic=True, _test_asof=too_early)

    def test_input_symlink_rejected(self):
        alias = self.root / "source-link.csv"
        alias.symlink_to(self.q)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            publish(quotes_path=alias, provenance_path=self.p, inbox=self.inbox,
                    allow_synthetic=True, _test_asof=self.asof)

    def test_developer_cannot_fake_real_data_time(self):
        data = json.loads(self.p.read_text())
        data["source_kind"] = "BROKER_BID_ASK_H1"  # test-only claim, NOT real broker data
        write_json(self.p, data)
        with self.assertRaisesRegex(ValueError, "Clock override"):
            publish(quotes_path=self.q, provenance_path=self.p, inbox=self.inbox,
                    allow_synthetic=True, _test_asof=self.asof)

    def test_reject_publish_to_public_site(self):
        with self.assertRaisesRegex(ValueError, "public"):
            publish(quotes_path=self.q, provenance_path=self.p,
                    inbox=self.root / "docs" / "inbox", allow_synthetic=True,
                    _test_asof=self.asof)

    def test_private_folder_permissions(self):
        self.pub()
        self.assertEqual(self.inbox.stat().st_mode & 0o077, 0)


class PollTests(InputFixture):
    def test_no_ready_is_not_market_health(self):
        self.mock_monitor()
        self.inbox.mkdir()
        result, run, exp = self.fake_poll()
        self.assertEqual(result["status"], "NO_READY_SNAPSHOT")
        self.assertFalse(result["market_data_freshness_confirmed"])
        run.assert_not_called()
        exp.assert_not_called()

    def test_consume_moves_complete_snapshot_after_export(self):
        first = self.pub()
        self.mock_monitor()
        result, run, exp = self.fake_poll()
        self.assertEqual(result["status"], "PRIVATE_SNAPSHOT_PROCESSED")
        self.assertEqual(result["queue_depth_remaining"], 0)
        self.assertTrue((self.inbox / "processed" / first["snapshot_id"] / READY).is_file())
        self.assertFalse((self.inbox / first["snapshot_id"]).exists())
        self.assertTrue(run.called)
        self.assertTrue(exp.called)
        idle, run, _ = self.fake_poll()
        self.assertEqual(idle["status"], "NO_READY_SNAPSHOT")
        run.assert_not_called()

    def test_corrupt_quotes_refused_before_supervisor_runs(self):
        first = self.pub()
        folder = self.inbox / first["snapshot_id"]
        (folder / QUOTES).write_text("corrupted")
        self.mock_monitor()
        with patch("private_h1_inbox.run_once") as run:
            with self.assertRaises(ValueError):
                self.fake_poll()
            run.assert_not_called()
        self.assertTrue(folder.exists())

    def test_missing_ready_marker_not_observed(self):
        first = self.pub()
        (self.inbox / first["snapshot_id"] / READY).unlink()
        self.mock_monitor()
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.fake_poll()

    def test_blocked_monitor_does_not_ack_snapshot(self):
        first = self.pub()
        self.mock_monitor()
        with patch("private_h1_inbox.run_once", return_value={"status": "BLOCKED_OPERATIONAL_ERROR"}), \
             patch("private_h1_inbox.write_export") as exp:
            result = poll(inbox=self.inbox, out=self.monitor, allow_synthetic=True,
                          _test_asof=self.asof)
        self.assertEqual(result["status"], "BLOCKED_OPERATIONAL_ERROR")
        self.assertTrue((self.inbox / first["snapshot_id"]).is_dir())
        exp.assert_not_called()

    def test_conflicting_same_endpoint_rejected(self):
        first = self.pub()
        first_folder = self.inbox / first["snapshot_id"]
        self.snapshot(450, altered=True)
        second = self.pub()
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.mock_monitor()
        with self.assertRaisesRegex(ValueError, "incompatible"):
            self.fake_poll()
        self.assertTrue(first_folder.exists())

    def test_publishing_lock_protects_concurrent_writer(self):
        self.inbox.mkdir(mode=0o700)
        (self.inbox / ".publish_lock").mkdir()
        with self.assertRaisesRegex(ValueError, "publisher"):
            self.pub()

    def test_consumer_requires_manual_monitor_init(self):
        self.pub()
        with self.assertRaisesRegex(ValueError, "previously frozen"):
            self.fake_poll()

    def test_export_failure_leaves_snapshot_unacknowledged(self):
        first = self.pub()
        self.mock_monitor()
        with patch("private_h1_inbox.run_once", return_value={"status": "OBSERVED_SYNTHETIC_TEST_ONLY"}), \
             patch("private_h1_inbox.write_export", side_effect=ValueError("failed")):
            with self.assertRaisesRegex(ValueError, "failed"):
                poll(inbox=self.inbox, out=self.monitor, allow_synthetic=True, _test_asof=self.asof)
        self.assertTrue((self.inbox / first["snapshot_id"]).exists())

    def test_real_supervisor_and_dashboard_bridge_on_fixture(self):
        # No mocks: frozen model + first qualified hour + operational journal + export.
        self.snapshot(450)
        before = (pd.Timestamp(self.complete.iloc[449]["time"]) + pd.Timedelta(hours=1)).to_pydatetime()
        start(quotes_path=self.q, provenance_path=self.p, out=self.monitor,
              cfg=RetrainConfig(min_training_bars=300,max_training_bars=400,probability_threshold=.51),
              allow_synthetic=True, _test_asof=before)
        self.snapshot(451)
        after = (pd.Timestamp(self.complete.iloc[450]["time"]) +
                 pd.Timedelta(hours=1, minutes=5)).to_pydatetime()
        pushed = publish(quotes_path=self.q, provenance_path=self.p, inbox=self.inbox,
                         allow_synthetic=True, _test_asof=after)
        actual = poll(inbox=self.inbox, out=self.monitor, allow_synthetic=True,
                      _test_asof=after)
        self.assertEqual(actual["status"], "PRIVATE_SNAPSHOT_PROCESSED")
        self.assertEqual(actual["supervisor_status"], "OBSERVED_SYNTHETIC_TEST_ONLY")
        self.assertTrue((self.inbox / "processed" / pushed["snapshot_id"]).exists())
        export = json.loads((self.monitor / "forward_status.json").read_text())
        self.assertEqual(export["metrics"]["overall"]["predictions"], 1)
        self.assertEqual(export["metrics"]["overall"]["pending"], 1)
        self.assertEqual(export["status"], "SYNTHETIC_TEST_ONLY")
        self.assertFalse(export["approved_for_live_trading"])


if __name__ == "__main__":
    unittest.main()

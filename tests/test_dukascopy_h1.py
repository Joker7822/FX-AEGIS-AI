"""All converter tests use locally generated non-market fixtures; zero network calls."""
import csv
import lzma
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dukascopy_h1 import convert_local, decode_daily_bi5, _bi5_day
from execution_lab import load_quotes


class DukascopyOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "raw" / "USDJPY"
        self.csv = self.base / "ticks.csv"
        day = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.rows = []
        for d in range(15):
            for h in range(24):
                when = day + timedelta(days=d, hours=h)
                bid = round(142 + .01 * (d*24+h), 3)
                ask = round(bid + .011, 3)
                self.rows.append((when, bid, ask))
        with self.csv.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(["time", "bid", "ask"])
            for t, b, a in self.rows:
                writer.writerow([t.isoformat().replace("+00:00", "Z"), b, a])
        for d in range(15):
            selected = self.rows[d*24:(d+1)*24]
            blob = b"".join(struct.pack(">IIIff", h*3_600_000, int(round(a*1000)),
                                         int(round(b*1000)), 3., 3.)
                            for h, (_, b, a) in enumerate(selected))
            folder = self.root / "2024" / "00"
            folder.mkdir(parents=True, exist_ok=True)
            (folder/f"{d+1:02d}_ticks.bi5").write_bytes(lzma.compress(blob, format=lzma.FORMAT_ALONE))

    def convert(self, source, kind, name="out", **kw):
        return convert_local(source, self.base/name, kind=kind, fixture=True,
                             license_scope="private synthetic test only", license_acknowledged=True, **kw)

    def test_csv_converts_into_bidask_h1_for_existing_engine(self):
        result = self.convert(self.csv, "tick-csv")
        self.assertEqual(result["h1_bars"], 360)
        self.assertEqual(result["source_ticks"], 360)
        self.assertEqual(result["status"], "SYNTHETIC_TEST_ONLY")
        q, meta, audit = load_quotes(Path(result["quotes"]), Path(result["provenance"]), allow_synthetic=True)
        self.assertEqual(len(q), 360)
        self.assertEqual(audit["gap_count"], 0)
        self.assertAlmostEqual(q.iloc[0]["bid_open"], 142.000)
        self.assertAlmostEqual(q.iloc[0]["ask_open"], 142.011)
        self.assertNotIn("OANDA", meta["source_name"])
        self.assertFalse(meta["broker_origin_independently_confirmed"])
        with self.assertRaisesRegex(ValueError, "already|immutable"):
            self.convert(self.csv, "tick-csv")

    def test_offline_daily_bi5_decodes_to_same_ohlc(self):
        a = self.convert(self.csv, "tick-csv", name="csv")
        b = self.convert(self.root, "bi5-daily", name="bi5")
        self.assertEqual(b["input_files"], 15)
        q1, _, _ = load_quotes(Path(a["quotes"]), Path(a["provenance"]), allow_synthetic=True)
        q2, _, _ = load_quotes(Path(b["quotes"]), Path(b["provenance"]), allow_synthetic=True)
        self.assertTrue(q1.equals(q2))

    def test_cutoff_excludes_incomplete_bars(self):
        cutoff = datetime(2024, 1, 14, tzinfo=timezone.utc)
        result = self.convert(self.root, "bi5-daily", completed_through=cutoff)
        self.assertEqual(result["h1_bars"], 13*24)
        self.assertEqual(result["excluded_incomplete_ticks"], 2*24)

    def test_missing_timezone_and_reverse_order_rejected(self):
        text = self.csv.read_text(encoding="utf-8")
        self.csv.write_text(text.replace("2024-01-01T00:00:00Z", "2024-01-01T00:00:00"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.convert(self.csv, "tick-csv", name="bad-tz")

    def test_invalid_spread_and_legacy_timebase_rejected(self):
        p = self.root / "2024" / "00" / "01_ticks.bi5"
        blob = struct.pack(">IIIff", 86_400_000, 142011, 142000, 1., 1.)
        p.write_bytes(lzma.compress(blob, format=lzma.FORMAT_ALONE))
        with self.assertRaisesRegex(ValueError, "daily UTC boundary"):
            self.convert(self.root, "bi5-daily", name="bad-time")
        self.assertEqual(_bi5_day(p, self.root).date().isoformat(), "2024-01-01")

    def test_no_rights_or_unknown_layout_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Acknowledged"):
            convert_local(self.csv, self.base/"rights", kind="tick-csv", fixture=True,
                          license_scope="private", license_acknowledged=False)
        with self.assertRaisesRegex(ValueError, "local USDJPY instrument directory"):
            self.convert(self.root/"2024"/"00"/"01_ticks.bi5", "bi5-daily", name="bad-layout")

    def test_reject_publish_to_docs(self):
        project = Path(__file__).resolve().parents[1]
        with self.assertRaises(ValueError):
            convert_local(self.csv, project/"docs"/"provider", kind="tick-csv", fixture=True,
                          license_scope="private", license_acknowledged=True)


if __name__ == "__main__":
    unittest.main()

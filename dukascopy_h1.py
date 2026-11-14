"""Offline Dukascopy USDJPY tick archive -> private H1 Bid/Ask research dataset.

NO network access, no AWS Requester Pays operations, no account authentication.
Input is a user-supplied local DAILY .bi5 archive (new layout), or normalized
UTC tick CSV. Legacy hourly .bi5 is rejected, not silently mis-decoded.
Never publish the licensed data or claim source authenticity from a hash alone.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import lzma
import math
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from broker_h1_oanda import _private_destination
from execution_lab import COLUMNS, SCHEMA, load_quotes, sha256_file, write_json

SOURCE = "https://wiki.dukascopy.com/en/development/data-export/"
DAYFILE = re.compile(r"^(\d{4})/(0[0-9]|1[01])/([0-3]\d)_ticks\.bi5$")
RECORD = struct.Struct(">IIIff")
MAX_COMPRESSED_BYTES = 48 * 1024 * 1024  # per day
MAX_UNCOMPRESSED_BYTES = 220 * 1024 * 1024
MAX_SPREAD_PIPS = 50.0
POINT_VALUE_USDJPY = 1000.0


def _file_hash_chain(paths: list[Path], base: Path) -> str:
    """Audit list contents and relative names in a deterministic order."""
    h = hashlib.sha256()
    for path in paths:
        h.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0")
        h.update(bytes.fromhex(sha256_file(path)))
    return h.hexdigest()


def _bi5_day(path: Path, root: Path) -> datetime:
    relative = path.relative_to(root).as_posix()
    m = DAYFILE.fullmatch(relative)
    if not m:
        raise ValueError(f"Unsupported .bi5 path/layout: {relative}; requires YYYY/MM/DD_ticks.bi5 with zero-based month")
    year, month0, day = map(int, m.groups())
    return datetime(year, month0 + 1, day, tzinfo=timezone.utc)


def decode_daily_bi5(path: Path, day_start: datetime) -> Iterator[tuple[datetime, float, float]]:
    """Decode official newer *daily* .bi5 format; not compatible with legacy hourly data."""
    if day_start.tzinfo is None or day_start.utcoffset() != timedelta(0):
        raise ValueError("day_start must be UTC-aware")
    if path.stat().st_size > MAX_COMPRESSED_BYTES:
        raise ValueError("Compressed daily input exceeds safety cap")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
    blob = decoder.decompress(path.read_bytes(), max_length=MAX_UNCOMPRESSED_BYTES + 1)
    if len(blob) > MAX_UNCOMPRESSED_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError("Unexpected/unbounded .bi5 data; possibly unsupported format")
    if len(blob) == 0 or len(blob) % RECORD.size:
        raise ValueError(".bi5 record size is not a multiple of 20 bytes")
    previous = -1
    for ms, raw_ask, raw_bid, ask_vol, bid_vol in RECORD.iter_unpack(blob):
        if ms >= 86_400_000 or ms < previous:
            raise ValueError(".bi5 timestamp exceeds daily UTC boundary or is out of order; legacy layout suspected")
        if not (math.isfinite(ask_vol) and math.isfinite(bid_vol) and ask_vol >= 0 and bid_vol >= 0):
            raise ValueError("Invalid .bi5 tick volume")
        ask, bid = raw_ask / POINT_VALUE_USDJPY, raw_bid / POINT_VALUE_USDJPY
        _verify_quote(bid, ask)
        yield day_start + timedelta(milliseconds=ms), bid, ask
        previous = ms


def _verify_quote(bid: float, ask: float) -> None:
    if (not math.isfinite(bid) or not math.isfinite(ask) or
            bid <= 0 or ask < bid or (ask - bid) > MAX_SPREAD_PIPS * .01 + 1e-9):
        raise ValueError("Invalid tick Bid/Ask or spread over 50 pips")


def _utc_tick(value: str) -> datetime:
    if not (value.endswith("Z") or (len(value) > 6 and value[-6] in "+-" and value[-3] == ":")):
        raise ValueError("Tick CSV times must have explicit timezone offset (UTC recommended)")
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("Naive tick timestamp")
    return t.astimezone(timezone.utc)


def read_tick_csv(path: Path) -> Iterator[tuple[datetime, float, float]]:
    """Read normalized explicit-timezone CSV with exact `time,bid,ask` columns."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["time", "bid", "ask"]:
            raise ValueError("Normalized CSV requires exact columns: time,bid,ask")
        previous: datetime | None = None
        for r in reader:
            t = _utc_tick(r["time"])
            bid, ask = float(r["bid"]), float(r["ask"])
            _verify_quote(bid, ask)
            if previous is not None and t < previous:
                raise ValueError("Tick timestamps are not monotonically increasing")
            previous = t
            yield t, bid, ask


class H1Accumulator:
    """Streaming H1 OHLC aggregation, never fills gaps or invents ticks."""
    def __init__(self, completed_through: datetime):
        if completed_through.tzinfo is None or completed_through.utcoffset() != timedelta(0):
            raise ValueError("completed_through must be UTC")
        if completed_through.minute or completed_through.second or completed_through.microsecond:
            raise ValueError("completed_through must be aligned to an H1 boundary")
        self.limit = completed_through
        self.rows: list[dict] = []
        self.current: dict | None = None
        self.last_tick: datetime | None = None
        self.ticks = 0
        self.excluded_incomplete_ticks = 0

    def add(self, time_utc: datetime, bid: float, ask: float) -> None:
        _verify_quote(bid, ask)
        if time_utc.utcoffset() != timedelta(0):
            raise ValueError("All tick timestamps must be normalized to UTC")
        if self.last_tick is not None and time_utc < self.last_tick:
            raise ValueError("Source ticks are not chronologically ordered")
        self.last_tick = time_utc
        if time_utc >= self.limit:
            self.excluded_incomplete_ticks += 1
            return
        hour = time_utc.replace(minute=0, second=0, microsecond=0)
        if self.current is None or self.current["time"] != hour.strftime("%Y-%m-%dT%H:%M:%SZ"):
            if self.current is not None:
                self.rows.append(self.current)
            self.current = {"time": hour.strftime("%Y-%m-%dT%H:%M:%SZ")}
            for name, value in (("bid", bid), ("ask", ask)):
                for kind in ("open", "high", "low", "close"):
                    self.current[f"{name}_{kind}"] = value
        else:
            for name, value in (("bid", bid), ("ask", ask)):
                self.current[f"{name}_high"] = max(self.current[f"{name}_high"], value)
                self.current[f"{name}_low"] = min(self.current[f"{name}_low"], value)
                self.current[f"{name}_close"] = value
        self.ticks += 1

    def finish(self) -> list[dict]:
        if self.current is not None:
            self.rows.append(self.current)
            self.current = None
        return self.rows


def convert_local(source: Path, output: Path, *, kind: str, license_scope: str,
                  license_acknowledged: bool, fixture: bool = False,
                  completed_through: datetime | None = None) -> dict:
    """Offline, privacy-preserving conversion. Does not call a data service."""
    if not license_acknowledged or not license_scope.strip() or license_scope.strip().lower() == "public":
        raise ValueError("Acknowledged private research/data usage rights are mandatory")
    output = _private_destination(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing to replace an existing/immutable private data snapshot")
    boundary = completed_through or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    collector = H1Accumulator(boundary)
    if kind == "bi5-daily":
        if not source.is_dir():
            raise ValueError(".bi5 input must be a local USDJPY instrument directory")
        files = sorted(source.glob("*/*/*_ticks.bi5"))
        if not files:
            raise ValueError("No daily .bi5 files found; no download has been attempted")
        for path in files:
            day = _bi5_day(path, source)
            for tick in decode_daily_bi5(path, day):
                collector.add(*tick)
        input_digest = _file_hash_chain(files, source)
    elif kind == "tick-csv":
        if not source.is_file():
            raise ValueError("CSV source must be a local file")
        files = [source]
        for tick in read_tick_csv(source):
            collector.add(*tick)
        input_digest = sha256_file(source)
    else:
        raise ValueError("Only local bi5-daily and tick-csv inputs are supported")
    rows = collector.finish()
    if len(rows) < 200:
        raise ValueError("Need at least 200 completed H1 bars; increase date span, do not synthesize missing bars")
    # Persist only AFTER all source ticks passed validation; use separate temp names.
    output.mkdir(parents=True, exist_ok=True)
    qfile, mfile = output / "bidask_h1.csv", output / "provenance.json"
    tmp_q, tmp_m = output / ".pending_bidask.csv", output / ".pending_provenance.json"
    try:
        with tmp_q.open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] if k == "time" else f"{row[k]:.6f}" for k in COLUMNS})
        meta = {
            "schema": SCHEMA, "source_kind": "SYNTHETIC_TEST_FIXTURE" if fixture else "BROKER_BID_ASK_H1",
            "source_name": "Synthetic Dukascopy-layout fixture" if fixture else "Dukascopy user-supplied local ticks",
            "symbol": "USDJPY", "timestamp_semantics": "BAR_OPEN", "account_currency": "JPY",
            "quote_timezone": "UTC", "sha256": sha256_file(tmp_q), "license_scope": license_scope,
            "provider_documentation": SOURCE, "source_format": kind,
            "source_files": len(files), "input_sha256": input_digest,
            "completed_through_exclusive_utc": boundary.isoformat(),
            "source_tick_count": collector.ticks,
            "incomplete_tick_count_excluded": collector.excluded_incomplete_ticks,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "no_network_access": True, "broker_origin_independently_confirmed": False,
            "note": "User-supplied quotes are unverified and not guaranteed executable; redistribution not authorized by this converter.",
        }
        write_json(tmp_m, meta)
        _, _, audit = load_quotes(tmp_q, tmp_m, allow_synthetic=fixture)
        tmp_q.replace(qfile)
        tmp_m.replace(mfile)
    finally:
        tmp_q.unlink(missing_ok=True)
        tmp_m.unlink(missing_ok=True)
    return {"status": "SYNTHETIC_TEST_ONLY" if fixture else "USER_SUPPLIED_PRIVATE_QUOTES_NOT_INDEPENDENTLY_VERIFIED",
            "input_files": len(files), "source_ticks": collector.ticks, "h1_bars": len(rows),
            "excluded_incomplete_ticks": collector.excluded_incomplete_ticks,
            "quote_sha256": meta["sha256"], "quotes": str(qfile), "provenance": str(mfile),
            "audit": audit, "approved_for_live_trading": False}


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline Dukascopy tick-to-H1 converter. No download/network or order APIs.")
    ap.add_argument("--input", type=Path, required=True, help="Local YYYY/MM/DD_ticks.bi5 directory or UTC tick CSV")
    ap.add_argument("--input-format", choices=["bi5-daily", "tick-csv"], required=True)
    ap.add_argument("--out", type=Path, default=Path("private_quotes/dukascopy_h1_snapshot"))
    ap.add_argument("--license-scope", required=True)
    ap.add_argument("--license-acknowledged", action="store_true")
    ap.add_argument("--completed-through-utc", help="Exclusive UTC completed-hour cutoff, ISO 8601 with explicit Z")
    args = ap.parse_args()
    cutoff = _utc_tick(args.completed_through_utc) if args.completed_through_utc else None
    res = convert_local(args.input, args.out, kind=args.input_format,
                        license_scope=args.license_scope,
                        license_acknowledged=args.license_acknowledged,
                        completed_through=cutoff)
    print(json.dumps({k: v for k, v in res.items() if k != "audit"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

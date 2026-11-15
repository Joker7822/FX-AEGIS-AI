"""Local-only operational supervisor for frozen-model H1 forward research.

Reads an already-updated private quote snapshot; NEVER downloads quotes, reads
credentials, schedules itself, places trades, or automatically re-freezes models.
A chained local event log is an accidental-tamper check, NOT third-party attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from forward_monitor import _private_out, _state_load, _utc_now, observe

SCHEMA = "fx-aegis-forward-supervisor-v1"
EVENTS = "operations_events.jsonl"
STATUS = "operations_status.json"
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
BLOCKED = frozenset({
    "BLOCKED_EXPECTED_H1_MISSING",
    "BLOCKED_MODEL_DRIFT_REVIEW_REQUIRED",
    "BLOCKED_OPERATIONAL_ERROR",
})


def _digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _read_events(directory: Path) -> list[dict]:
    path = directory / EVENTS
    if not path.exists():
        return []
    if path.stat().st_size > MAX_JOURNAL_BYTES:
        raise ValueError("Operations journal exceeds bounded size; requires offline archival review")
    events = []
    prev = "0" * 64
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n"):
                raise ValueError("Truncated operational event; manual review required")
            try:
                item = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Unreadable operations journal") from exc
            if (not isinstance(item, dict) or item.get("schema") != SCHEMA
                    or type(item.get("seq")) is not int or item["seq"] != len(events) + 1
                    or item.get("previous_event_sha256") != prev):
                raise ValueError("Broken operational hash chain or sequence")
            original = item.get("event_sha256")
            fields = {k: v for k, v in item.items() if k != "event_sha256"}
            if original != _digest(fields):
                raise ValueError("Modified operational hash chain")
            if events and item["observed_at_utc"] < events[-1]["observed_at_utc"]:
                raise ValueError("Operational clock order moved backwards")
            prev = original
            events.append(item)
    return events


def _status_consistent(directory: Path, events: list[dict]) -> None:
    target = directory / STATUS
    if not events:
        if target.exists():
            raise ValueError("Operational status exists without journal; manual review required")
        return
    if not target.is_file():
        raise ValueError("Operational status is absent despite journal; manual review required")
    try:
        status = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Operational status is unreadable") from exc
    last = events[-1]
    if (status.get("schema") != SCHEMA or status.get("event_sha256") != last["event_sha256"]
            or status.get("seq") != last["seq"]
            or status.get("status") != last["status"]):
        raise ValueError("Operational status and journal disagree; manual review required")


def _read_monitor_hash(directory: Path) -> str:
    # Verify current monitor integrity before using its digest as evidence.
    path = directory / "monitor_state.json"
    state = _state_load(path)
    if (state.get("mode") != "RESEARCH_ONLY_NO_ORDERS"
            or state.get("approved_for_live_trading") is not False):
        raise ValueError("Monitor state allows unsafe operation")
    return _digest(state)


def _append(directory: Path, prior: list[dict], payload: dict) -> dict:
    last = prior[-1] if prior else None
    item = {"schema": SCHEMA, "seq": len(prior) + 1,
            "previous_event_sha256": last["event_sha256"] if last else "0" * 64,
            **payload}
    item["event_sha256"] = _digest(item)
    # os.O_APPEND ensures one complete newline-terminated write per event.
    # Local file systems only; concurrent writers are excluded by directory lock.
    raw = (json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")) + "\n").encode("utf-8")
    if (directory / EVENTS).exists() and (directory / EVENTS).stat().st_size + len(raw) > MAX_JOURNAL_BYTES:
        raise ValueError("Operational journal full; archival review required")
    fd = os.open(directory / EVENTS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise OSError("Partial event write")
        os.fsync(fd)
    finally:
        os.close(fd)
    view = {"schema": SCHEMA, "seq": item["seq"], "event_sha256": item["event_sha256"],
            "observed_at_utc": item["observed_at_utc"], "status": item["status"],
            "new_bars": item["new_bars"], "new_predictions": item["new_predictions"],
            "monitor_status": item["monitor_status"],
            "approved_for_live_trading": False, "order_placement": "DISABLED",
            "source_authenticity_independently_verified": False}
    fd, tmp = tempfile.mkstemp(prefix=".ops-status-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(view, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, directory / STATUS)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return view


def run_once(*, quotes_path: Path, provenance_path: Path, out: Path,
             allow_synthetic: bool = False, expect_new_bar: bool = False,
             _test_asof: datetime | None = None) -> dict:
    """Single manual/offline poll. No implicit init, resume, or model replacement."""
    directory = _private_out(Path(out))
    if not directory.is_dir():
        raise ValueError("Frozen monitor directory absent; initialize manually first")
    lock = directory / ".supervisor_lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("Supervisor already active or stale lock needs manual review") from exc
    try:
        events = _read_events(directory)
        _status_consistent(directory, events)
        prior_hash = _read_monitor_hash(directory)
        if events:
            if events[-1]["status"] in BLOCKED:
                raise ValueError("Supervisor is stopped; manual review and a new independent journal required")
            if events[-1]["monitor_state_sha256"] != prior_hash:
                raise ValueError("Monitor changed outside supervised journal")
        now = _utc_now(_test_asof, synthetic=allow_synthetic)
        if events and now.isoformat() < events[-1]["observed_at_utc"]:
            raise ValueError("Supervisor capture clock moved backwards")
        try:
            result = observe(quotes_path=Path(quotes_path), provenance_path=Path(provenance_path),
                             out=directory, allow_synthetic=allow_synthetic,
                             _test_asof=_test_asof)
            monitor_hash = _read_monitor_hash(directory)
            new_bars = int(result.get("new_bars", 0))
            new_predictions = int(result.get("new_time_qualified_predictions", 0))
            monitor_status = result["monitor_status"]
            if monitor_status == "PAUSED_MODEL_DRIFT_REVIEW_REQUIRED":
                status = "BLOCKED_MODEL_DRIFT_REVIEW_REQUIRED"
            elif result["status"] == "NO_NEW_BARS" and expect_new_bar:
                status = "BLOCKED_EXPECTED_H1_MISSING"
            elif result["status"] == "NO_NEW_BARS":
                status = "IDLE_NO_NEW_H1"
            else:
                status = ("OBSERVED_SYNTHETIC_TEST_ONLY" if result["status"] == "SYNTHETIC_TEST_ONLY"
                          else "OBSERVED_USER_SUPPLIED_QUOTES")
            problem = None
        except Exception as exc:
            # Error details may contain local filenames. Log only the exception class.
            # The frozen monitor cannot automatically resume after any failure.
            status = "BLOCKED_OPERATIONAL_ERROR"
            new_bars = new_predictions = 0
            problem = type(exc).__name__
            monitor_status = "UNKNOWN_CHECK_MANUALLY"
            try:
                monitor_hash = _read_monitor_hash(directory)
            except Exception:
                monitor_hash = "UNVERIFIED"
        payload = {"observed_at_utc": now.isoformat(), "status": status,
                   "new_bars": new_bars, "new_predictions": new_predictions,
                   "monitor_status": monitor_status, "monitor_state_sha256": monitor_hash,
                   "error_type": problem, "expected_new_bar": bool(expect_new_bar),
                   "allow_synthetic_test_flag": bool(allow_synthetic),
                   "order_placement": "DISABLED", "approved_for_live_trading": False,
                   "local_clock_not_independently_attested": True}
        summary = _append(directory, events, payload)
        return summary
    finally:
        lock.rmdir()


def health(*, out: Path, max_heartbeat_age_minutes: int = 90,
           _test_asof: datetime | None = None) -> dict:
    """Read-only. Distinguish process heartbeat from market-feed freshness."""
    if type(max_heartbeat_age_minutes) is not int or not 1 <= max_heartbeat_age_minutes <= 10080:
        raise ValueError("Invalid heartbeat interval")
    directory = _private_out(Path(out))
    if not directory.is_dir() or not (directory / "monitor_state.json").is_file():
        return {"status": "NOT_INITIALIZED", "healthy": False,
                "market_data_freshness_confirmed": False}
    if (directory / ".supervisor_lock").exists():
        return {"status": "SUPERVISOR_RUNNING_OR_STALE_LOCK", "healthy": False,
                "market_data_freshness_confirmed": False}
    events = _read_events(directory)
    _status_consistent(directory, events)
    state_hash = _read_monitor_hash(directory)
    if not events:
        return {"status": "NOT_YET_POLLED", "healthy": False,
                "market_data_freshness_confirmed": False}
    last = events[-1]
    if last["monitor_state_sha256"] != state_hash:
        raise ValueError("Monitor state differs from last supervised observation")
    now = _test_asof or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Health clock must be timezone aware")
    now = now.astimezone(timezone.utc)
    moment = datetime.fromisoformat(last["observed_at_utc"])
    age = (now - moment).total_seconds() / 60
    if age < 0:
        raise ValueError("Health clock moved backwards")
    if last["status"] in BLOCKED:
        status = last["status"]
    elif age > max_heartbeat_age_minutes:
        status = "STALE_SUPERVISOR_HEARTBEAT"
    else:
        status = last["status"]
    return {"schema": SCHEMA, "status": status,
            "healthy": status in ("IDLE_NO_NEW_H1", "OBSERVED_SYNTHETIC_TEST_ONLY", "OBSERVED_USER_SUPPLIED_QUOTES"),
            "heartbeat_age_minutes": round(age, 3),
            "last_event_sha256": last["event_sha256"],
            "event_count": len(events),
            "market_data_freshness_confirmed": False,
            "approved_for_live_trading": False, "order_placement": "DISABLED"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Local H1 forward research supervisor; no network or orders")
    sub = ap.add_subparsers(dest="cmd", required=True)
    tick = sub.add_parser("run-once")
    tick.add_argument("--quotes", type=Path, required=True)
    tick.add_argument("--provenance", type=Path, required=True)
    tick.add_argument("--out", type=Path, default=Path("private_reports/forward_monitor"))
    tick.add_argument("--allow-synthetic", action="store_true")
    tick.add_argument("--expect-new-bar", action="store_true",
                      help="Use only when a separate, verified market calendar expects an H1 candle")
    check = sub.add_parser("health")
    check.add_argument("--out", type=Path, default=Path("private_reports/forward_monitor"))
    check.add_argument("--max-heartbeat-age-minutes", type=int, default=90)
    args = ap.parse_args()
    try:
        result = (run_once(quotes_path=args.quotes, provenance_path=args.provenance,
                           out=args.out, allow_synthetic=args.allow_synthetic,
                           expect_new_bar=args.expect_new_bar)
                  if args.cmd == "run-once"
                  else health(out=args.out, max_heartbeat_age_minutes=args.max_heartbeat_age_minutes))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if (result.get("status") not in BLOCKED
                     and result.get("status") not in ("STALE_SUPERVISOR_HEARTBEAT", "NOT_INITIALIZED", "NOT_YET_POLLED", "SUPERVISOR_RUNNING_OR_STALE_LOCK")) else 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # Do not print local paths or broker data in logs.
        print(json.dumps({"schema": SCHEMA, "status": "FAILED_CLOSED_REVIEW_REQUIRED",
                          "error_type": type(exc).__name__, "approved_for_live_trading": False,
                          "order_placement": "DISABLED"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

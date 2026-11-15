"""Offline, private frozen-monitor -> local dashboard bridge.

There is deliberately no HTTP client, authentication, GitHub upload, data
scheduler, broker API, order routing, or auto model promotion in this module.
A locally supplied full H1 quote snapshot must be updated by the operator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forward_monitor import _locked_model_ok, _private_out, _state_load
from forward_supervisor import (
    BLOCKED, EVENTS, STATUS, _read_events, _read_monitor_hash,
    _status_consistent, run_once,
)

SCHEMA = "fx-aegis-private-forward-dashboard-v1"
MAX_PREDICTIONS_IN_REPORT = 720
MAX_PREDICTIONS_IN_JOURNAL = 100_000
HEX64 = frozenset("0123456789abcdef")


def _utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing monitor timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid monitor timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Naive monitor timestamp")
    return dt.astimezone(timezone.utc)


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in HEX64 for c in value)


def _metrics(predictions: list[dict]) -> dict:
    done = [p for p in predictions if p["actual_up"] is not None]
    pending = len(predictions) - len(done)
    if not done:
        return {"predictions": len(predictions), "scored": 0, "pending": pending,
                "accuracy": None, "brier": None}
    accuracy = sum((p["probability_up"] > 0.5) == p["actual_up"] for p in done) / len(done)
    brier = sum((p["probability_up"] - int(p["actual_up"])) ** 2 for p in done) / len(done)
    return {"predictions": len(predictions), "scored": len(done), "pending": pending,
            "accuracy": round(accuracy, 6), "brier": round(brier, 6)}


def _validated_pred(row: dict) -> dict:
    if not isinstance(row, dict):
        raise ValueError("Invalid prediction journal entry")
    if type(row.get("signal_index")) is not int or row["signal_index"] < 0:
        raise ValueError("Invalid prediction index")
    ts = _utc(row.get("signal_bar_open_utc"))
    at = _utc(row.get("predicted_at_utc"))
    if at < ts + timedelta(hours=1) or at >= ts + timedelta(hours=2):
        raise ValueError("Prediction was not made before the target event")
    proba = row.get("probability_up")
    if type(proba) not in (float, int) or not math.isfinite(proba) or not 0 <= proba <= 1:
        raise ValueError("Prediction probability out of bounds")
    actual = row.get("actual_up")
    if actual is not None and type(actual) is not bool:
        raise ValueError("Invalid forward outcome")
    side = row.get("side")
    if type(side) is not int or side not in (-1, 0, 1):
        raise ValueError("Invalid prediction direction")
    scored_at = row.get("scored_at_utc")
    if actual is None and scored_at is not None:
        raise ValueError("A pending prediction cannot have a score time")
    if actual is not None and (scored_at is None or _utc(scored_at) < ts + timedelta(hours=3)):
        raise ValueError("Outcome time is inconsistent")
    return {"signal_time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "predicted_at_utc": at.isoformat(),
            "probability_up": float(proba), "actual_up": actual,
            "scored_at_utc": _utc(scored_at).isoformat() if scored_at else None,
            "side": side, "signal_index": row["signal_index"]}


def build_report(directory: Path) -> dict:
    """Read and cross-validate three private journals; never reads market quotes."""
    root = _private_out(Path(directory))
    if not root.is_dir():
        raise ValueError("Monitor directory missing")
    if any((root / x).exists() for x in (".supervisor_lock", ".observe_lock")):
        raise ValueError("Monitor currently locked; do not export a partial state")
    events = _read_events(root)
    _status_consistent(root, events)
    if not events:
        raise ValueError("No supervised observation has completed")
    state = _state_load(root / "monitor_state.json")
    lock = json.loads((root / "immutable_model_lock.json").read_text(encoding="utf-8"))
    _locked_model_ok(lock)
    if state.get("frozen_lock_sha256") != lock.get("lock_sha256"):
        raise ValueError("Frozen lock and monitor state differ")
    if state.get("mode") != "RESEARCH_ONLY_NO_ORDERS" or state.get("approved_for_live_trading") is not False or state.get("eligible_for_paper_promotion") is not False:
        raise ValueError("Monitor is not research-only")
    last = events[-1]
    event_time = _utc(last.get("observed_at_utc"))
    state_time = _utc(state.get("last_observed_at_utc"))
    if (last.get("monitor_state_sha256") != _read_monitor_hash(root)
            or event_time < state_time
            or (last.get("status", "").startswith("OBSERVED_") and event_time != state_time)):
        raise ValueError("Operational event and monitor state disagree")
    if last.get("status") not in {"OBSERVED_SYNTHETIC_TEST_ONLY", "OBSERVED_USER_SUPPLIED_QUOTES", "IDLE_NO_NEW_H1"} | BLOCKED:
        raise ValueError("Unknown supervisor status")
    preds = state.get("predictions")
    if not isinstance(preds, list) or len(preds) > MAX_PREDICTIONS_IN_JOURNAL:
        raise ValueError("Invalid forward prediction count")
    validated = [_validated_pred(p) for p in preds]
    if any(b["signal_index"] <= a["signal_index"] or b["signal_time"] <= a["signal_time"]
           for a, b in zip(validated, validated[1:])):
        raise ValueError("Duplicate or reversed forward predictions")
    obs = state.get("observations")
    if not isinstance(obs, list):
        raise ValueError("Missing observations")
    alerts = state.get("alerts")
    if not isinstance(alerts, list) or len(alerts) > 100_000:
        raise ValueError("Missing or oversized alert history")
    display = validated[-MAX_PREDICTIONS_IN_REPORT:]
    sha = state.get("last_snapshot_quote_sha256")
    if sha is not None and not _hex64(sha):
        raise ValueError("Invalid price digest")
    source_kind = lock.get("source_kind")
    if source_kind not in ("SYNTHETIC_TEST_FIXTURE", "BROKER_BID_ASK_H1"):
        raise ValueError("Unsupported source kind")
    metrics = {"overall": _metrics(validated),
               "recent_24": _metrics(validated[-24:]),
               "recent_168": _metrics(validated[-168:])}
    return {
        "schema": SCHEMA, "status": "SYNTHETIC_TEST_ONLY" if source_kind == "SYNTHETIC_TEST_FIXTURE" else "UNVERIFIED_USER_SUPPLIED_QUOTES",
        "source_kind": source_kind, "monitor_status": state.get("monitor_status"),
        "supervisor_status": last["status"], "frozen_at_utc": _utc(lock["frozen_at_utc"]).isoformat(),
        "last_observed_at_utc": _utc(last["observed_at_utc"]).isoformat(),
        "quote_sha256": sha, "frozen_lock_sha256": lock["lock_sha256"],
        "last_event_sha256": last["event_sha256"], "operation_events": len(events),
        "observed_bars_since_freeze": len(obs),
        "on_time_bars": sum(x.get("on_time") is True for x in obs),
        "delayed_bars": sum(x.get("on_time") is False for x in obs),
        "alerts": len(alerts),
        "metrics": metrics, "predictions": display,
        "predictions_truncated_for_view": max(0, len(validated) - len(display)),
        "execution_assumption": "NO_BROKER_ORDERS_RESEARCH_ONLY",
        "approved_for_live_trading": False, "no_network_access": True,
        "source_authenticity_independently_verified": False,
        "timestamp_independently_attested": False,
        "note": "The browser reads this private export locally. It is not a published result, proof of source authenticity, or a calibrated trading forecast.",
    }


def write_export(directory: Path, destination: Path) -> dict:
    root = _private_out(Path(directory))
    path = _private_out(Path(destination))
    if root not in path.parents or path.name != "forward_status.json":
        raise ValueError("Export must be inside the monitor directory as forward_status.json")
    if path in (root / "monitor_state.json", root / STATUS, root / EVENTS):
        raise ValueError("Cannot replace monitor journal")
    report = build_report(root)
    payload = (json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(payload) > 1_000_000:
        raise ValueError("Forward dashboard export exceeds 1 MB")
    fd, tmp = tempfile.mkstemp(prefix=".forward-display-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"status": "PRIVATE_EXPORT_READY", "path": str(path),
            "scored": report["metrics"]["overall"]["scored"],
            "pending": report["metrics"]["overall"]["pending"],
            "monitor_status": report["monitor_status"], "approved_for_live_trading": False}


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _stable_inputs(paths: tuple[Path, Path]) -> tuple[str, str]:
    return tuple(_file_hash(p) for p in paths)


def main() -> int:
    ap = argparse.ArgumentParser(description="Local private H1 monitor + dashboard export; no network or trading")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("run-once", "export"):
        parser = sub.add_parser(cmd)
        parser.add_argument("--out", type=Path, default=Path("private_reports/forward_monitor"))
    tick = sub.choices["run-once"]
    tick.add_argument("--quotes", type=Path, required=True)
    tick.add_argument("--provenance", type=Path, required=True)
    tick.add_argument("--allow-synthetic", action="store_true", help="Fixtures only; never claim market data")
    tick.add_argument("--expect-new-bar", action="store_true", help="Only when an external calendar confirms H1 close")
    args = ap.parse_args()
    try:
        directory = _private_out(args.out)
        if args.cmd == "run-once":
            files = (args.quotes, args.provenance)
            before = _stable_inputs(files)
            status = run_once(quotes_path=args.quotes, provenance_path=args.provenance, out=directory,
                              allow_synthetic=args.allow_synthetic, expect_new_bar=args.expect_new_bar)
            if before != _stable_inputs(files):
                raise ValueError("Input snapshot changed during processing; manual review required")
            if status["status"] in BLOCKED:
                print(json.dumps({"status": status["status"], "approved_for_live_trading": False}))
                return 2
        result = write_export(directory, directory / "forward_status.json")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "FAILED_CLOSED_REVIEW_REQUIRED", "error_type": type(exc).__name__,
                          "approved_for_live_trading": False, "order_placement": "DISABLED"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

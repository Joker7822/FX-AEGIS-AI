"""Append-only, time-of-observation audited H1 *research* forward monitor.

NO brokerage access, trading orders, network, credential loading, or model promotion.
This is a LOCAL operational journal, not independently attested capture-time proof.
The model is frozen once; scoring never re-trains it on future observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from execution_lab import ExecutionConfig, load_quotes, write_json
from forward_paper import (
    SCHEMA as FREEZE_SCHEMA, _code_hash, _locked_training, _row_prefix_hash, _sha, freeze,
)
from h1_ml_retrainer import RetrainConfig, causal_design

SCHEMA = "fx-aegis-forward-monitor-v1"
PROJECT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class MonitorPolicy:
    max_capture_lag_minutes: int = 15
    drift_window: int = 24
    minimum_scored: int = 24
    min_direction_accuracy: float = 0.40
    max_brier_score: float = 0.32

    def check(self) -> None:
        if not (type(self.max_capture_lag_minutes) is int
                and 1 <= self.max_capture_lag_minutes <= 59
                and type(self.drift_window) is int and 8 <= self.drift_window <= 500
                and type(self.minimum_scored) is int and 8 <= self.minimum_scored <= self.drift_window
                and type(self.min_direction_accuracy) is float
                and math.isfinite(self.min_direction_accuracy)
                and 0.0 <= self.min_direction_accuracy <= 1.0
                and type(self.max_brier_score) is float
                and math.isfinite(self.max_brier_score)
                and 0.0 < self.max_brier_score <= 1.0):
            raise ValueError("Invalid immutable monitor policy")


def _private_out(path: Path) -> Path:
    dest = path.expanduser().resolve()
    if any(part in ("docs", ".github", ".git") for part in dest.parts):
        raise ValueError("Cannot publish private forward observations to GitHub or Pages")
    if dest == PROJECT or PROJECT in dest.parents:
        names = dest.relative_to(PROJECT).parts
        if not names or names[0] not in ("private_reports", "results"):
            raise ValueError("Inside project, forward journal must use gitignored output")
    return dest


def _utc_now(test_asof: datetime | None, *, synthetic: bool) -> datetime:
    if test_asof is not None and not synthetic:
        raise ValueError("Clock override is permitted for synthetic tests only")
    instant = test_asof if test_asof is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Capture clock must have a timezone")
    return instant.astimezone(timezone.utc)


def _atomic_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".forward-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _state_digest(body: dict) -> str:
    return _sha(body)


def _persist(path: Path, state: dict) -> None:
    content = dict(state)
    content["state_sha256"] = _state_digest(content)
    _atomic_json(path, content)


def _state_load(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("Monitor state is absent; initialise with init (never silently reset)")
    stored = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(stored, dict):
        raise ValueError("Invalid monitor journal")
    digest = stored.pop("state_sha256", None)
    if stored.get("schema") != SCHEMA or digest != _state_digest(stored):
        raise ValueError("Monitor journal integrity failed; no silent reset")
    return stored


def _locked_model_ok(record: dict) -> None:
    given = record.get("lock_sha256")
    body = {k: v for k, v in record.items() if k != "lock_sha256"}
    if (record.get("schema") != FREEZE_SCHEMA or given != _sha(body)
            or record.get("paper_only") is not True or record.get("approved_for_live_trading") is not False):
        raise ValueError("Frozen model lock integrity or policy failed")
    if (record.get("code_sha256") != _code_hash()
            or record.get("numpy_version") != np.__version__
            or record.get("pandas_version") != pd.__version__
            or record.get("sklearn_version") != sklearn.__version__):
        raise ValueError("Frozen model code/dependencies changed; independent re-freeze required")


def _source_ok(meta: dict, lock: dict) -> None:
    for field, frozen in (("source_kind", "source_kind"), ("source_name", "source_name"),
                          ("license_scope", "source_license_scope")):
        if meta.get(field) != lock.get(frozen):
            raise ValueError("Data provider/source/license changed since model freeze")


def start(*, quotes_path: Path, provenance_path: Path, out: Path,
          cfg: RetrainConfig = RetrainConfig(), execution: ExecutionConfig = ExecutionConfig(),
          policy: MonitorPolicy = MonitorPolicy(), allow_synthetic: bool = False,
          _test_asof: datetime | None = None) -> dict:
    """Freeze training prefix exactly once; new observations must arrive in later invocations."""
    cfg.check(); execution.check(); policy.check()
    output = _private_out(out)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Forward journal already exists; preserve locked history")
    quotes, meta, _ = load_quotes(Path(quotes_path), Path(provenance_path), allow_synthetic=allow_synthetic)
    now = _utc_now(_test_asof, synthetic=meta["source_kind"] == "SYNTHETIC_TEST_FIXTURE")
    if _test_asof is not None and not allow_synthetic:
        raise ValueError("Clock override forbidden for broker data")
    if quotes.iloc[-1]["time"].to_pydatetime() + timedelta(hours=1) > now:
        raise ValueError("The supplied training prefix includes an unfinished candle")
    output.mkdir(parents=True, exist_ok=True)
    lockfile = output / "immutable_model_lock.json"
    lock = freeze(Path(quotes_path), Path(provenance_path), lockfile,
                  cfg=cfg, execution=execution, allow_synthetic=allow_synthetic,
                  _test_asof=now if allow_synthetic else None)
    state = {"schema": SCHEMA, "mode": "RESEARCH_ONLY_NO_ORDERS",
             "monitor_status": "ACTIVE", "created_at_utc": now.isoformat(),
             "last_observed_at_utc": now.isoformat(),
             "frozen_lock_sha256": lock["lock_sha256"],
             "last_seen_rows": len(quotes),
             "last_seen_prefix_sha256": _row_prefix_hash(quotes, len(quotes)),
             "policy": asdict(policy),
             "observations": [], "predictions": [], "alerts": [],
             "approved_for_live_trading": False, "eligible_for_paper_promotion": False}
    _persist(output / "monitor_state.json", state)
    return {"schema": SCHEMA, "status": "FROZEN_ACTIVE_RESEARCH_ONLY",
            "frozen_rows": len(quotes), "frozen_at_utc": now.isoformat(),
            "approved_for_live_trading": False, "broker_access": "DISABLED"}


def _scores(predictions: list[dict], policy: MonitorPolicy) -> tuple[dict, bool]:
    scored = [x for x in predictions if x["actual_up"] is not None]
    recent = scored[-policy.drift_window:]
    n = len(recent)
    if not n:
        return {"scored": 0, "window_scored": 0, "accuracy": None, "brier": None}, False
    accuracy = sum((p["probability_up"] > 0.5) == p["actual_up"] for p in recent) / n
    brier = sum((p["probability_up"] - int(p["actual_up"]))**2 for p in recent) / n
    # Do not claim a calibrated or statistically significant drift detector.
    paused = (n >= policy.minimum_scored and
              (accuracy < policy.min_direction_accuracy or brier > policy.max_brier_score))
    return {"scored": len(scored), "window_scored": n,
            "accuracy": round(accuracy, 6), "brier": round(brier, 6)}, paused


def observe(*, quotes_path: Path, provenance_path: Path, out: Path,
            allow_synthetic: bool = False, _test_asof: datetime | None = None) -> dict:
    """Capture only newly completed H1 bars, with real first-seen wall-clock times.

    Late/backfilled bars are recorded as unqualified, never retrospectively scored
    as timely model predictions. A paused model NEVER resumes automatically.
    """
    output = _private_out(out)
    if not output.is_dir():
        raise ValueError("Monitor directory absent; use init")
    run_lock = output / ".observe_lock"
    try:
        run_lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("Another forward observation is active or needs manual lock audit") from exc
    try:
        state = _state_load(output / "monitor_state.json")
        record = json.loads((output / "immutable_model_lock.json").read_text(encoding="utf-8"))
        _locked_model_ok(record)
        if (state.get("frozen_lock_sha256") != record["lock_sha256"]
                or state.get("mode") != "RESEARCH_ONLY_NO_ORDERS"
                or state.get("approved_for_live_trading") is not False
                or state.get("eligible_for_paper_promotion") is not False
                or state.get("monitor_status") not in ("ACTIVE", "PAUSED_MODEL_DRIFT_REVIEW_REQUIRED")):
            raise ValueError("Unsafe/corrupted forward journal status")
        policy = MonitorPolicy(**state["policy"])
        policy.check()
        quotes, meta, audit = load_quotes(Path(quotes_path), Path(provenance_path),
                                         allow_synthetic=allow_synthetic)
        _source_ok(meta, record)
        synthetic = meta["source_kind"] == "SYNTHETIC_TEST_FIXTURE"
        now = _utc_now(_test_asof, synthetic=synthetic)
        if _test_asof is not None and not allow_synthetic:
            raise ValueError("Clock override forbidden for broker data")
        if pd.Timestamp(now) < pd.Timestamp(state["last_observed_at_utc"]):
            raise ValueError("Wall clock moved backwards; cannot establish first-seen order")
        n = int(state["last_seen_rows"])
        if (len(quotes) < n or len(quotes) < record["frozen_rows"]
                or _row_prefix_hash(quotes, n) != state["last_seen_prefix_sha256"]
                or _row_prefix_hash(quotes, record["frozen_rows"]) != record["prefix_sha256"]):
            raise ValueError("Prior observed H1 history has been revised/truncated")
        # Do not mislabel future/incomplete bars as observed.
        new_quotes = quotes.iloc[n:]
        if len(new_quotes) and any(t + pd.Timedelta(hours=1) > pd.Timestamp(now)
                                   for t in new_quotes["time"]):
            raise ValueError("Incoming snapshot contains not-yet-completed H1 bars")
        if len(quotes) == n:
            return {"schema": SCHEMA, "status": "NO_NEW_BARS", "new_bars": 0,
                    "monitor_status": state["monitor_status"], "approved_for_live_trading": False}
        predictions = state["predictions"]
        observations = state["observations"]
        new_predictions = 0
        late = 0
        # Feature extraction is causal (past/current closes only); no future target leakage.
        # Training is rebuilt ONLY from the immutable pre-freeze prefix.
        current_features, _, _ = causal_design(quotes)
        models = None
        if state["monitor_status"] == "ACTIVE":
            models, _, _ = _locked_training(quotes, int(record["frozen_rows"]),
                                            RetrainConfig(**record["model_cfg"]))
        cfg = RetrainConfig(**record["model_cfg"])
        for i in range(n, len(quotes)):
            row = quotes.iloc[i]
            t = row["time"].to_pydatetime()
            closed = t + timedelta(hours=1)
            lag = (now - closed).total_seconds() / 60
            # Once the target bar i+2 starts, the result could already be known.
            on_time = 0 <= lag <= policy.max_capture_lag_minutes and now < t + timedelta(hours=2)
            observations.append({"bar_index": i, "bar_open_utc": t.isoformat(),
                                 "first_seen_utc": now.isoformat(), "on_time": bool(on_time)})
            # Mature previous decisions BEFORE considering the next signal;
            # a fresh drift alert must block even the current bar's prediction.
            for p in predictions:
                si = int(p["signal_index"])
                if p["actual_up"] is not None or si + 2 > i:
                    continue
                if (quotes.iloc[si+1]["time"] - quotes.iloc[si]["time"] != pd.Timedelta(hours=1)
                        or quotes.iloc[si+2]["time"] - quotes.iloc[si+1]["time"] != pd.Timedelta(hours=1)):
                    continue
                op1 = (quotes.iloc[si+1]["bid_open"] + quotes.iloc[si+1]["ask_open"]) / 2
                op2 = (quotes.iloc[si+2]["bid_open"] + quotes.iloc[si+2]["ask_open"]) / 2
                p["actual_up"] = bool(op2 > op1)
                p["scored_at_utc"] = now.isoformat()
            interim_metrics, paused = _scores(predictions, policy)
            if paused and state["monitor_status"] == "ACTIVE":
                state["monitor_status"] = "PAUSED_MODEL_DRIFT_REVIEW_REQUIRED"
                state["alerts"].append({"at_utc": now.isoformat(),
                                        "reason": "EXPLORATORY_ACCURACY_OR_BRIER_THRESHOLD_BREACH",
                                        "scores": interim_metrics})
            if not on_time:
                late += 1
                continue
            if state["monitor_status"] != "ACTIVE" or models is None:
                continue
            if current_features.iloc[i].isna().any():
                continue
            probs = [float(m.predict_proba(current_features.iloc[[i]])[0, 1]) for m in models]
            if any(not 0 <= p <= 1 or not math.isfinite(p) for p in probs):
                raise ValueError("Nonfinite ML prediction")
            side = (1 if min(probs) >= cfg.probability_threshold
                    else -1 if max(probs) <= 1 - cfg.probability_threshold else 0)
            if float(current_features.iloc[i]["spread_close_pips"]) > cfg.max_signal_spread_pips:
                side = 0
            predictions.append({"signal_index": i, "signal_bar_open_utc": t.isoformat(),
                                "predicted_at_utc": now.isoformat(),
                                "probability_up": round(sum(probs)/2, 8),
                                "model_probs": [round(p, 8) for p in probs],
                                "side": side, "actual_up": None,
                                "scored_at_utc": None})
            new_predictions += 1
        metrics, _ = _scores(predictions, policy)
        state["last_seen_rows"] = len(quotes)
        state["last_seen_prefix_sha256"] = _row_prefix_hash(quotes, len(quotes))
        state["last_observed_at_utc"] = now.isoformat()
        state["last_snapshot_quote_sha256"] = audit["quotes_sha256"]
        _persist(output / "monitor_state.json", state)
        return {"schema": SCHEMA, "status": "SYNTHETIC_TEST_ONLY" if synthetic else
                "USER_ATTESTED_QUOTES_RESEARCH_ONLY", "monitor_status": state["monitor_status"],
                "new_bars": len(new_quotes), "late_unqualified_bars": late,
                "new_time_qualified_predictions": new_predictions,
                "scores": metrics, "alerts": len(state["alerts"]),
                "approved_for_live_trading": False, "eligible_for_paper_promotion": False,
                "broker_access": "DISABLED", "order_placement": "DISABLED"}
    finally:
        run_lock.rmdir()


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline locked-model forward monitor, NO broker/order access")
    commands = ap.add_subparsers(dest="cmd", required=True)
    init = commands.add_parser("init")
    review = commands.add_parser("observe")
    for p in (init, review):
        p.add_argument("--quotes", type=Path, required=True)
        p.add_argument("--provenance", type=Path, required=True)
        p.add_argument("--out", type=Path, default=Path("private_reports/forward_monitor"))
        p.add_argument("--allow-synthetic", action="store_true")
    init.add_argument("--slippage-pips-per-side", type=float, required=True)
    init.add_argument("--commission-jpy-per-roundtrip", type=float, required=True)
    init.add_argument("--probability-threshold", type=float, default=.60)
    init.add_argument("--max-capture-lag-minutes", type=int, default=15)
    args = ap.parse_args()
    if args.cmd == "init":
        result = start(quotes_path=args.quotes, provenance_path=args.provenance, out=args.out,
                       cfg=RetrainConfig(probability_threshold=args.probability_threshold),
                       execution=ExecutionConfig(slippage_pips_per_side=args.slippage_pips_per_side,
                                                 commission_jpy_per_roundtrip=args.commission_jpy_per_roundtrip),
                       policy=MonitorPolicy(max_capture_lag_minutes=args.max_capture_lag_minutes),
                       allow_synthetic=args.allow_synthetic)
    else:
        result = observe(quotes_path=args.quotes, provenance_path=args.provenance,
                         out=args.out, allow_synthetic=args.allow_synthetic)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

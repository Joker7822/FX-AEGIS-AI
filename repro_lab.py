"""FX AEGIS AI: reproducible, research-only parameter experiments.

Do not confuse FRED DEXJPUS daily reference values with executable FX quotes.
The holdout is *never* used to select candidates. Repeated manual holdout
inspection invalidates its practical status as a fresh holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from github_pipeline import DATASET, META, ema_series, read_reference

ROOT = Path(__file__).resolve().parent
SCHEMA = "fx-aegis-repro-1"
SPLITS = (
    ("train", "2006-11-13", "2016-01-01"),
    ("validation", "2016-01-01", "2021-01-01"),
    ("holdout", "2021-01-01", "9999-12-31"),
)


def canonical(obj: dict | list) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, obj: dict | list) -> None:
    from github_pipeline import _atomic_text
    _atomic_text(path, json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def validate_source(data: Path, metadata: Path, *, fixture: bool = False) -> tuple[list[dict], dict]:
    points = read_reference(data)
    if len(points) < 252:
        raise ValueError("At least 252 historical observations are required")
    raw_sha = sha256(data.read_bytes())
    if fixture:
        return points, {"kind": "SYNTHETIC_FIXTURE_NOT_FOR_TRADING", "data_sha256": raw_sha,
                        "metadata_sha256": None, "source": "explicit --fixture mode"}
    if not metadata.is_file():
        raise ValueError("Missing FRED source provenance; use --fixture only for synthetic tests")
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    if meta.get("kind") != "daily_reference_rate_nontradeable":
        raise ValueError("Dataset is not verified FRED daily reference data")
    if meta.get("csv_sha256") != raw_sha or int(meta.get("rows", -1)) != len(points):
        raise ValueError("Dataset hash or row count differs from FRED metadata")
    if meta.get("start_actual") != points[0]["date"] or meta.get("end_actual") != points[-1]["date"]:
        raise ValueError("Dataset dates disagree with source provenance")
    return points, {"kind": "FRED_DAILY_REFERENCE_NON_EXECUTABLE", "data_sha256": raw_sha,
                    "metadata_sha256": sha256(metadata.read_bytes()),
                    "source": meta.get("source_url", "https://fred.stlouisfed.org/series/DEXJPUS")}


def config(fast: int = 15, slow: int = 100, allocation: float = .25,
           spread_pips: float = 1.4, allow_short: bool = True, capital: float = 1_000_000) -> dict:
    if not (isinstance(fast, int) and isinstance(slow, int) and 2 <= fast < slow <= 500):
        raise ValueError("Require integer EMA periods 2 <= fast < slow <= 500")
    if not (math.isfinite(allocation) and 0 < allocation <= 1):
        raise ValueError("Allocation out of range")
    if not (math.isfinite(spread_pips) and 0 <= spread_pips <= 50):
        raise ValueError("Cost out of range")
    if not (math.isfinite(capital) and 1000 <= capital <= 1e9):
        raise ValueError("Capital out of range")
    if not isinstance(allow_short, bool):
        raise ValueError("allow_short must be boolean")
    return {"fast": fast, "slow": slow, "allocation": allocation,
            "spread_pips": spread_pips, "allow_short": allow_short, "capital": capital}


def _ema_cache(points: list[dict], periods: set[int]) -> tuple[list[float], dict[int, list[float]]]:
    prices = [p["close"] for p in points]
    return prices, {period: ema_series(prices, period) for period in sorted(periods)}


def simulate_window(points: list[dict], cfg: dict, start: str, stop: str,
                    cache: tuple[list[float], dict[int, list[float]]] | None = None,
                    *, cost_multiple: float = 1) -> dict:
    """Same reference-close return convention as github_pipeline.simulate_reference.

    EMA state uses only prices from before each evaluated interval; positions
    reset to flat at the *beginning* of each evaluation window.
    """
    if not (math.isfinite(cost_multiple) and cost_multiple >= 0):
        raise ValueError("Cost multiple invalid")
    cfg = config(**cfg)
    prices, emas = cache or _ema_cache(points, {cfg["fast"], cfg["slow"]})
    equ = float(cfg["capital"])
    peak = equ
    dd = 0.0
    last = 0
    transitions = 0
    evaluations = 0
    first = None
    prev_included = None
    # warmup uses entire historical prefix, never the future
    for i in range(cfg["slow"] + 1, len(points)):
        dt = points[i]["date"]
        if dt < start:
            continue
        if dt >= stop:
            break
        prev = i - 1
        side = 1 if emas[cfg["fast"]][prev] > emas[cfg["slow"]][prev] else (-1 if cfg["allow_short"] and emas[cfg["fast"]][prev] < emas[cfg["slow"]][prev] else 0)
        moves = abs(side - last)
        cost = moves * cfg["spread_pips"] * cost_multiple * .01 / prices[prev] * cfg["allocation"]
        change = cfg["allocation"] * side * (prices[i] / prices[prev] - 1) - cost
        equ *= max(.000001, 1 + change)
        peak = max(peak, equ)
        dd = max(dd, 100 * (1 - equ / peak))
        transitions += int(moves > 0)
        last = side
        first = first or dt
        prev_included = dt
        evaluations += 1
    if evaluations < 200:
        raise ValueError(f"Insufficient sample in {start} to {stop}: {evaluations} sessions")
    return {"start": first, "end": prev_included, "observations": evaluations,
            "return_pct": round(100 * (equ / cfg["capital"] - 1), 6),
            "max_dd_pct": round(dd, 6), "position_changes": transitions}


def env() -> dict:
    return {"python": platform.python_version(), "numpy": np.__version__,
            "pandas": pd.__version__}


def engine_hashes() -> dict:
    files = ("repro_lab.py", "github_pipeline.py")
    return {f: sha256((ROOT / f).read_bytes()) for f in files}


def evaluate(points: list[dict], cfg: dict) -> dict:
    cache = _ema_cache(points, {cfg["fast"], cfg["slow"]})
    metrics = {key: simulate_window(points, cfg, begin, end, cache)
               for key, begin, end in SPLITS}
    metrics["holdout_double_cost"] = simulate_window(points, cfg, SPLITS[-1][1], SPLITS[-1][2], cache, cost_multiple=2)
    # No automatic real-trade acceptance based on daily reference rates.
    return metrics


def capture(data: Path, meta: Path, out: Path, cfg: dict, *, fixture: bool = False, seed: int = 42) -> dict:
    points, source = validate_source(data, meta, fixture=fixture)
    metrics = evaluate(points, cfg)
    immutable = {
        "schema": SCHEMA, "model": "ema_cross_daily_reference_v1", "source": source,
        "data_rows": len(points), "first_observation": points[0]["date"], "last_observation": points[-1]["date"],
        "config": cfg, "splits": list(SPLITS), "seed": seed,
        "seed_usage": "Recorded for future stochastic agents; EMA calculations are deterministic without RNG",
        "software": env(), "engine_sha256": engine_hashes(),
    }
    result = {"schema": SCHEMA, "metrics": metrics,
              "approved_for_live_trading": False,
              "disclaimer": "Historical FRED daily reference exercise, not an executable backtest or future-return forecast"}
    record = {"manifest": immutable, "result": result}
    proof = {"record_sha256": sha256(canonical(record)),
             "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "git_revision": os.getenv("GITHUB_SHA", "unavailable-local"),
             "record": record}
    atomic_json(out, proof)
    return proof


def verify(proof_path: Path, data: Path, meta: Path) -> dict:
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    record = proof["record"]
    if proof["record_sha256"] != sha256(canonical(record)):
        raise ValueError("Proof file has been modified")
    expected = record["manifest"]
    if expected["schema"] != SCHEMA or record["result"].get("schema") != SCHEMA:
        raise ValueError("Incompatible proof schema")
    points, source = validate_source(data, meta, fixture=expected["source"]["kind"] == "SYNTHETIC_FIXTURE_NOT_FOR_TRADING")
    if expected["source"] != source:
        raise ValueError("Data or metadata bytes differ from recorded snapshot")
    if expected["software"] != env():
        raise ValueError("Runtime versions differ; restore recorded environment before replay")
    if expected["engine_sha256"] != engine_hashes():
        raise ValueError("Code bytes differ from recorded engine")
    if expected["data_rows"] != len(points) or expected["last_observation"] != points[-1]["date"] or expected["first_observation"] != points[0]["date"]:
        raise ValueError("Dataset observation range changed")
    if expected["splits"] != [list(s) for s in SPLITS]:
        raise ValueError("Research period protocol changed")
    rerun = evaluate(points, expected["config"])
    if rerun != record["result"]["metrics"]:
        raise ValueError("Recomputed metrics differ")
    return {"verified": True, "record_sha256": proof["record_sha256"],
            "data_sha256": source["data_sha256"], "approved_for_live_trading": False}


def scan(data: Path, meta: Path, out: Path, *, fixture: bool = False,
         seed: int = 42, spread_pips: float = 1.4) -> dict:
    points, source = validate_source(data, meta, fixture=fixture)
    # This grid and windows are fixed before looking at the holdout.
    pairs = [(f, s) for f in (10, 15, 20, 50) for s in (100, 150, 200) if f < s]
    cache = _ema_cache(points, {n for pair in pairs for n in pair})
    candidates = []
    for f, s in pairs:
        for allocation in (.25, .35):
            for short in (True, False):
                cfg = config(f, s, allocation, spread_pips, short)
                train = simulate_window(points, cfg, SPLITS[0][1], SPLITS[0][2], cache)
                val = simulate_window(points, cfg, SPLITS[1][1], SPLITS[1][2], cache)
                val_cost2 = simulate_window(points, cfg, SPLITS[1][1], SPLITS[1][2], cache, cost_multiple=2)
                # Ranking uses training/validation only; zero holdout access.
                score = round(val["return_pct"] - .5 * val["max_dd_pct"], 6)
                eligible = (train["return_pct"] > 0 and val["return_pct"] > 0
                            and val_cost2["return_pct"] > 0 and val["max_dd_pct"] <= 10)
                candidates.append({"config": cfg, "train": train, "validation": val,
                                   "validation_double_cost": val_cost2, "score": score,
                                   "passed_research_gates": eligible})
    candidates.sort(key=lambda c: (-c["score"], c["config"]["fast"],
                                   c["config"]["slow"], c["config"]["allocation"],
                                   c["config"]["allow_short"]))
    chosen = candidates[0]
    hold = simulate_window(points, chosen["config"], SPLITS[2][1], SPLITS[2][2], cache)
    hold_cost2 = simulate_window(points, chosen["config"], SPLITS[2][1], SPLITS[2][2], cache, cost_multiple=2)
    report = {
        "schema": SCHEMA, "source": source, "data_rows": len(points),
        "data_range": [points[0]["date"], points[-1]["date"]],
        "splits": list(SPLITS), "random_seed": seed,
        "selection_rule": "max validation_return_pct - 0.5 * validation_max_dd; training and validation only",
        "candidate_count": len(candidates), "candidates_ranked_without_holdout": candidates,
        "holdout_examined_once_for_top_candidate": {"config": chosen["config"],
                                                     "holdout": hold,
                                                     "holdout_double_cost": hold_cost2},
        "research_gate_passed": bool(chosen["passed_research_gates"]),
        "approved_for_live_trading": False,
        "warning": "Repeated use of this fixed holdout to change the grid/strategy invalidates it as an unused holdout.",
        "software": env(), "engine_sha256": engine_hashes(),
    }
    report["report_sha256"] = sha256(canonical(report))
    atomic_json(out, report)
    return report


def publish(proof_path: Path, scan_path: Path, data: Path, meta: Path, output: Path) -> dict:
    """Produce a minimal Pages status only after successful deterministic replay."""
    checked = verify(proof_path, data, meta)
    scan_report = json.loads(scan_path.read_text(encoding="utf-8"))
    if scan_report.get("schema") != SCHEMA or scan_report.get("report_sha256") != sha256(canonical({k:v for k,v in scan_report.items() if k != "report_sha256"})):
        raise ValueError("Parameter search report integrity check failed")
    if scan_report.get("source", {}).get("data_sha256") != checked["data_sha256"]:
        raise ValueError("Parameter search did not use same data snapshot")
    selected = scan_report["holdout_examined_once_for_top_candidate"]
    obj = {
        "status": "REPLAY_VERIFIED", "schema": SCHEMA, "source_kind": scan_report["source"]["kind"],
        "data_sha256": checked["data_sha256"], "record_sha256": checked["record_sha256"],
        "scan_sha256": scan_report["report_sha256"],
        "candidate_count": scan_report["candidate_count"],
        "selected_config": selected["config"],
        "selected_validation": scan_report["candidates_ranked_without_holdout"][0]["validation"],
        "selected_holdout": selected["holdout"],
        "research_gate_passed": bool(scan_report["research_gate_passed"]),
        "approved_for_live_trading": False,
        "warning": "Reproducibility is not profitability. FRED daily reference results are not executable FX backtests.",
    }
    atomic_json(output, obj)
    return obj


def main() -> None:
    p = argparse.ArgumentParser(description="Reproducible FX daily-reference research, no live trading")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("capture", "scan", "verify", "publish"):
        s = sub.add_parser(name)
        s.add_argument("--dataset", type=Path, default=DATASET)
        s.add_argument("--metadata", type=Path, default=META)
        if name in ("verify", "publish"):
            s.add_argument("--proof", type=Path, required=True)
            if name == "publish":
                s.add_argument("--scan", type=Path, required=True)
                s.add_argument("--out", type=Path, required=True)
        else:
            s.add_argument("--out", type=Path, required=True)
            s.add_argument("--fixture", action="store_true", help="Explicit synthetic-data test mode")
            s.add_argument("--seed", type=int, default=42)
            s.add_argument("--spread-pips", type=float, default=1.4)
        if name == "capture":
            s.add_argument("--fast", type=int, default=15)
            s.add_argument("--slow", type=int, default=100)
            s.add_argument("--allocation", type=float, default=.25)
            s.add_argument("--capital", type=float, default=1_000_000)
            s.add_argument("--long-only", action="store_true")
    args = p.parse_args()
    if args.command == "capture":
        cfg = config(args.fast, args.slow, args.allocation, args.spread_pips,
                     not args.long_only, args.capital)
        obj = capture(args.dataset, args.metadata, args.out, cfg, fixture=args.fixture, seed=args.seed)
        print(json.dumps({"proof": str(args.out), "record_sha256": obj["record_sha256"],
                          "metrics": obj["record"]["result"]["metrics"]}, ensure_ascii=False, indent=2))
    elif args.command == "verify":
        print(json.dumps(verify(args.proof, args.dataset, args.metadata), ensure_ascii=False, indent=2))
    elif args.command == "publish":
        print(json.dumps(publish(args.proof, args.scan, args.dataset, args.metadata, args.out), ensure_ascii=False, indent=2))
    else:
        obj = scan(args.dataset, args.metadata, args.out, fixture=args.fixture,
                   seed=args.seed, spread_pips=args.spread_pips)
        print(json.dumps({"report": str(args.out), "report_sha256": obj["report_sha256"],
                          "candidate_count": obj["candidate_count"],
                          "selected": obj["holdout_examined_once_for_top_candidate"],
                          "research_gate_passed": obj["research_gate_passed"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""FX AEGIS AI v1.9 — conservative, deterministic USDJPY H1 Bid/Ask research.

STRICTLY RESEARCH ONLY: no broker connection, no order placement.

- Bar timestamps denote UTC OPEN times. Signal on completed bar i is filled at
  the next bar's open (i+1), exited at the following bar's open (i+2).
- Long: Ask entry / Bid exit. Short: Bid entry / Ask exit.
- Unknown rollover/overnight financing => veto, not silently zero-cost.
- Metadata hashes guard against file substitution, but do not authenticate a
  broker's claimed source. External signals can still contain future leakage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = "fx-aegis-h1-bidask-v1"
PIP_JPY = 0.01  # USD/JPY pip; not appropriate for all currency pairs
COLUMNS = ["time", "bid_open", "bid_high", "bid_low", "bid_close",
           "ask_open", "ask_high", "ask_low", "ask_close"]
UTC_PATTERN = r"(?:Z|[+-]\d\d:\d\d)$"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def utc_times(values: pd.Series, field: str) -> pd.Series:
    if values.isna().any() or not values.astype(str).str.contains(UTC_PATTERN, regex=True).all():
        raise ValueError(f"{field} requires explicit UTC offset/Z timestamps")
    return pd.to_datetime(values, utc=True, errors="raise")


def load_quotes(path: Path, provenance: Path, *, allow_synthetic: bool = False) -> tuple[pd.DataFrame, dict, dict]:
    if not path.is_file() or not provenance.is_file():
        raise ValueError("Both quotes CSV and provenance JSON are mandatory")
    meta = json.loads(provenance.read_text(encoding="utf-8"))
    required = ["schema", "source_kind", "source_name", "symbol", "timestamp_semantics",
                "account_currency", "quote_timezone", "sha256", "license_scope"]
    if not isinstance(meta, dict) or any(not meta.get(k) for k in required):
        raise ValueError("Missing mandatory broker/source provenance fields")
    if (meta["schema"] != SCHEMA or meta["symbol"] != "USDJPY" or
        meta["timestamp_semantics"] != "BAR_OPEN" or
        meta["quote_timezone"] != "UTC" or meta["account_currency"] != "JPY"):
        raise ValueError("Only timestamped USDJPY H1 Bid/Ask / JPY accounts are supported")
    synthetic = meta["source_kind"] == "SYNTHETIC_TEST_FIXTURE"
    if synthetic and not allow_synthetic:
        raise ValueError("Synthetic dataset requires explicit --allow-synthetic")
    if not synthetic and meta["source_kind"] != "BROKER_BID_ASK_H1":
        raise ValueError("Unrecognized price provenance; FRED daily reference is not a Bid/Ask feed")
    checksum = sha256_file(path)
    if meta["sha256"] != checksum:
        raise ValueError("Quote CSV SHA-256 differs from source provenance")
    quotes = pd.read_csv(path)
    if list(quotes.columns) != COLUMNS:
        raise ValueError(f"CSV must have exact columns: {','.join(COLUMNS)}")
    if len(quotes) < 200:
        raise ValueError("Need >=200 actual H1 bars to inspect coverage and test orders")
    quotes["time"] = utc_times(quotes["time"], "time")
    if not quotes["time"].is_monotonic_increasing or quotes["time"].duplicated().any():
        raise ValueError("H1 bar-open timestamps must be strictly increasing and unique")
    if (quotes["time"].dt.minute.ne(0) | quotes["time"].dt.second.ne(0) |
        quotes["time"].dt.microsecond.ne(0)).any():
        raise ValueError("H1 bars must be aligned to a full UTC hour")
    for col in COLUMNS[1:]:
        quotes[col] = pd.to_numeric(quotes[col], errors="raise")
    values = quotes[COLUMNS[1:]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Bid/Ask OHLC prices must be finite positive numbers")
    if (quotes["ask_open"] < quotes["bid_open"]).any() or (quotes["ask_close"] < quotes["bid_close"]).any():
        raise ValueError("Ask cannot be below Bid at the matched open/close snapshot")
    for pfx in ("bid", "ask"):
        hi, lo = quotes[f"{pfx}_high"], quotes[f"{pfx}_low"]
        if ((hi < quotes[[f"{pfx}_open", f"{pfx}_close", f"{pfx}_low"]].max(axis=1)) |
            (lo > quotes[[f"{pfx}_open", f"{pfx}_close", f"{pfx}_high"]].min(axis=1))).any():
            raise ValueError(f"{pfx} OHLC high/low is internally inconsistent")
    spacing = quotes["time"].diff().dt.total_seconds().dropna() / 3600.
    one_hour_ratio = float((spacing == 1).mean())
    if one_hour_ratio < .85:
        raise ValueError("Data does not plausibly represent H1 bars (too few consecutive hours)")
    spreads = (quotes["ask_open"] - quotes["bid_open"]) / PIP_JPY
    audit = {
        "data_kind": "SYNTHETIC_FIXTURE" if synthetic else "BROKER_QUOTE_USER_ATTESTED_NOT_INDEPENDENTLY_VERIFIED",
        "quotes_sha256": checksum,
        "source": meta["source_name"],
        "license_scope": meta["license_scope"],
        "rows": len(quotes), "start_utc": quotes["time"].iloc[0].isoformat(),
        "end_utc": quotes["time"].iloc[-1].isoformat(),
        "h1_consecutive_fraction": round(one_hour_ratio, 6),
        "gap_count": int((spacing > 1).sum()),
        "max_gap_hours": round(float(spacing.max()), 3),
        "open_spread_pips_median": round(float(spreads.median()), 4),
        "open_spread_pips_p95": round(float(spreads.quantile(.95)), 4),
        "open_spread_pips_max": round(float(spreads.max()), 4),
        "can_validate_real_profitability": False,
        "broker_origin_independently_confirmed": False,
    }
    return quotes.reset_index(drop=True), meta, audit


def load_signals(path: Path, quotes: pd.DataFrame) -> dict[int, int]:
    signals = pd.read_csv(path)
    if list(signals.columns) != ["bar_open_time", "side"]:
        raise ValueError("Signals CSV needs exactly bar_open_time,side")
    times = utc_times(signals["bar_open_time"], "bar_open_time")
    if times.duplicated().any() or (not times.is_monotonic_increasing):
        raise ValueError("Signal times must be unique and increasing")
    sides = pd.to_numeric(signals["side"], errors="raise")
    if sides.isna().any() or not sides.isin([-1, 0, 1]).all():
        raise ValueError("side must be -1,0,1")
    index = {t: i for i, t in enumerate(quotes["time"])}
    out = {}
    for timestamp, side in zip(times, sides):
        if timestamp not in index:
            raise ValueError(f"Signal {timestamp} does not correspond to an H1 bar")
        out[index[timestamp]] = int(side)
    return out


def ema_signals(quotes: pd.DataFrame, fast: int, slow: int, *, allow_short: bool) -> dict[int, int]:
    if not (2 <= fast < slow <= 500):
        raise ValueError("Require 2 <= fast < slow <= 500")
    midpoint = (quotes["bid_close"] + quotes["ask_close"]) / 2
    a = midpoint.ewm(span=fast, adjust=False).mean()
    b = midpoint.ewm(span=slow, adjust=False).mean()
    return {i: 1 if a.iloc[i] > b.iloc[i] else -1 if allow_short and a.iloc[i] < b.iloc[i] else 0
            for i in range(slow, len(quotes))}


def load_rollovers(path: Path | None) -> dict[str, tuple[pd.Timestamp, float, float]] | None:
    if path is None:
        return None
    df = pd.read_csv(path)
    needed = ["date_utc", "rollover_time_utc", "swap_long_jpy_per_1000usd", "swap_short_jpy_per_1000usd"]
    if list(df.columns) != needed:
        raise ValueError("Swap ledger must include date_utc,rollover_time_utc,long/short JPY per 1000 USD")
    times = utc_times(df["rollover_time_utc"], "rollover_time_utc")
    out: dict[str, tuple[pd.Timestamp, float, float]] = {}
    for i, row in df.iterrows():
        day = str(row["date_utc"])
        if day != times.iloc[i].strftime("%Y-%m-%d") or day in out:
            raise ValueError("Swap ledger dates must match UTC timestamps and be unique")
        long = float(row["swap_long_jpy_per_1000usd"])
        short = float(row["swap_short_jpy_per_1000usd"])
        if not (math.isfinite(long) and math.isfinite(short)):
            raise ValueError("Swap ledger requires finite signed amounts")
        out[day] = (times.iloc[i], long, short)
    return out


def uncertain_rollover_window(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    """Fail closed without actual broker schedule: 21/22 UTC and midnight are risky.

    NY 17:00 rollover varies with daylight saving time. This overly broad veto
    cannot certify all broker rules, so it is an explicit conservative shortcut.
    """
    if start.date() != end.date():
        return True
    for hour in (21, 22):
        marker = start.normalize() + pd.Timedelta(hours=hour)
        if start <= marker < end:
            return True
    return False


def financing(start: pd.Timestamp, end: pd.Timestamp, side: int, qty: int,
              rollovers: dict[str, tuple[pd.Timestamp, float, float]] | None) -> tuple[float | None, str]:
    if rollovers is None:
        return (None, "UNKNOWN_ROLLOVER") if uncertain_rollover_window(start, end) else (0., "OK")
    for day in pd.date_range(start.normalize(), end.normalize(), freq="D", tz="UTC"):
        if day.strftime("%Y-%m-%d") not in rollovers:
            return None, "MISSING_SWAP_COVERAGE"
    swap = 0.
    for t, long, short in rollovers.values():
        if start <= t < end:
            swap += (long if side == 1 else short) * qty / 1000.
    return swap, "OK"


@dataclass(frozen=True)
class ExecutionConfig:
    capital_jpy: float = 1_000_000
    allocation: float = .25
    lot_step_usd: int = 1000
    max_spread_pips: float = 2.0
    slippage_pips_per_side: float = .2
    commission_jpy_per_roundtrip: float = 0.
    max_dd_fraction: float = .10
    daily_loss_fraction: float = .02

    def check(self) -> None:
        if not (1000 <= self.capital_jpy <= 1e9 and 0 < self.allocation <= .5
                and isinstance(self.lot_step_usd, int) and 1 <= self.lot_step_usd <= 100000
                and 0 <= self.max_spread_pips <= 100
                and 0 <= self.slippage_pips_per_side <= 100
                and 0 <= self.commission_jpy_per_roundtrip <= 1e6
                and 0 < self.max_dd_fraction <= .5 and 0 < self.daily_loss_fraction <= .2):
            raise ValueError("Out-of-range execution/risk parameters")


def simulate(quotes: pd.DataFrame, side_by_bar: dict[int, int], cfg: ExecutionConfig,
             *, rollovers: dict[str, tuple[pd.Timestamp, float, float]] | None = None) -> dict:
    cfg.check()
    equity, peak = cfg.capital_jpy, cfg.capital_jpy
    daily_equity, day = cfg.capital_jpy, None
    stop = False
    busy_until = -1
    skips: dict[str, int] = {}
    ledger = []
    curve = [{"time_utc": quotes.iloc[0]["time"].isoformat(), "equity_jpy": round(equity, 2)}]
    for i, side in sorted(side_by_bar.items()):
        if side == 0:
            continue
        reason = None
        if i < 0 or i+2 >= len(quotes):
            reason = "MISSING_FUTURE_OPEN"
        elif i < busy_until:
            reason = "OVERLAPPING_POSITION"
        else:
            signal, entry, exit_bar = quotes.iloc[i], quotes.iloc[i+1], quotes.iloc[i+2]
            entry_time, exit_time = entry["time"], exit_bar["time"]
            if (entry_time - signal["time"] != pd.Timedelta(hours=1) or
                exit_time - entry_time != pd.Timedelta(hours=1)):
                reason = "GAP_OR_WEEKEND"
            elif stop or equity <= peak*(1-cfg.max_dd_fraction):
                reason = "MAX_DRAWDOWN_HALT"
            else:
                if day != entry_time.date():
                    day, daily_equity = entry_time.date(), equity
                if equity <= daily_equity*(1-cfg.daily_loss_fraction):
                    reason = "DAILY_LOSS_HALT"
                else:
                    spread = (entry["ask_open"] - entry["bid_open"]) / PIP_JPY
                    if spread > cfg.max_spread_pips:
                        reason = "SPREAD_TOO_WIDE"
                    else:
                        swap, swap_result = financing(entry_time, exit_time, side, 1000, rollovers)
                        if swap_result != "OK":
                            reason = swap_result
                        else:
                            # Entry side is the quote that can actually be crossed.
                            px = entry["ask_open"] if side == 1 else entry["bid_open"]
                            qty = int(equity * cfg.allocation / px / cfg.lot_step_usd) * cfg.lot_step_usd
                            if qty < cfg.lot_step_usd:
                                reason = "INSUFFICIENT_EQUITY_FOR_LOT"
                            else:
                                slip = cfg.slippage_pips_per_side * PIP_JPY
                                entry_fill = (entry["ask_open"] + slip) if side == 1 else (entry["bid_open"] - slip)
                                exit_fill = (exit_bar["bid_open"] - slip) if side == 1 else (exit_bar["ask_open"] + slip)
                                gross = side * (exit_fill-entry_fill) * qty
                                swap_jpy = (swap or 0.) * qty/1000.
                                pnl = gross + swap_jpy - cfg.commission_jpy_per_roundtrip
                                equity += pnl
                                peak = max(equity, peak)
                                stop = stop or equity <= peak*(1-cfg.max_dd_fraction)
                                ledger.append({
                                    "signal_bar_time_utc": signal["time"].isoformat(),
                                    "entry_time_utc": entry_time.isoformat(), "exit_time_utc": exit_time.isoformat(),
                                    "side": side, "units_usd": qty,
                                    "entry_fill_jpy_per_usd": round(float(entry_fill), 6),
                                    "exit_fill_jpy_per_usd": round(float(exit_fill), 6),
                                    "entry_spread_pips": round(float(spread), 5),
                                    "gross_after_slippage_before_commission_jpy": round(float(gross), 4),
                                    "swap_jpy": round(float(swap_jpy), 4),
                                    "commission_jpy": cfg.commission_jpy_per_roundtrip,
                                    "net_pnl_jpy": round(float(pnl), 4),
                                    "equity_after_jpy": round(float(equity), 4)
                                })
                                curve.append({"time_utc": exit_time.isoformat(), "equity_jpy": round(equity, 2)})
                                busy_until = i+2
        if reason:
            skips[reason] = skips.get(reason, 0) + 1
    values = [c["equity_jpy"] for c in curve]
    high, max_dd = values[0], 0.
    for v in values:
        high = max(high, v)
        max_dd = max(max_dd, (1-v/high)*100)
    return {
        "research_only": True, "approved_for_live_trading": False,
        "signals_provided": len(side_by_bar),
        "trades": len(ledger), "skips_by_reason": skips,
        "initial_capital_jpy": cfg.capital_jpy,
        "final_capital_jpy": round(equity, 4),
        "return_pct": round(100*(equity/cfg.capital_jpy-1), 6),
        "max_closed_equity_drawdown_pct": round(max_dd, 6),
        "assumptions": asdict(cfg),
        "known_limitations": [
            "Uses H1 bar OPEN quotes, not tick-level execution or available liquidity",
            "Slippage is constant and hypothetical; price improvements/requotes/partial fills not represented",
            "Swap schedule must be broker-supplied; missing coverage causes veto",
            "Closed-trade drawdown excludes intrabar exposure and margin liquidation",
            "Historical signal file may have future leakage; externally supplied signals require separate audit",
            "A source hash alone cannot independently authenticate broker provenance",
        ],
        "ledger": ledger, "equity_curve": curve,
    }


def make_fixture(output: Path, *, n: int = 1250, seed: int = 42) -> tuple[Path, Path]:
    if n < 200 or n > 10000:
        raise ValueError("Fixture n outside 200..10000")
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    t = datetime(2023, 1, 2, tzinfo=timezone.utc)
    mid, rows = 131.0, []
    for _ in range(n):
        while t.weekday() >= 5:
            t += timedelta(hours=1)
        spread = rng.uniform(.006, .019)
        bump = rng.gauss(0, .039)
        midpoint_open = mid
        midpoint_close = mid + bump
        bid_open = midpoint_open-spread/2
        ask_open = midpoint_open+spread/2
        bid_close = midpoint_close-spread/2
        ask_close = midpoint_close+spread/2
        pad_hi = rng.uniform(.005, .024)
        pad_lo = rng.uniform(.005, .024)
        bar = [t.isoformat().replace("+00:00", "Z")]
        for op,cl in [(bid_open,bid_close),(ask_open,ask_close)]:
            bar.extend([op,max(op,cl)+pad_hi,min(op,cl)-pad_lo,cl])
        rows.append(bar)
        t += timedelta(hours=1)
        mid = midpoint_close
    csv_path = output/'bidask_h1_synthetic.csv'
    pd.DataFrame(rows,columns=COLUMNS).to_csv(csv_path,index=False,float_format="%.6f")
    metadata_path = output/'provenance.json'
    write_json(metadata_path, {
        "schema": SCHEMA,"source_kind":"SYNTHETIC_TEST_FIXTURE",
        "source_name":"deterministic random fixture, NOT MARKET DATA", "symbol":"USDJPY",
        "timestamp_semantics":"BAR_OPEN", "account_currency":"JPY", "quote_timezone":"UTC",
        "license_scope":"synthetic_test_only", "sha256":sha256_file(csv_path), "seed":seed,
    })
    return csv_path, metadata_path


def run_cli() -> None:
    p = argparse.ArgumentParser(description="H1 broker Bid/Ask research: NEVER submits orders")
    sub = p.add_subparsers(dest="action", required=True)
    fix = sub.add_parser("fixture", help="Create deterministic synthetic-only H1 Bid/Ask")
    fix.add_argument("--out", type=Path, default=Path("data/bidask_fixture"))
    fix.add_argument("--rows", type=int, default=1250)
    fix.add_argument("--seed", type=int, default=42)
    for action in ("validate", "simulate"):
        x = sub.add_parser(action)
        x.add_argument("--quotes", type=Path, required=True)
        x.add_argument("--provenance", type=Path, required=True)
        x.add_argument("--allow-synthetic", action="store_true")
        x.add_argument("--out", type=Path, required=True)
        if action == "simulate":
            x.add_argument("--signals", type=Path, help="Pre-generated bar_open_time,side (-1/0/1); must be PIT-audited")
            x.add_argument("--fast", type=int, default=15)
            x.add_argument("--slow", type=int, default=100)
            x.add_argument("--long-only", action="store_true")
            x.add_argument("--allocation", type=float, default=.25)
            x.add_argument("--capital-jpy", type=float, default=1_000_000)
            x.add_argument("--lot-step-usd", type=int, default=1000)
            x.add_argument("--max-spread-pips", type=float, default=2.)
            # Force user to state cost assumptions, even if they are 0 by documented broker terms.
            x.add_argument("--slippage-pips-per-side", type=float, required=True)
            x.add_argument("--commission-jpy-per-roundtrip", type=float, required=True)
            x.add_argument("--swap-ledger", type=Path, default=None)
    args = p.parse_args()
    if args.action == "fixture":
        q,m = make_fixture(args.out,n=args.rows,seed=args.seed)
        print(json.dumps({"synthetic_quotes":str(q),"provenance":str(m)}))
        return
    quotes, meta, audit = load_quotes(args.quotes,args.provenance,allow_synthetic=args.allow_synthetic)
    args.out.mkdir(parents=True,exist_ok=True)
    write_json(args.out/'quote_audit.json',audit)
    if args.action == "validate":
        print(json.dumps(audit,ensure_ascii=False))
        return
    cfg = ExecutionConfig(capital_jpy=args.capital_jpy, allocation=args.allocation,
                          lot_step_usd=args.lot_step_usd,max_spread_pips=args.max_spread_pips,
                          slippage_pips_per_side=args.slippage_pips_per_side,
                          commission_jpy_per_roundtrip=args.commission_jpy_per_roundtrip)
    signals = load_signals(args.signals,quotes) if args.signals else ema_signals(quotes,args.fast,args.slow,
                                                                                 allow_short=not args.long_only)
    swaps = load_rollovers(args.swap_ledger)
    results = simulate(quotes,signals,cfg,rollovers=swaps)
    results["data_kind"] = audit["data_kind"]
    results["quote_file_sha256"] = audit["quotes_sha256"]
    results["source_name"] = meta["source_name"]
    results["signal_source"] = "EXTERNAL_UNVERIFIED_POINT_IN_TIME" if args.signals else "EMA_COMPLETED_BAR"
    results["rollover_source"] = str(args.swap_ledger) if args.swap_ledger else "NONE_VETO_UNCERTAIN"
    led = results.pop("ledger")
    eq = results.pop("equity_curve")
    pd.DataFrame(led).to_csv(args.out/'trades.csv',index=False)
    pd.DataFrame(eq).to_csv(args.out/'equity.csv',index=False)
    write_json(args.out/'execution_report.json',results)
    print(json.dumps({k:results[k] for k in ("data_kind","trades","return_pct","max_closed_equity_drawdown_pct","skips_by_reason")}, ensure_ascii=False))

if __name__ == "__main__":
    run_cli()

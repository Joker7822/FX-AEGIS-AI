"""FX multi-agent research backtester. NO broker connection / NO live orders.

Use only USD/JPY OHLC data with UTC timestamps. Predictions use completed bar t;
idealized entry at t+1 open, exit at t+2 open, with costs. Research only.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from trading_conditions import add_market_regime, apply_gates, load_events, TradeGates
from custom_agents import CustomDirectionAgent, load_spec


TREND_FEATURES = ["return_1", "return_3", "return_6", "ema_gap", "ma_slope", "range_pct"]
REVERSION_FEATURES = ["return_1", "return_3", "zscore20", "rsi14", "range_pct", "hour_sin", "hour_cos"]
VOL_FEATURES = ["return_1", "return_3", "volatility12", "range_pct", "zscore20", "hour_sin", "hour_cos"]
FEATURES = list(dict.fromkeys(TREND_FEATURES + REVERSION_FEATURES + VOL_FEATURES))


@dataclass(frozen=True)
class Config:
    starting_yen: float = 1_000_000.0
    units_usd: int = 1_000
    max_notional_fraction: float = 0.50
    max_drawdown_stop: float = 0.10
    daily_loss_stop: float = 0.02
    spread_pips: float = 1.0
    slippage_pips_per_side: float = 0.2
    commission_yen_per_trade: float = 0.0
    p_threshold: float = 0.60
    volatility_cap_quantile: float = 0.80
    max_bar_gap_hours: float = 4.0
    retrain_every_bars: int = 168
    seed: int = 42


def load_ohlc(path: str | Path) -> pd.DataFrame:
    """Read OHLC CSV. UTC timestamps are mandatory in user data convention."""
    source = pd.read_csv(path)
    needed = ["time", "open", "high", "low", "close"]
    missing = [c for c in needed if c not in source]
    if missing:
        raise ValueError(f"CSV columns missing: {missing}")
    if source[needed].isna().any().any():
        raise ValueError("CSV contains missing OHLC/timestamp values")
    # Require explicit timezone to prevent accidental local-time interpretation.
    if not source["time"].astype(str).str.contains(r"(?:Z|[+-]\d\d:\d\d)$", regex=True).all():
        raise ValueError("Every timestamp must explicitly include UTC offset, e.g. 2024-01-01T00:00:00Z")
    if 'spread_pips' in source.columns:
        needed.append('spread_pips')
    d = source[needed].copy()
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="raise")
    for c in needed[1:]:
        d[c] = pd.to_numeric(d[c], errors="raise")
    if 'spread_pips' in d and (
        d['spread_pips'].isna().any() or (~np.isfinite(d['spread_pips'])).any() or
        (d['spread_pips'] < 0).any() or (d['spread_pips'] > 100).any()
    ):
        raise ValueError('spread_pips must be finite and between 0 and 100')
    if len(d) < 1000:
        raise ValueError("At least 1000 bars are needed for this demonstration")
    if not d["time"].is_monotonic_increasing or d["time"].duplicated().any():
        raise ValueError("Timestamp must be strictly increasing with no duplicates")
    if not np.isfinite(d[['open','high','low','close']].to_numpy()).all() or (d[['open','high','low','close']] <= 0).any().any():
        raise ValueError("OHLC must contain positive finite prices")
    if ((d["high"] < d[["open", "close", "low"]].max(axis=1)) |
        (d["low"] > d[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("OHLC high/low is inconsistent")
    return d.reset_index(drop=True)


def make_features(ohlc: pd.DataFrame) -> pd.DataFrame:
    """At row t, model inputs use only data known after bar t closes.

    Columns ending in *_target or *open/*time for future execution are never
    used as features by models. They exist only for supervised training/results.
    """
    d = ohlc.copy()
    c = d["close"]
    d["return_1"] = c.pct_change(1)
    d["return_3"] = c.pct_change(3)
    d["return_6"] = c.pct_change(6)
    ema_fast = c.ewm(span=8, adjust=False).mean()
    ema_slow = c.ewm(span=24, adjust=False).mean()
    d["ema_gap"] = (ema_fast - ema_slow) / c
    ma20 = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    d["zscore20"] = (c - ma20) / sd20.replace(0, np.nan)
    d["ma_slope"] = ma20.pct_change(3)
    delta = c.diff()
    gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    d["rsi14"] = (100 * gains / (gains + losses).replace(0, np.nan) - 50) / 50
    d["volatility12"] = d["return_1"].rolling(12).std()
    d["range_pct"] = (d["high"] - d["low"]) / c
    hour = d["time"].dt.hour
    d["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    d["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    d["entry_open"] = d["open"].shift(-1)
    d["exit_open"] = d["open"].shift(-2)
    d["entry_time"] = d["time"].shift(-1)
    d["exit_time"] = d["time"].shift(-2)
    d["future_return"] = d["exit_open"] / d["entry_open"] - 1
    d["abs_target"] = d["future_return"].abs()
    d["up_target"] = (d["future_return"] > 0).astype(int)
    d = d.replace([np.inf, -np.inf], np.nan)
    d = d.dropna(subset=FEATURES + ["entry_open", "exit_open", "entry_time", "exit_time", "future_return"])
    if len(d) < 900:
        raise ValueError("Too few usable feature rows")
    return d.reset_index(drop=True)


class TrendAgent:
    """Independent nonlinear direction model using trend-oriented indicators."""

    def __init__(self, seed: int):
        self.model = HistGradientBoostingClassifier(
            max_iter=65, max_leaf_nodes=12, min_samples_leaf=30,
            l2_regularization=0.5, random_state=seed
        )

    def fit(self, data: pd.DataFrame) -> None:
        self.model.fit(data[TREND_FEATURES], data["up_target"])

    def predict(self, data: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(data[TREND_FEATURES])[:, 1]


class ReversionAgent:
    """Independent direction model relying on mean-reversion indicators."""

    def __init__(self):
        self.model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=400, C=0.5))

    def fit(self, data: pd.DataFrame) -> None:
        self.model.fit(data[REVERSION_FEATURES], data["up_target"])

    def predict(self, data: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(data[REVERSION_FEATURES])[:, 1]


class VolatilityAgent:
    """Independent forecast of next holding-period absolute return."""

    def __init__(self, seed: int):
        self.model = RandomForestRegressor(
            n_estimators=55, max_depth=6, min_samples_leaf=20,
            n_jobs=1, random_state=seed
        )

    def fit(self, data: pd.DataFrame) -> None:
        self.model.fit(data[VOL_FEATURES], data["abs_target"])

    def predict(self, data: pd.DataFrame) -> np.ndarray:
        return np.maximum(0, self.model.predict(data[VOL_FEATURES]))


class CouncilAgent:
    """Conservative coordination: both directional models must agree."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.trend = TrendAgent(cfg.seed)
        self.reversion = ReversionAgent()
        self.volatility = VolatilityAgent(cfg.seed)
        self.volatility_cap = 0.0

    def fit(self, history: pd.DataFrame) -> None:
        if history["up_target"].nunique() != 2:
            raise ValueError("Training history contains only one class; cannot fit direction models")
        self.trend.fit(history)
        self.reversion.fit(history)
        self.volatility.fit(history)
        self.volatility_cap = float(history["abs_target"].quantile(self.cfg.volatility_cap_quantile))

    def decide(self, data: pd.DataFrame) -> pd.DataFrame:
        pt = self.trend.predict(data)
        pr = self.reversion.predict(data)
        pv = self.volatility.predict(data)
        th = self.cfg.p_threshold
        long = (pt >= th) & (pr >= th)
        short = (pt <= 1 - th) & (pr <= 1 - th)
        side = np.where(long, 1, np.where(short, -1, 0))
        risky = pv > self.volatility_cap
        side[risky] = 0
        return pd.DataFrame({
            "p_up_trend": pt,
            "p_up_reversion": pr,
            "predicted_abs_return": pv,
            "volatility_cap": self.volatility_cap,
            "volatility_veto": risky,
            "side": side.astype(int),
        }, index=data.index)


class RiskAgent:
    """Deterministic veto. No protective stop-loss or intrabar risk simulation."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.equity = cfg.starting_yen
        self.peak = cfg.starting_yen
        self.day = None
        self.day_open_equity = cfg.starting_yen
        self.halted = False

    def accept(self, when: pd.Timestamp, next_open: float) -> str:
        if self.halted or self.equity <= self.peak * (1 - self.cfg.max_drawdown_stop):
            return "HALTED_DRAWDOWN"
        day = when.date()
        if self.day != day:
            self.day = day
            self.day_open_equity = self.equity
        if self.equity <= self.day_open_equity * (1 - self.cfg.daily_loss_stop):
            return "DAILY_LOSS_LIMIT"
        # 1000 USD standard size; no dynamic sizing or margin/stop-out modelling.
        if self.cfg.units_usd * next_open > self.equity * self.cfg.max_notional_fraction:
            return "NOTIONAL_LIMIT"
        return "OK"

    def realize(self, pnl: float) -> None:
        self.equity += pnl
        self.peak = max(self.peak, self.equity)
        if self.equity <= self.peak * (1 - self.cfg.max_drawdown_stop):
            self.halted = True


class AuditAgent:
    """Collects prediction, veto, fill assumptions, trade, and equity trace."""

    def __init__(self):
        self.decisions: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.equity: list[dict[str, Any]] = []


def walk_forward_predictions(data: pd.DataFrame, start: int, stop: int,
                             cfg: Config, custom_agent_paths: tuple[str | Path, ...] = ()) -> pd.DataFrame:
    """Expanding history with a one-row purge between train & inference.

    At test index t, train indices satisfy k <= t-2; label for k exits at
    open(k+2), already known by completed bar t. No future label is fitted.
    """
    specs = [load_spec(p) for p in custom_agent_paths]
    if len({spec.name for spec in specs}) != len(specs):
        raise ValueError("Custom agent names must be unique")
    parts = []
    for block_start in range(start, stop, cfg.retrain_every_bars):
        block_end = min(stop, block_start + cfg.retrain_every_bars)
        history = data.iloc[:block_start - 1]
        if len(history) < 300:
            raise ValueError("Insufficient history for walk-forward model fit")
        council = CouncilAgent(cfg)
        council.fit(history)
        segment = data.iloc[block_start:block_end]
        pred = council.decide(segment)
        # Each custom model fits on exactly the same strictly historical slice.
        # Extra models may veto a candidate but NEVER create a new trade.
        if specs:
            original_side = pred["side"].to_numpy().copy()
            current_side = original_side.copy()
            for spec in specs:
                custom = CustomDirectionAgent(spec)
                custom.fit(history)
                proba = custom.predict(segment)
                pred[f"custom_{spec.name}_p_up"] = proba
                current_side = custom.veto(current_side, proba)
            pred["custom_veto"] = (original_side != 0) & (current_side == 0)
            pred["side"] = current_side.astype(int)
        pred["fit_until_utc"] = str(history["exit_time"].iloc[-1])
        pred["training_rows"] = len(history)
        parts.append(pred)
    return pd.concat(parts)


def backtest(data: pd.DataFrame, predictions: pd.DataFrame,
             start: int, stop: int, cfg: Config) -> tuple[dict, AuditAgent]:
    risk = RiskAgent(cfg)
    audit = AuditAgent()
    audit.equity.append({"time_utc": str(data.loc[start, "time"]), "equity_yen": cfg.starting_yen})
    next_eligible = start
    for i in range(start, stop):
        bar = data.loc[i]
        p = predictions.loc[i]
        side = int(p["side"])
        reason = (str(p.get('filter_reason', 'WAIT_OR_VOLATILITY_VETO'))
                  if side == 0 else 'CANDIDATE')
        if side != 0:
            if i < next_eligible:
                reason = "POSITION_ALREADY_OPEN"
            elif i + 2 >= stop:
                reason = "SEGMENT_BOUNDARY_NO_FUTURE_PRICES"
            elif ((bar["entry_time"] - bar["time"]).total_seconds() / 3600 > cfg.max_bar_gap_hours or
                  (bar["exit_time"] - bar["entry_time"]).total_seconds() / 3600 > cfg.max_bar_gap_hours):
                reason = "DATA_GAP"
            else:
                reason = risk.accept(bar["time"], float(bar["entry_open"]))
                if reason == "OK":
                    # USDJPY pip is 0.01 JPY. Spread charged once round trip;
                    # slippage on both entry and exit. Mid-price OHLC assumption.
                    pip_jpy = 0.01
                    # If spread snapshots exist, charge the *entry bar's* observed
                    # spread. It is not usable as a pre-entry signal feature.
                    entry_spread = (float(data.loc[i+1, 'spread_pips'])
                                    if 'spread_pips' in data.columns else cfg.spread_pips)
                    spread_jpy = entry_spread * pip_jpy
                    slip_jpy = cfg.slippage_pips_per_side * pip_jpy
                    cost_per_usd = spread_jpy + 2 * slip_jpy
                    price_delta = side * (float(bar["exit_open"]) - float(bar["entry_open"]))
                    gross = cfg.units_usd * price_delta
                    cost = cfg.units_usd * cost_per_usd + cfg.commission_yen_per_trade
                    pnl = gross - cost
                    risk.realize(pnl)
                    next_eligible = i + 2  # Exit occurs before next eligible bar closes.
                    audit.trades.append({
                        "signal_time_utc": str(bar["time"]),
                        "entry_time_utc": str(bar["entry_time"]),
                        "exit_time_utc": str(bar["exit_time"]),
                        "side": "BUY" if side > 0 else "SELL",
                        "units_usd": cfg.units_usd,
                        "entry_mid": float(bar["entry_open"]),
                        "exit_mid": float(bar["exit_open"]),
                        "gross_pnl_yen": round(gross, 3),
                        "estimated_cost_yen": round(cost, 3),
                        "charged_spread_pips": entry_spread,
                        "net_pnl_yen": round(pnl, 3),
                        "equity_after_yen": round(risk.equity, 3),
                        "max_dd_halt_active": risk.halted,
                    })
                    audit.equity.append({
                        "time_utc": str(bar["exit_time"]),
                        "equity_yen": float(risk.equity),
                    })
        audit.decisions.append({
            "custom_veto": bool(p.get("custom_veto", False)),
            "custom_votes_json": json.dumps({k: round(float(p[k]), 6) for k in p.index if str(k).startswith("custom_") and str(k).endswith("_p_up")}, ensure_ascii=False),
            "signal_time_utc": str(bar["time"]),
            "p_up_trend": round(float(p["p_up_trend"]), 6),
            "p_up_reversion": round(float(p["p_up_reversion"]), 6),
            "predicted_abs_return": round(float(p["predicted_abs_return"]), 7),
            "volatility_cap": round(float(p["volatility_cap"]), 7),
            "volatility_veto": bool(p["volatility_veto"]),
            "model_side": side,
            "raw_council_side": int(p.get('raw_council_side', side)),
            "filter_reason": str(p.get('filter_reason', 'NOT_APPLIED')),
            "event_veto": bool(p.get('event_veto', False)),
            "execution_result": reason,
            "fit_until_utc": p["fit_until_utc"],
        })
    profits = [t["net_pnl_yen"] for t in audit.trades]
    pos = sum(max(p, 0) for p in profits)
    neg = -sum(min(p, 0) for p in profits)
    eq = np.array([x["equity_yen"] for x in audit.equity], dtype=float)
    peaks = np.maximum.accumulate(eq)
    drawdown = eq / peaks - 1
    summary = {
        "start_utc": str(data.loc[start, "time"]),
        "end_utc": str(data.loc[stop - 1, "time"]),
        "initial_yen": cfg.starting_yen,
        "final_yen": round(risk.equity, 2),
        "net_profit_yen": round(risk.equity - cfg.starting_yen, 2),
        "return_pct": round((risk.equity / cfg.starting_yen - 1) * 100, 4),
        "max_closed_trade_drawdown_pct": round(float(-np.min(drawdown) * 100), 4),
        "trade_count": len(profits),
        "win_rate_pct": round(100 * sum(p > 0 for p in profits) / len(profits), 3) if profits else None,
        "profit_factor": round(pos / neg, 4) if neg > 0 else None,
        "permanent_drawdown_halt": risk.halted,
        "caution": "Max drawdown uses only closed-trade equity, not intrabar excursions. No real execution, leverage margin call, swap, or tax modeled.",
    }
    return summary, audit


def run(csv: str | Path, out: str | Path, cfg: Config,
        strategy: str = 'baseline', event_csv: str | Path | None = None,
        gates: TradeGates = TradeGates(),
        custom_agent_paths: tuple[str | Path, ...] = ()) -> dict:
    if strategy not in ('baseline', 'trend'):
        raise ValueError('strategy must be baseline or trend')
    d = add_market_regime(make_features(load_ohlc(csv)))
    events = load_events(event_csv) if event_csv is not None else None
    n = len(d)
    val_start = int(n * 0.60)
    test_start = int(n * 0.80)
    if val_start < 300 or test_start - val_start < 10 or n - test_start < 10:
        raise ValueError("Dataset too short for proposed chronological split")
    custom_agent_paths = tuple(custom_agent_paths)
    specs = [load_spec(path) for path in custom_agent_paths]
    validation_pred = walk_forward_predictions(d, val_start, test_start, cfg, custom_agent_paths)
    test_pred = walk_forward_predictions(d, test_start, n, cfg, custom_agent_paths)
    if strategy == 'trend':
        validation_pred = apply_gates(d, validation_pred, cfg, events, gates)
        test_pred = apply_gates(d, test_pred, cfg, events, gates)
    validation, val_audit = backtest(d, validation_pred, val_start, test_start, cfg)
    holdout, test_audit = backtest(d, test_pred, test_start, n, cfg)
    stress_cfg = replace(cfg, spread_pips=cfg.spread_pips * 2,
                         slippage_pips_per_side=cfg.slippage_pips_per_side * 2)
    # Stress doubles assumed costs; for recorded spread, multiply the
    # observed spread by 2 in a fresh COPY, never modify signal features.
    stress_data = d.copy()
    if 'spread_pips' in stress_data:
        stress_data['spread_pips'] *= 2
    val_stress, _ = backtest(stress_data, validation_pred, val_start, test_start, stress_cfg)
    test_stress, _ = backtest(stress_data, test_pred, test_start, n, stress_cfg)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    for prefix, audit in (("validation", val_audit), ("test", test_audit)):
        pd.DataFrame(audit.decisions).to_csv(directory / f"{prefix}_decisions.csv", index=False)
        pd.DataFrame(audit.trades, columns=[
            "signal_time_utc", "entry_time_utc", "exit_time_utc", "side", "units_usd",
            "entry_mid", "exit_mid", "gross_pnl_yen", "estimated_cost_yen",
            "net_pnl_yen", "charged_spread_pips", "equity_after_yen", "max_dd_halt_active"
        ]).to_csv(directory / f"{prefix}_trades.csv", index=False)
        pd.DataFrame(audit.equity).to_csv(directory / f"{prefix}_equity.csv", index=False)
    summary = {
        "description": "Research-only multi-agent chronological walk-forward evaluation, USDJPY",
        "rows_after_indicators": n,
        "train_60pct_until": str(d.loc[val_start - 1, "time"]),
        "validation_20pct": validation,
        "test_20pct": holdout,
        "strategy": strategy,
        "custom_agents": [asdict(spec) for spec in specs],
        "trade_gates": asdict(gates) if strategy == 'trend' else None,
        "event_file": str(event_csv) if event_csv else None,
        "events_loaded": 0 if events is None else len(events),
        "validation_2x_cost_stress": val_stress,
        "test_2x_cost_stress": test_stress,
        "research_screen": {
            "validation_net_positive": validation['net_profit_yen'] > 0,
            "test_net_positive": holdout['net_profit_yen'] > 0,
            "test_2x_cost_positive": test_stress['net_profit_yen'] > 0,
            "test_profit_factor_at_least_1p2": (holdout['profit_factor'] or 0) >= 1.2,
            "test_trade_count_at_least_100": holdout['trade_count'] >= 100,
            "important": 'Checks are exploratory; passing does NOT prove future expected profit.'
        },
        "config": asdict(cfg),
        "notes": [
            "Validation and test are independently initialized with virtual 1,000,000 JPY each.",
            "Each walk-forward block re-fits on strictly older data and purges 1 row before block start.",
            "Signal is computed at completed candle t; simulated entry next open; exit following open.",
            "Fixed spread/slippage assumptions are not observed market costs.",
            "No live execution. Synthetic demo is not a profitability claim.",
        ],
    }
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only USDJPY multi-agent FX backtester")
    parser.add_argument("--csv", required=True, help="UTC OHLC CSV (time,open,high,low,close)")
    parser.add_argument("--out", default="results", help="Results directory")
    parser.add_argument("--spread-pips", type=float, default=1.0)
    parser.add_argument("--slippage-pips", type=float, default=0.2)
    parser.add_argument('--ai-threshold', type=float, default=0.60)
    parser.add_argument('--min-adx', type=float, default=20.0)
    parser.add_argument('--max-spread-pips', type=float, default=1.5)
    parser.add_argument("--custom-agent", action="append", default=[],
                        help="Path to a validated JSON agent; may be supplied repeatedly. Veto only.")
    parser.add_argument("--strategy", choices=['baseline', 'trend'], default='trend',
                        help='trend: cost/regime/event gates; baseline: original 6-agent votes')
    parser.add_argument('--events-csv', default=None,
                        help='Optional release calendar with event_time_utc and known_at_utc')
    args = parser.parse_args()
    if not 0 <= args.spread_pips <= 100 or not 0 <= args.slippage_pips <= 100:
        parser.error("Costs must be nonnegative and <=100 pips")
    if not .50 < args.ai_threshold < 1:
        parser.error('AI probability threshold must be strictly between 0.5 and 1')
    if not 0 <= args.min_adx <= 100 or not 0 <= args.max_spread_pips <= 100:
        parser.error('ADX must be 0-100 and spread cap 0-100 pips')
    cfg = Config(spread_pips=args.spread_pips, slippage_pips_per_side=args.slippage_pips,
                 p_threshold=args.ai_threshold)
    gates = TradeGates(min_adx=args.min_adx, max_spread_pips=args.max_spread_pips)
    result = run(args.csv, args.out, cfg,
                 strategy=args.strategy, event_csv=args.events_csv, gates=gates,
                 custom_agent_paths=tuple(args.custom_agent))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

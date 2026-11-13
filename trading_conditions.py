"""Point-in-time trade gates for research-only USDJPY hourly backtesting.

No internet calls are made here: offline deterministic replays need archived,
publication-timestamped events and observed quote spreads, not current news.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import numpy as np
import pandas as pd

TRUSTED_DOMAINS = (
    'bls.gov', 'federalreserve.gov', 'boj.or.jp', 'cftc.gov',
    'stlouisfed.org', 'bea.gov', 'treasury.gov',
)


def trusted_url(url: str) -> bool:
    parsed = urlparse(str(url))
    host = (parsed.hostname or '').lower()
    return (parsed.scheme == 'https' and
            any(host == domain or host.endswith('.' + domain)
                for domain in TRUSTED_DOMAINS))


def parse_utc_column(values: pd.Series, name: str) -> pd.Series:
    if not values.astype(str).str.contains(r'(?:Z|[+-]\d\d:\d\d)$', regex=True).all():
        raise ValueError(f'{name} must have explicit UTC offsets')
    return pd.to_datetime(values, utc=True, errors='raise')


def load_events(path: str | Path) -> pd.DataFrame:
    """Use a verified release calendar, never scrape news retrospectively.

    known_at_utc = when the *calendar listing* was provably available,
    which is distinct from event_time_utc. Current web snapshots must not be
    used as if they were known in earlier historical backtests.
    """
    raw = pd.read_csv(path)
    expected = {'event_time_utc', 'known_at_utc', 'event', 'source_url'}
    if not expected.issubset(raw.columns):
        raise ValueError(f'Event file missing {sorted(expected - set(raw.columns))}')
    if raw.empty:
        raise ValueError('Event file is empty')
    if raw[list(expected)].isna().any().any():
        raise ValueError('Event file has null fields')
    if not raw['source_url'].map(trusted_url).all():
        raise ValueError('Event source_url must be HTTPS on approved official domains')
    raw['event_time_utc'] = parse_utc_column(raw['event_time_utc'], 'event_time_utc')
    raw['known_at_utc'] = parse_utc_column(raw['known_at_utc'], 'known_at_utc')
    if (raw['known_at_utc'] > raw['event_time_utc']).any():
        raise ValueError('Only pre-announced events are supported (known_at must be <= event)')
    return raw.sort_values('event_time_utc').reset_index(drop=True)


def add_market_regime(data: pd.DataFrame) -> pd.DataFrame:
    """Indicators for completed bar t only; no backward fill, no future bars."""
    d = data.copy()
    high, low, close = d['high'], d['low'], d['close']
    d['ema50'] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    d['ema200'] = close.ewm(span=200, adjust=False, min_periods=200).mean()
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.), index=d.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.), index=d.index)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high-prev_close).abs(), (low-prev_close).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    d['atr14_pips'] = atr / .01
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    d['adx14'] = dx.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    return d


@dataclass(frozen=True)
class TradeGates:
    min_adx: float = 20.
    max_spread_pips: float = 1.5
    max_spread_to_atr: float = .15
    min_gross_move_to_roundtrip_cost: float = 3.
    before_event_minutes: int = 60
    after_event_minutes: int = 30


def known_event_veto(signal_time, entry_time, events: pd.DataFrame, gates: TradeGates) -> bool:
    if events is None or events.empty:
        return False
    return bool(((events['known_at_utc'] <= signal_time) &
                (events['event_time_utc'] >= entry_time - pd.Timedelta(minutes=gates.after_event_minutes)) &
                (events['event_time_utc'] <= entry_time + pd.Timedelta(minutes=gates.before_event_minutes))).any())


def apply_gates(data: pd.DataFrame, predictions: pd.DataFrame, cfg,
                events: pd.DataFrame | None = None, gates: TradeGates = TradeGates()) -> pd.DataFrame:
    """Gate model decisions by technical regime, observed/assumed costs, known news.

    Gross-move screen checks magnitude relative to costs; it is NOT a claim
    that the predicted directional expected return is positive.
    """
    d = data.loc[predictions.index]
    p = predictions.copy()
    p['raw_council_side'] = p['side'].astype(int)
    p['filter_reason'] = 'PASS'
    p['event_veto'] = False
    spread = (d['spread_pips'] if 'spread_pips' in d
              else pd.Series(cfg.spread_pips, index=d.index))
    for i in p.index:
        side = int(p.at[i, 'side'])
        if side == 0:
            p.at[i, 'filter_reason'] = ('CUSTOM_AGENT_VETO' if 'custom_veto' in p and bool(p.at[i, 'custom_veto']) else 'MODEL_WAIT_OR_VOLATILITY')
            continue
        r = d.loc[i]
        trend_long = r['close'] > r['ema200'] and r['ema50'] > r['ema200']
        trend_short = r['close'] < r['ema200'] and r['ema50'] < r['ema200']
        if (not np.isfinite(r['ema200']) or not np.isfinite(r['adx14']) or
                r['adx14'] < gates.min_adx or not (trend_long if side > 0 else trend_short)):
            p.at[i, 'filter_reason'] = 'TREND_REGIME_VETO'
        elif (float(spread.loc[i]) > gates.max_spread_pips or
              not np.isfinite(r['atr14_pips']) or r['atr14_pips'] <= 0 or
              float(spread.loc[i]) > gates.max_spread_to_atr * r['atr14_pips']):
            p.at[i, 'filter_reason'] = 'SPREAD_VETO'
        else:
            estimated_roundtrip_pips = float(spread.loc[i]) + 2*cfg.slippage_pips_per_side
            forecast_abs_move_pips = (p.at[i, 'predicted_abs_return'] * r['close'] / .01)
            if forecast_abs_move_pips < gates.min_gross_move_to_roundtrip_cost * estimated_roundtrip_pips:
                p.at[i, 'filter_reason'] = 'MOVE_TOO_SMALL_FOR_COST'
            elif events is not None and known_event_veto(r['time'], r['entry_time'], events, gates):
                p.at[i, 'filter_reason'] = 'KNOWN_HIGH_IMPACT_EVENT'
                p.at[i, 'event_veto'] = True
        if p.at[i, 'filter_reason'] != 'PASS':
            p.at[i, 'side'] = 0
    return p

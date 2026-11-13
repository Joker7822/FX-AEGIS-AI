"""Public, read-only GitHub Pages FX research snapshot builder.

FRED DEXJPUS: reference rates, not tradeable bid/ask candles. Public Pages
contains no secrets, account details or broker orders. No live trading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from history20 import START, fetch_fred

PROJECT = Path(__file__).resolve().parent
DATASET = PROJECT / 'datasets' / 'fred' / 'dexjpus_daily.csv'
META = PROJECT / 'datasets' / 'fred' / 'metadata.json'


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.tmp-', delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        finally:
            pass
    tmp.replace(path)


def refresh_fred(data_file: Path = DATASET, meta_file: Path = META, *, utc_today: date | None = None) -> dict:
    """Retrieve official FRED only; maintain source hash and latest observation.

    Never fabricate a row or consider weekends as missing prices.
    """
    today = utc_today or datetime.now(timezone.utc).date()
    if today < START:
        raise ValueError('UTC date precedes requested historical start')
    raw, source = fetch_fred(start=START, end=today)
    valid = raw.dropna(subset=['close']).copy()
    valid = valid.sort_values('date')
    if len(valid) < 252:
        raise ValueError('Insufficient real reference observations')
    output = 'date,close\n' + ''.join(f"{row.date:%Y-%m-%d},{float(row.close):.6f}\n" for row in valid.itertuples())
    _atomic_text(data_file, output)
    info = {
        'source':'FRED / Federal Reserve (DEXJPUS)',
        'source_url':'https://fred.stlouisfed.org/series/DEXJPUS',
        'series':'DEXJPUS', 'kind':'daily_reference_rate_nontradeable',
        'start_requested':START.isoformat(),'end_requested':today.isoformat(),
        'start_actual':valid['date'].min().date().isoformat(),
        'end_actual':valid['date'].max().date().isoformat(),
        'rows':len(valid),
        'csv_sha256':hashlib.sha256(output.encode('utf-8')).hexdigest(),
        'source_payload_sha256':source['payload_sha256'],
        'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),
        'warnings': [
            'Published daily NY-noon USD/JPY reference data, not broker execution prices.',
            'Does not contain bid/ask spreads, intraday highs/lows or exact fill information.',
            'May be revised retrospectively; not a point-in-time historical news feed.',
        ],
    }
    _atomic_text(meta_file, json.dumps(info, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    return info


def read_reference(data_file: Path = DATASET) -> list[dict]:
    """Read strictly monotonic historical *reference* points for public visualization."""
    if not data_file.is_file():
        return []
    data = pd.read_csv(data_file)
    if list(data.columns) != ['date','close']:
        raise ValueError('Expected only date and close columns; do not use fabricated OHLC')
    dt = pd.to_datetime(data['date'], format='%Y-%m-%d', errors='raise')
    val = pd.to_numeric(data['close'], errors='raise')
    if dt.isna().any() or dt.duplicated().any() or not dt.is_monotonic_increasing:
        raise ValueError('Reference date ordering invalid')
    if val.isna().any() or not np.isfinite(val).all() or (val <= 0).any():
        raise ValueError('Reference prices invalid')
    if len(data) > 9000:
        raise ValueError('Daily dataset unexpectedly large')
    return [{'date':x.strftime('%Y-%m-%d'), 'close':float(v)} for x,v in zip(dt, val)]


def ema_series(prices: list[float], n: int) -> list[float]:
    if not 2 <= n <= 500 or not prices:
        raise ValueError('EMA period out of range')
    out = [float(prices[0])]
    alpha = 2/(n+1)
    for p in prices[1:]:
        out.append(alpha*float(p)+(1-alpha)*out[-1])
    return out


def simulate_reference(points: list[dict], *, capital: float=1_000_000,
                       fast: int=50, slow: int=200, allocation: float=0.35,
                       spread_pips: float=1.4, allow_short: bool=True) -> dict:
    """Illustrative hindsight scenario at reference closes, never executable backtest.

    Signals at close[t-1] choose notional exposure for (t-1, t].
    The first 200+ observations are a warm-up period. No forward leakage.
    Cost = per-side change in exposure (0,1,2) times estimated round-trip
    price conversion. This is explicitly a rough sensitivity estimate only.
    """
    if not (1000 <= capital <= 1_000_000_000):
        raise ValueError('Capital must be between 1k and 1bn JPY')
    if not (2 <= fast < slow <= 500):
        raise ValueError('Require 2 <= fast < slow <= 500')
    if not (0 < allocation <= 1):
        raise ValueError('Allocation must be within (0, 1]')
    if not (0 <= spread_pips <= 50):
        raise ValueError('Spread parameter out of range')
    prices = [float(p['close']) for p in points]
    if any(not math.isfinite(p) or p <= 0 for p in prices):
        raise ValueError('Invalid reference price')
    if len(prices) < slow + 2:
        raise ValueError('Insufficient observations for scenario')
    ema_fast = ema_series(prices, fast)
    ema_slow = ema_series(prices, slow)
    equity = float(capital)
    running_max = equity
    max_dd = 0.0
    prior_position = 0
    switches = 0
    records=[]
    for i in range(slow+1,len(prices)):
        prev=i-1
        side=1 if ema_fast[prev]>ema_slow[prev] else (-1 if allow_short and ema_fast[prev]<ema_slow[prev] else 0)
        transitions=abs(side-prior_position)
        # USD/JPY fractional rate change; directional exposure fractional to equity.
        # Reference-price P&L, deliberately NOT broker executed P&L.
        step = (prices[i] / prices[prev] - 1)
        cost_fraction=transitions*spread_pips*0.01/prices[prev]*allocation
        equity *= max(0.000001,1+allocation*side*step-cost_fraction)
        switches += int(transitions>0)
        running_max=max(running_max,equity)
        max_dd=max(max_dd,100*(1-equity/running_max))
        records.append({'date':points[i]['date'],'equity':round(equity,2), 'position':side})
        prior_position=side
    return {
        'currency':'JPY','first_sim_date':records[0]['date'], 'last_sim_date':records[-1]['date'],
        'start_capital':capital,'final_capital':round(equity,2),
        'pnl':round(equity-capital,2),'return_pct':round((equity/capital-1)*100,4),
        'max_drawdown_pct':round(max_dd,4),'position_changes':switches,
        'data_kind':'FRED DEXJPUS daily reference / non-executable illustration',
        'fees_assumed_pips_per_transition':spread_pips,
        'limitations':'Reference closes are not executable bid/ask prices; excludes swap, slippage spikes, margin, taxes, fills and point-in-time revisions.',
        'curve':records,
    }


def make_site(output_dir: Path, data_file: Path = DATASET, meta_file: Path = META) -> dict:
    """Build static /docs/data/research.json; front end runs offline in Pages."""
    points=read_reference(data_file)
    provenance={}
    if meta_file.is_file():
        provenance=json.loads(meta_file.read_text(encoding='utf-8'))
    if points:
        if provenance.get('csv_sha256') != hashlib.sha256(data_file.read_bytes()).hexdigest():
            raise ValueError('CSV hash does not match provenance metadata')
        default=simulate_reference(points) if len(points)>202 else None
        status='FRED_REFERENCE_AVAILABLE'
    else:
        default=None
        status='NO_VERIFIED_HISTORY'
    obj={
        'name':'FX AEGIS AI','version':'1.6',
        'data_status':status,
        'as_of_utc':datetime.now(timezone.utc).isoformat(),
        'provenance':provenance,
        'reference_daily':points,
        'default_simulation':default,
        'simulation_is_hypothetical':True,
        'no_live_trading':True,
    }
    dest=output_dir/'data'/'research.json'
    _atomic_text(dest,json.dumps(obj,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    return {'points':len(points),'site_file':str(dest),'status':status}


def main():
    ap=argparse.ArgumentParser(description='GitHub hosted historical reference + read-only simulator')
    sub=ap.add_subparsers(dest='action',required=True)
    ref=sub.add_parser('refresh'); ref.add_argument('--dataset',type=Path,default=DATASET);ref.add_argument('--metadata',type=Path,default=META)
    build=sub.add_parser('build');build.add_argument('--dataset',type=Path,default=DATASET);build.add_argument('--metadata',type=Path,default=META);build.add_argument('--site',type=Path,default=PROJECT/'docs')
    sim=sub.add_parser('simulate',help='Hypothetical, non-executable daily-reference scenario')
    sim.add_argument('--dataset',type=Path,default=DATASET)
    sim.add_argument('--out',type=Path,required=True)
    sim.add_argument('--fast',type=int,default=50)
    sim.add_argument('--slow',type=int,default=200)
    sim.add_argument('--capital',type=float,default=1_000_000)
    sim.add_argument('--allocation',type=float,default=.35)
    sim.add_argument('--spread-pips',type=float,default=1.4)
    sim.add_argument('--long-only',action='store_true')
    args=ap.parse_args()
    if args.action=='refresh': result=refresh_fred(args.dataset,args.metadata)
    elif args.action=='build':result=make_site(args.site,args.dataset,args.metadata)
    else:
        history=read_reference(args.dataset)
        result=simulate_reference(history,capital=args.capital,fast=args.fast,slow=args.slow,
                                  allocation=args.allocation,spread_pips=args.spread_pips,
                                  allow_short=not args.long_only)
        _atomic_text(args.out,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        result={k:v for k,v in result.items() if k!='curve'}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

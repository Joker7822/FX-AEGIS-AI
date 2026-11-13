"""20-year historical-data ingestion, quality gates and regime reporting.

FRED DEXJPUS is a NY-noon *reference* rate. It is NEVER reshaped into
intraday OHLC, used as an execution price, or merged into point-in-time
trading features. Hourly bid/ask broker bars must be obtained separately.
No broker order API, model modification or execution is implemented here.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

START = date(2006, 11, 13)
END = date(2026, 11, 13)
FRED_PAGE = "https://fred.stlouisfed.org/series/DEXJPUS"
FRED_GRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXJPUS"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
JAPAN_H1 = "https://www.dukascopy.jp/marketwatch/historical/"
COLS = ("time", "open", "high", "low", "close")


def write_json(path: str | Path, obj: dict) -> None:
    """Write validated JSON atomically, never silently overwrite partial output."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=p.parent,
                                         prefix='.hist20-', suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(p)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def parse_window(start: str, end: str) -> tuple[date, date]:
    left = date.fromisoformat(start)
    right = date.fromisoformat(end)
    if left >= right:
        raise ValueError('start must precede end')
    if right > datetime.now(timezone.utc).date():
        raise ValueError('end cannot be in the future (UTC)')
    return left, right


def fred_table(payload: bytes, format_hint: str = 'csv') -> pd.DataFrame:
    """Normalize official FRED CSV or V1 JSON; drop '.', keep source dates.

    Assumes the input was verified as DEXJPUS from official FRED. Callers
    importing local user files retain a separate *unverified* source label.
    """
    if format_hint == 'json':
        doc = json.loads(payload.decode('utf-8-sig'))
        observations = doc.get('observations')
        if not isinstance(observations, list):
            raise ValueError('FRED JSON missing observations list')
        df = pd.DataFrame(observations)
        date_col, value_col = 'date', 'value'
    elif format_hint == 'csv':
        df = pd.read_csv(io.BytesIO(payload), encoding='utf-8-sig', dtype=str)
        date_col = next((c for c in ('observation_date', 'DATE', 'date') if c in df.columns), None)
        value_col = 'DEXJPUS'
    else:
        raise ValueError('Unsupported FRED format')
    if date_col not in df.columns or value_col not in df.columns:
        raise ValueError('Required FRED DEXJPUS date/value columns absent')
    values = df[value_col].astype(str).str.strip()
    unknown = ~values.isin(('.', '', 'nan', 'NaN', 'NaT')) & pd.to_numeric(values, errors='coerce').isna()
    if unknown.any():
        raise ValueError('Unknown nonnumeric value in FRED rate column')
    d = pd.DataFrame({'date': pd.to_datetime(df[date_col], format='%Y-%m-%d', errors='raise'),
                      'close': pd.to_numeric(values.replace('.', np.nan), errors='coerce')})
    if d['date'].isna().any() or d['date'].duplicated().any() or not d['date'].is_monotonic_increasing:
        raise ValueError('Dates must be unique, strictly increasing and complete')
    bad = d['close'].notna() & (~np.isfinite(d['close']) | (d['close'] <= 0))
    if bad.any():
        raise ValueError('Invalid non-positive FRED price')
    return d


def crop_fred(d: pd.DataFrame, start: date = START, end: date = END) -> pd.DataFrame:
    window = d.loc[(d['date'].dt.date >= start) & (d['date'].dt.date <= end)].copy()
    if len(window) == 0:
        raise ValueError('No rows in requested date window')
    return window


def fetch_fred(method: str = 'graph', api_key: str | None = None,
               start: date = START, end: date = END, timeout: float = 30) -> tuple[pd.DataFrame, dict]:
    """Official FRED only; fail closed on HTTP errors, missing API keys, bad data."""
    if method == 'api':
        if not api_key:
            raise ValueError('FRED_API_KEY required for official API method')
        params = dict(series_id='DEXJPUS', api_key=api_key, file_type='json',
                      observation_start=start.isoformat(), observation_end=end.isoformat())
        url = FRED_API + '?' + urlencode(params)
        format_hint = 'json'
    elif method == 'graph':
        url, format_hint = FRED_GRAPH, 'csv'
    else:
        raise ValueError('Unknown FRED method')
    request = Request(url, headers={'User-Agent': 'FX-AEGIS-AI-history20/1.5', 'Accept': 'text/csv, application/json'})
    with urlopen(request, timeout=timeout) as result:
        if result.geturl().split('/')[2] not in ('fred.stlouisfed.org', 'api.stlouisfed.org'):
            raise ValueError('Unexpected data provider redirect')
        blob = result.read(8_000_001)
        if len(blob) > 8_000_000:
            raise ValueError('Unexpectedly large response')
    raw = crop_fred(fred_table(blob, format_hint), start, end)
    # URL intentionally excludes API key in stored provenance.
    provenance = {
        'source': 'FRED / Board of Governors of the Federal Reserve System',
        'series': 'DEXJPUS', 'method': method,
        'source_url': FRED_PAGE,
        'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
        'payload_sha256': hashlib.sha256(blob).hexdigest(),
        'is_tradeable_ohlc': False,
        'is_point_in_time_vintage': False,
    }
    return raw, provenance


def import_fred_file(path: str | Path, start: date = START, end: date = END) -> tuple[pd.DataFrame, dict]:
    blob = Path(path).read_bytes()
    raw = crop_fred(fred_table(blob), start, end)
    provenance = {
        'source': 'USER_SUPPLIED_FILE_UNVERIFIED',
        'series': 'DEXJPUS_CLAIMED', 'source_url': FRED_PAGE,
        'retrieved_at_utc': None, 'payload_sha256': hashlib.sha256(blob).hexdigest(),
        'is_tradeable_ohlc': False, 'is_point_in_time_vintage': False,
    }
    return raw, provenance


def daily_report(raw: pd.DataFrame, start: date = START, end: date = END,
                 provenance: dict | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    cropped = crop_fred(raw, start, end)
    present = cropped.dropna(subset=['close']).copy().reset_index(drop=True)
    if len(present) < 20:
        raise ValueError('Not enough valid daily reference observations')
    # Statistics are calculated only from *observed* reference rates, not market fills.
    present['return_1'] = present['close'].pct_change()
    present['volatility_63d_annualized'] = present['return_1'].rolling(63, min_periods=63).std() * np.sqrt(252)
    present['ma_252d'] = present['close'].rolling(252, min_periods=252).mean()
    present['regime'] = np.select([
        present['ma_252d'].isna(),
        present['close'] > present['ma_252d'],
        present['close'] < present['ma_252d'],
    ], ['INSUFFICIENT_HISTORY', 'ABOVE_252D', 'BELOW_252D'], default='AT_252D')
    returns = present.dropna(subset=['return_1']).copy()
    returns['year'] = returns['date'].dt.year
    yearly_rows = []
    for year, part in returns.groupby('year', sort=True):
        yearly_rows.append({
            'year': int(year),
            'observations': int(len(part)),
            'start_reference_jpy_per_usd': float(present.loc[present['date'] < part.iloc[0]['date'], 'close'].iloc[-1]),
            'end_reference_jpy_per_usd': float(part['close'].iloc[-1]),
            'annualized_reference_return_volatility_pct': round(float(part['return_1'].std() * np.sqrt(252) * 100), 3)
            if len(part) > 1 else None,
            'reference_rate_change_pct': None,
        })
        base = yearly_rows[-1]['start_reference_jpy_per_usd']
        yearly_rows[-1]['reference_rate_change_pct'] = round((yearly_rows[-1]['end_reference_jpy_per_usd'] / base - 1) * 100, 3)
    yearly = pd.DataFrame(yearly_rows)
    peak = present['close'].cummax()
    dd = present['close'] / peak - 1
    summary = {
        'dataset': 'USDJPY daily reference rate / NOT tradeable OHLC',
        'requested_start': start.isoformat(), 'requested_end': end.isoformat(),
        'actual_start': present['date'].min().date().isoformat(),
        'actual_end': present['date'].max().date().isoformat(),
        'rows_with_reference_rate': len(present),
        'missing_or_holiday_rows': int(cropped['close'].isna().sum()),
        'first_reference_rate': float(present['close'].iloc[0]),
        'last_reference_rate': float(present['close'].iloc[-1]),
        'daily_return_std_annualized_pct': round(float(returns['return_1'].std() * np.sqrt(252) * 100), 4),
        'max_spot_reference_rate_drawdown_pct': round(float(-dd.min() * 100), 4),
        'years_with_data': sorted(int(x) for x in present['date'].dt.year.unique()),
        'date_span_years': round((present['date'].max() - present['date'].min()).days / 365.2425, 3),
        'has_full_requested_span': (present['date'].min().date() <= start and present['date'].max().date() >= end),
        'provenance': provenance or {'source': 'UNVERIFIED'},
        'critical_limits': [
            'FRED DEXJPUS is a NY noon reference rate, not executable broker OHLC or bid/ask.',
            'No hourly candles, spreads, real order fills, or broker financing can be derived.',
            'Retrospectively revised reference data are not point-in-time vintage for historic AI signals.',
            'Statistics refer to spot reference exchange-rate movement, NOT trading strategy P&L.',
        ],
    }
    return summary, yearly, present


def save_daily_results(raw: pd.DataFrame, provenance: dict,
                       out: str | Path, start: date, end: date) -> dict:
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    summary, yearly, observations = daily_report(raw, start, end, provenance)
    # Keep sparse reference observations and the dates of missing quoted values.
    crop_fred(raw, start, end).to_csv(directory / 'fred_reference_daily.csv', index=False, date_format='%Y-%m-%d')
    yearly.to_csv(directory / 'reference_years.csv', index=False)
    observations.to_csv(directory / 'reference_regimes.csv', index=False, date_format='%Y-%m-%d')
    write_json(directory / 'history20_summary.json', summary)
    return summary


def import_hourly(input_file: str | Path, output_file: str | Path,
                  source_timezone: str | None = None,
                  time_col: str | None = None, time_format: str | None = None) -> dict:
    """Normalize hourly broker OHLC without forward filling or invented bars.

    If timestamps are timezone-naive, the user must *explicitly* declare the
    source timezone. Times with DST ambiguity fail, rather than guessing.
    """
    source = pd.read_csv(input_file, encoding='utf-8-sig')
    cols = {str(c).strip().lower(): c for c in source.columns}
    chosen = time_col or next((cols[c] for c in ('time', 'timestamp', 'gmt time', 'date') if c in cols), None)
    if chosen not in source.columns:
        raise ValueError('Specify --time-col for broker timestamp column')
    mapped = {}
    for c in ('open', 'high', 'low', 'close'):
        if c not in cols:
            raise ValueError(f'Missing {c} OHLC column')
        mapped[c] = pd.to_numeric(source[cols[c]], errors='raise')
    if 'spread_pips' in cols:
        mapped['spread_pips'] = pd.to_numeric(source[cols['spread_pips']], errors='raise')
    t = pd.to_datetime(source[chosen], format=time_format, errors='raise')
    if isinstance(t.dtype, pd.DatetimeTZDtype):
        utc = t.dt.tz_convert('UTC')
    else:
        if not source_timezone:
            raise ValueError('Naive broker timestamps: supply --source-timezone explicitly, e.g. UTC')
        utc = t.dt.tz_localize(source_timezone, ambiguous='raise', nonexistent='raise').dt.tz_convert('UTC')
    normalized = pd.DataFrame({'time': utc.dt.strftime('%Y-%m-%dT%H:%M:%SZ'), **mapped})
    validate_hourly(normalized)
    dest = Path(output_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(dest, index=False)
    return {'output': str(dest), 'rows': int(len(normalized)),
            'source_sha256': hashlib.sha256(Path(input_file).read_bytes()).hexdigest(),
            'source_timezone_declared': source_timezone,
            'spread_pips_observed_column': 'spread_pips' in normalized.columns,
            'not_verified_as_broker_data': True}


def validate_hourly(data_or_path: pd.DataFrame | str | Path, start: date = START,
                    end: date = END, minimum_monthly_bars: int = 300) -> dict:
    from main import load_ohlc
    if isinstance(data_or_path, pd.DataFrame):
        # Use the same strict checks as the backtest, including UTC offset and OHLC.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'candles.csv'
            data_or_path.to_csv(p, index=False)
            data = load_ohlc(p)
    else:
        data = load_ohlc(data_or_path)
    all_times = data['time']
    # Coverage should be measured within the requested 20-year window even
    # when the broker CSV additionally contains earlier/later observations.
    window = data.loc[(all_times >= pd.Timestamp(start, tz='UTC')) &
                      (all_times < pd.Timestamp(end, tz='UTC') + pd.Timedelta(days=1))]
    if len(window) < 1000:
        raise ValueError('Fewer than 1000 hourly bars within requested window')
    times = window['time']
    first, last = times.iloc[0], times.iloc[-1]
    diffs = times.diff().dt.total_seconds().div(3600).iloc[1:]
    hourly_ratio = float(diffs.eq(1).mean())
    if hourly_ratio < .70:
        raise ValueError('Not a plausible H1 series: below 70% adjacent 1-hour candles')
    monthly = window.groupby(times.dt.tz_localize(None).dt.to_period('M')).size()
    month_start = pd.Period(start.isoformat()[:7], freq='M') + 1
    month_end = pd.Period(end.isoformat()[:7], freq='M') - 1
    full_months = pd.period_range(month_start, month_end, freq='M')
    counts = monthly.reindex(full_months, fill_value=0)
    good_months = int((counts >= minimum_monthly_bars).sum())
    coverage = round(100 * good_months / len(full_months), 2) if len(full_months) else 0
    first_ok = first.date() <= start + pd.Timedelta(days=14) and first.date() >= start - pd.Timedelta(days=14)
    last_ok = last.date() >= end - pd.Timedelta(days=14) and last.date() <= end + pd.Timedelta(days=14)
    # Declared 'ready' does not validate authenticity of a user-supplied file.
    full = bool(first_ok and last_ok and coverage >= 95)
    return {
        'dataset': 'USER_SUPPLIED_H1_OHLC_UNVERIFIED',
        'requested_start': start.isoformat(), 'requested_end': end.isoformat(),
        'actual_start_utc': first.isoformat(), 'actual_end_utc': last.isoformat(),
        'total_bars': int(len(window)),
        'raw_input_bars': int(len(data)),
        'adjacent_1h_fraction_pct': round(hourly_ratio * 100, 2),
        'max_time_gap_hours': float(diffs.max()),
        'candidate_full_months': int(len(full_months)),
        'months_with_minimum_bars': good_months,
        'covered_full_months_pct': coverage,
        'minimum_monthly_bars': minimum_monthly_bars,
        'span_endpoints_ok': bool(first_ok and last_ok),
        'coverage_ready_for_20y_research': full,
        'spread_column_present': 'spread_pips' in window.columns,
        'is_20y_data_authenticated': False,
        'warnings': [
            'Source identity, real bid/ask, spreads and trade execution cannot be independently verified from this CSV alone.',
            'A high monthly coverage ratio does not prove candle-by-candle completeness.',
            'If spread is absent, the main backtester uses an assumed fixed spread.',
        ],
    }


def trade_years(results: str | Path, out: str | Path) -> dict:
    """Evaluate *existing* validation and holdout trade logs by calendar year.

    This does not recalculate models or use final test years to tune settings.
    """
    result_dir = Path(results)
    src = []
    for part in ('validation', 'test'):
        file = result_dir / f'{part}_trades.csv'
        if not file.is_file():
            raise FileNotFoundError(file)
        d = pd.read_csv(file)
        if not d.empty:
            required = {'exit_time_utc', 'net_pnl_yen'}
            if not required.issubset(d.columns):
                raise ValueError('Expected trade log columns not found')
            d['year'] = pd.to_datetime(d['exit_time_utc'], utc=True, errors='raise').dt.year
            d['segment'] = part
            src.append(d)
    if not src:
        raise ValueError('No trades to analyze')
    df = pd.concat(src, ignore_index=True)
    rows = []
    for (segment, year), d in df.groupby(['segment', 'year'], sort=True):
        pnls = pd.to_numeric(d['net_pnl_yen'], errors='raise')
        pos, neg = pnls[pnls > 0].sum(), -pnls[pnls < 0].sum()
        rows.append({'segment': str(segment), 'year': int(year), 'trades': int(len(d)),
                     'net_pnl_yen': round(float(pnls.sum()), 2),
                     'win_rate_pct': round(float((pnls > 0).mean() * 100), 2),
                     'profit_factor': round(float(pos/neg), 4) if neg > 0 else None})
    yearly = pd.DataFrame(rows)
    o = Path(out)
    o.mkdir(parents=True, exist_ok=True)
    yearly.to_csv(o / 'strategy_years.csv', index=False)
    summary = {
        'tested_years': yearly.to_dict(orient='records'),
        'negative_years': yearly.loc[yearly['net_pnl_yen'] < 0, ['segment', 'year']].to_dict(orient='records'),
        'holdout_untouched': True,
        'caution': 'Annual realized net P&L from trade logs; incomplete years, no open-trade DD, swaps or broker fills.',
    }
    write_json(o / 'strategy_years_summary.json', summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description='FX AEGIS AI: 20-year real-data research tools (NO live trading)')
    subs = ap.add_subparsers(dest='command', required=True)
    fred = subs.add_parser('fetch-fred', help='Download official DEXJPUS daily reference rates')
    fred.add_argument('--method', choices=('graph', 'api'), default='graph')
    fred.add_argument('--out', default='results/history20')
    local = subs.add_parser('import-fred', help='Analyze manually downloaded FRED CSV')
    local.add_argument('--csv', required=True)
    local.add_argument('--out', default='results/history20')
    imp = subs.add_parser('import-h1', help='Normalize independently obtained broker H1 CSV')
    imp.add_argument('--csv', required=True)
    imp.add_argument('--out', required=True)
    imp.add_argument('--source-timezone', default=None, help='Mandatory for timezone-naive timestamps')
    imp.add_argument('--time-col', default=None)
    imp.add_argument('--time-format', default=None, help='Example: %%d.%%m.%%Y %%H:%%M:%%S.%%f')
    audit = subs.add_parser('audit-h1', help='Check 20-year hourly coverage, not broker authenticity')
    audit.add_argument('--csv', required=True)
    audit.add_argument('--out', default='results/history20/hourly_quality.json')
    audit.add_argument('--require-20y', action='store_true', help='Nonzero exit if insufficient span / monthly coverage')
    years = subs.add_parser('trade-years', help='Report year-by-year existing backtest performance')
    years.add_argument('--results', required=True)
    years.add_argument('--out', default='results/history20')
    for command in (fred, local, audit):
        command.add_argument('--start', default=START.isoformat())
        command.add_argument('--end', default=END.isoformat())
    args = ap.parse_args()
    if args.command in ('fetch-fred', 'import-fred'):
        a, b = parse_window(args.start, args.end)
        if args.command == 'fetch-fred':
            raw, provenance = fetch_fred(args.method, os.environ.get('FRED_API_KEY'), a, b)
        else:
            raw, provenance = import_fred_file(args.csv, a, b)
        info = save_daily_results(raw, provenance, args.out, a, b)
        print(json.dumps({k:info[k] for k in ('actual_start','actual_end','rows_with_reference_rate','has_full_requested_span')}, ensure_ascii=False, indent=2))
    elif args.command == 'import-h1':
        print(json.dumps(import_hourly(args.csv, args.out, args.source_timezone,
                                      args.time_col, args.time_format), ensure_ascii=False, indent=2))
    elif args.command == 'audit-h1':
        a, b = parse_window(args.start, args.end)
        info = validate_hourly(args.csv, a, b)
        write_json(args.out, info)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if args.require_20y and not info['coverage_ready_for_20y_research']:
            raise SystemExit('20-year hourly coverage gate failed (no backtest was launched)')
    else:
        info = trade_years(args.results, args.out)
        print(json.dumps({'year_rows':len(info['tested_years']),
                          'negative_years':len(info['negative_years'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()

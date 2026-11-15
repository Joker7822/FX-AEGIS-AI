"""Export H1 ML prediction/trade CSVs as a chart annotation JSON.

RESEARCH ONLY: local file conversion; no requests, broker orders, or publication.
Predictions are the uncalibrated arithmetic mean of two historical model outputs.
No synthetic stop-loss/take-profit levels are invented.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import timezone
from pathlib import Path

import pandas as pd

from execution_lab import load_quotes

ROOT = Path(__file__).resolve().parent
SCHEMA = 'fx-aegis-h1-research-overlay-v1'
REPORT_SCHEMA = 'fx-aegis-h1-ml-walkforward-v1'
SECTIONS = ('validation', 'diagnostic_only')
MAX_TRADES = 3000
MAX_PREDS = 20000
MAX_BYTES = 4 * 1024 * 1024


def _timestamp(value: str) -> pd.Timestamp:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing UTC timestamp')
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        raise ValueError('Missing timezone in timestamp')
    t = t.tz_convert('UTC')
    if t.minute or t.second or t.microsecond or t.nanosecond:
        raise ValueError('Timestamp is not an H1 UTC boundary')
    return t


def _clock(value: str) -> str:
    return _timestamp(value).strftime('%Y-%m-%dT%H:00:00Z')


def _number(value: str, label: str, *, minimum: float = 0, maximum: float = float('inf')) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} is not numeric') from None
    if not math.isfinite(n) or not minimum <= n <= maximum:
        raise ValueError(f'{label} is outside permitted range')
    return n


def _read_csv(path: Path, expected_fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f'Missing required ML report CSV: {path.name}')
    # pandas.DataFrame([]).to_csv() may emit a single newline when there are zero trades.
    if not path.read_text(encoding='utf-8-sig').strip():
        return []
    with path.open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not set(expected_fields).issubset(reader.fieldnames or []):
            raise ValueError(f'Unexpected columns in {path.name}')
        out = list(reader)
    if any(None in row or any(not row.get(key, '').strip() for key in expected_fields)
           for row in out):
        raise ValueError(f'Missing columns or fields in {path.name}')
    return out


def _verify_report(report: dict, quote_sha: str, n: int, kind: str) -> None:
    if not isinstance(report, dict) or report.get('schema') != REPORT_SCHEMA:
        raise ValueError('Unexpected ML report schema')
    digest = report.get('report_sha256')
    unsigned = {k: v for k, v in report.items() if k != 'report_sha256'}
    raw = json.dumps(unsigned, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                     allow_nan=False).encode('utf-8')
    if not isinstance(digest, str) or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('ML report digest mismatch')
    if (report.get('quote_sha256') != quote_sha or report.get('quote_rows') != n
            or report.get('approved_for_live_trading') is not False
            or report.get('eligible_for_paper_promotion') is not False):
        raise ValueError('ML report provenance/permission mismatch')
    expected = 'SYNTHETIC_TEST_ONLY' if kind == 'SYNTHETIC_FIXTURE' else 'BROKER_USER_ATTESTED_RESEARCH_ONLY'
    if report.get('status') != expected:
        raise ValueError('ML report data-kind/status mismatch')
    if report.get('target') != 'mid_open[i+2]>mid_open[i+1] for completed-bar signal i':
        raise ValueError('Target definition is not the documented H1 label')
    if report.get('split_policy') != 'chronological_60pct_research_20pct_validation_20pct_diagnostic_only':
        raise ValueError('Unexpected chronological split')


def _validate_output(path: Path):
    resolved = path.expanduser().resolve()
    if resolved.exists():
        raise ValueError('Output exists; do not overwrite immutable research annotation')
    if ROOT == resolved or ROOT in resolved.parents:
        relative = resolved.relative_to(ROOT)
        if relative.parts[0] not in ('results', 'private_reports', 'private_quotes', 'data'):
            raise ValueError('Never write private prediction or broker data to public GitHub paths')
    if any(part in ('docs', '.github') for part in resolved.parts):
        raise ValueError('Refusing to save research annotations into a public folder')
    return resolved


def export_overlay(quotes_path: Path, provenance_path: Path, ml_dir: Path, output: Path,
                   *, allow_synthetic: bool = False) -> dict:
    """Fail closed when source, model predictions, ledger, or chart contract disagree."""
    quotes, meta, audit = load_quotes(Path(quotes_path), Path(provenance_path),
                                     allow_synthetic=allow_synthetic)
    n = len(quotes)
    quote_sha = audit['quotes_sha256']
    report_path = Path(ml_dir) / 'h1_ml_retraining_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    _verify_report(report, quote_sha, n, audit['data_kind'])
    if report.get('quote_metadata_sha256') != hashlib.sha256(Path(provenance_path).read_bytes()).hexdigest():
        raise ValueError('Quote provenance JSON no longer matches ML run')
    target = _validate_output(Path(output))
    times = list(quotes['time'])
    index = {_clock(t.isoformat()): i for i, t in enumerate(times)}
    if len(index) != n:
        raise ValueError('Duplicate quote timestamps')
    folds = {'validation': (int(n*.6), int(n*.8)),
             'diagnostic_only': (int(n*.8), n)}
    trades, predictions, dropped = [], [], 0
    observed_prediction_sides = {}
    all_ids = set()
    for section in SECTIONS:
        start, end = folds[section]
        summary = report.get('sections', {}).get(section)
        if (not isinstance(summary, dict) or summary.get('bars') != end-start
                or _clock(summary.get('start_utc')) != _clock(times[start].isoformat())
                or _clock(summary.get('end_utc')) != _clock(times[end-1].isoformat())):
            raise ValueError(f'Fold metadata mismatch: {section}')
        decisions = _read_csv(Path(ml_dir)/f'{section}_predictions.csv',
                              ('signal_index', 'signal_bar_time_utc', 'entry_bar_time_utc',
                               'exit_bar_time_utc', 'p_up_logistic', 'p_up_boost', 'side',
                               'fit_as_of_utc', 'last_train_label_exit_utc', 'last_train_signal_index'))
        ledger = _read_csv(Path(ml_dir)/f'{section}_trades.csv',
                           ('signal_bar_time_utc', 'entry_time_utc', 'exit_time_utc', 'side',
                            'entry_fill_jpy_per_usd', 'exit_fill_jpy_per_usd'))
        if (len(decisions) != summary.get('signal_predictions')
                or len(ledger) != summary.get('trades')):
            raise ValueError(f'Count mismatch in ML results: {section}')
        for d in decisions:
            i = int(d['signal_index'])
            if not start <= i < end-2 or i+2 >= n:
                raise ValueError('Prediction escaped chronological fold')
            sig = _clock(d['signal_bar_time_utc'])
            if (sig != _clock(times[i].isoformat())
                    or _clock(d['entry_bar_time_utc']) != _clock(times[i+1].isoformat())
                    or _clock(d['exit_bar_time_utc']) != _clock(times[i+2].isoformat())):
                raise ValueError('Prediction timestamps do not match source H1 bars')
            side = int(d['side'])
            if side not in (-1, 0, 1) or sig in observed_prediction_sides:
                raise ValueError('Invalid or duplicate prediction side/time')
            observed_prediction_sides[sig] = side
            if (_timestamp(d['last_train_label_exit_utc']) > times[i]
                    or _timestamp(d['fit_as_of_utc']) > times[i]
                    or int(d['last_train_signal_index'])+2 > i):
                raise ValueError('Prediction training information leaks into the future')
            p1 = _number(d['p_up_logistic'], 'Logistic probability', maximum=1)
            p2 = _number(d['p_up_boost'], 'Boost probability', maximum=1)
            if times[i+1]-times[i] != pd.Timedelta(hours=1) or times[i+2]-times[i+1] != pd.Timedelta(hours=1):
                dropped += 1  # viewer refuses noncontiguous outcome labels
                continue
            predictions.append({'signal_time': sig,
                                'probability_up': round((p1+p2)/2, 8),
                                'p_up_logistic': p1, 'p_up_boost': p2,
                                'section': section})
        for j, trade in enumerate(ledger):
            sig = _clock(trade['signal_bar_time_utc'])
            if sig not in index:
                raise ValueError('Ledger references an unknown signal bar')
            i = index[sig]
            if not start <= i < end-2 or i+2 >= n:
                raise ValueError('Trade escaped chronological fold')
            side = int(trade['side'])
            if side not in (-1, 1) or observed_prediction_sides.get(sig) != side:
                raise ValueError('Executed trade does not match a trained model signal')
            if (_clock(trade['entry_time_utc']) != _clock(times[i+1].isoformat())
                    or _clock(trade['exit_time_utc']) != _clock(times[i+2].isoformat())
                    or times[i+1]-times[i] != pd.Timedelta(hours=1)
                    or times[i+2]-times[i+1] != pd.Timedelta(hours=1)):
                raise ValueError('Ledger entry/exit violates next-H1-open trade contract')
            entry = _number(trade['entry_fill_jpy_per_usd'], 'Entry fill', minimum=0.000001)
            exit_price = _number(trade['exit_fill_jpy_per_usd'], 'Exit fill', minimum=0.000001)
            ref_entry = quotes.iloc[i+1]['ask_open' if side == 1 else 'bid_open']
            ref_exit = quotes.iloc[i+2]['bid_open' if side == 1 else 'ask_open']
            slip = report['execution_config']['slippage_pips_per_side'] * .01
            if (abs(entry - (ref_entry+side*slip)) > 0.000002
                    or abs(exit_price - (ref_exit-side*slip)) > 0.000002):
                raise ValueError('Ledger fill does not match quote and declared slippage')
            tid = ('val' if section == 'validation' else 'diag') + f'-{j+1:06d}'
            if tid in all_ids:
                raise ValueError('Duplicate generated ID')
            all_ids.add(tid)
            trades.append({'id': tid, 'side': 'long' if side==1 else 'short',
                           'signal_time': sig,
                           'entry_time': _clock(trade['entry_time_utc']),
                           'exit_time': _clock(trade['exit_time_utc']),
                           'entry_price': entry, 'exit_price': exit_price,
                           'section': section})
    if len(trades) > MAX_TRADES or len(predictions) > MAX_PREDS:
        raise ValueError('Annotation exceeds chart trade/prediction count caps')
    synthetic = audit['data_kind'] == 'SYNTHETIC_FIXTURE'
    overlay = {'schema': SCHEMA, 'quotes_sha256': quote_sha,
               'approved_for_live_trading': False,
               'execution_assumption': 'H1_OPEN_HYPOTHETICAL',
               'synthetic': synthetic, 'trades': trades, 'predictions': predictions,
               'source': {'model_report_sha256': report['report_sha256'],
                          'prediction_probability': 'UNCALIBRATED_MEAN_OF_TWO_MODELS',
                          'reported_sections': list(SECTIONS),
                          'discarded_noncontiguous_predictions': dropped,
                          'stop_loss_take_profit': 'NOT_SIMULATED_NOT_INVENTED',
                          'source_authenticity_independently_verified': False}}
    payload = json.dumps(overlay, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8') + b'\n'
    if len(payload) > MAX_BYTES:
        raise ValueError('Annotation exceeds 4 MB viewer cap')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: do not overwrite an immutable snapshot or follow a final symlink.
    with target.open('xb') as f:
        f.write(payload)
    return {'status': 'SYNTHETIC_TEST_ONLY' if synthetic else 'PRIVATE_RESEARCH_ONLY',
            'trades': len(trades), 'predictions': len(predictions),
            'skipped_noncontiguous_predictions': dropped, 'output': str(target),
            'quotes_sha256': quote_sha, 'approved_for_live_trading': False}


def main() -> None:
    parser = argparse.ArgumentParser(description='Local ML-to-chart export; no network, no live orders')
    parser.add_argument('--quotes', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--ml-results', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path('private_reports/ml_annotations.json'))
    parser.add_argument('--allow-synthetic', action='store_true')
    args = parser.parse_args()
    print(json.dumps(export_overlay(args.quotes, args.provenance, args.ml_results, args.out,
                                    allow_synthetic=args.allow_synthetic),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
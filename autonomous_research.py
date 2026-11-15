"""Offline, unattended *research* cycle: new immutable H1 -> ML refit -> evolution audit.

No network calls, orders, broker accounts, automated deployment, or paper promotion.
This module deliberately cannot be used as a live trading bot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from execution_lab import ExecutionConfig, COLUMNS, load_quotes, load_rollovers, write_json
from h1_evolution_guard import MIN_BARS, load_catalog, research_audit
from h1_ml_retrainer import RetrainConfig, run as ml_retrain
from ml_chart_overlay import export_overlay
from self_evolve import gene_key

PROJECT = Path(__file__).resolve().parent
SCHEMA = 'fx-aegis-autonomous-research-v1'
STATE_SCHEMA = 'fx-aegis-autonomous-cycle-state-v1'


def digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _prefix_hash(quotes: pd.DataFrame, count: int) -> str:
    """Stable per-H1 quote prefix digest, so append cannot rewrite former history."""
    if count < 0 or count > len(quotes):
        raise ValueError('Invalid immutable prefix length')
    h = hashlib.sha256()
    for row in quotes.iloc[:count].itertuples(index=False, name=None):
        stamp = pd.Timestamp(row[0]).isoformat()
        h.update((stamp + ',' + ','.join(format(float(x), '.10f') for x in row[1:]) + '\n').encode())
    return h.hexdigest()


def _output_directory(out: Path) -> Path:
    path = out.expanduser().resolve()
    if any(p in ('docs', '.github', '.git') for p in path.parts):
        raise ValueError('Refusing public/site/git output path')
    if path == PROJECT or PROJECT in path.parents:
        first = path.relative_to(PROJECT).parts[0]
        if first not in ('private_reports', 'results'):
            raise ValueError('Inside repository output must be ignored private_reports/ or results/')
    return path


def _load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != STATE_SCHEMA:
        raise ValueError('Invalid run-state format')
    saved = data.get('state_sha256')
    remainder = {k: v for k, v in data.items() if k != 'state_sha256'}
    if saved != digest(remainder) or data.get('status') != 'RESEARCH_ONLY_NO_PROMOTION':
        raise ValueError('Run state was modified or is unsafe')
    return data


def _write_state_atomic(path: Path, state: dict) -> None:
    data = dict(state)
    data['state_sha256'] = digest(data)
    fd, tmp = tempfile.mkstemp(prefix='.state-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


@dataclass(frozen=True)
class CyclePolicy:
    min_new_h1_bars: int = 24
    bootstrap_reps: int = 64
    seed: int = 42

    def check(self):
        if not (type(self.min_new_h1_bars) is int and 1 <= self.min_new_h1_bars <= 720
                and type(self.bootstrap_reps) is int and 64 <= self.bootstrap_reps <= 4096
                and type(self.seed) is int and 0 <= self.seed < 2 ** 32):
            raise ValueError('Invalid fixed research-cycle policy')


def run_cycle(*, quotes_path: Path, provenance_path: Path, universe_path: Path,
              out: Path, allow_synthetic: bool = False,
              policy: CyclePolicy = CyclePolicy(), ml: RetrainConfig = RetrainConfig(),
              execution: ExecutionConfig = ExecutionConfig(),
              swap_ledger: Path | None = None) -> dict:
    """One deterministic research cycle; same data is never tested twice via this ledger.

    This prevents accidental replay and historical revisions, but cannot prove the
    input file is genuine or guarantee future out-of-sample performance.
    """
    policy.check()
    ml.check()
    execution.check()
    if execution.allocation != .25:
        raise ValueError('Genome reference allocation fixed at .25')
    output = _output_directory(Path(out))
    quotes, meta, audit = load_quotes(Path(quotes_path), Path(provenance_path),
                                      allow_synthetic=allow_synthetic)
    if len(quotes) < MIN_BARS:
        raise ValueError(f'At least {MIN_BARS} H1 bars required')
    genes, catalog = load_catalog(Path(universe_path))
    if (audit['data_kind'] == 'SYNTHETIC_FIXTURE') != (catalog['selection_source_kind'] == 'SYNTHETIC_TEST_FIXTURE'):
        raise ValueError('Synthetic/real candidate catalog cannot be mixed')
    if audit['data_kind'] != 'SYNTHETIC_FIXTURE' and (catalog['selection_source_kind'] != 'FRED_DAILY_REFERENCE_NON_EXECUTABLE'
                                                    or catalog['selected_before'] != '2021-01-01'):
        raise ValueError('Real research requires frozen pre-2021 candidate search history')
    # Important: maximum historical trials must be fully represented, otherwise no claim of corrected selection.
    # Source/algorithm changes are different experiments, never a silent retrain.
    code_paths = ('autonomous_research.py', 'h1_ml_retrainer.py', 'execution_lab.py',
                  'h1_evolution_guard.py', 'overfit_guard.py', 'self_evolve.py',
                  'ml_chart_overlay.py', 'requirements-repro.txt')
    code_sha = digest({name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
                       for name in code_paths})
    policy_sha = digest({'code_sha256': code_sha, 'cycle_policy': asdict(policy), 'ml': asdict(ml),
                         'execution': asdict(execution), 'swap_ledger_sha256':
                         hashlib.sha256(Path(swap_ledger).read_bytes()).hexdigest() if swap_ledger else None})
    identity = {'quote_sha256': audit['quotes_sha256'], 'rows': len(quotes),
                'end_utc': quotes['time'].iloc[-1].isoformat(),
                'source_name': meta['source_name'], 'source_kind': audit['data_kind'],
                'candidate_sha256': catalog['payload_sha256'],
                'policy_sha256': policy_sha}
    # All inputs are validated before acquiring the single-writer directory lock.
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.cycle_lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError('Another cycle is running, or a stale lock requires manual audit') from exc
    try:
        state_file = output / 'state.json'
        previous = _load_state(state_file)
        if previous is None:
            if (output / 'runs').exists() and any((output / 'runs').iterdir()):
                raise ValueError('Orphaned run exists without valid state; refuse silent reset')
        else:
            last = previous['last']
            if last['policy_sha256'] != policy_sha:
                raise ValueError('Research policy changed: manual independent experiment required')
            if last['source_kind'] != identity['source_kind'] or last['source_name'] != identity['source_name']:
                raise ValueError('Data source changed across locked cycles')
            if len(quotes) < last['rows']:
                raise ValueError('Historical H1 record count was truncated')
            if _prefix_hash(quotes, last['rows']) != last['prefix_sha256']:
                raise ValueError('Prior H1 history was revised: cannot treat as new forward observations')
            if len(quotes) == last['rows']:
                if identity['quote_sha256'] != last['quote_sha256']:
                    raise ValueError('Same-length quote snapshot differs')
                return {'schema': SCHEMA, 'status': 'NO_NEW_H1_BARS', 'new_bars': 0,
                        'run_count': previous['run_count'], 'approved_for_live_trading': False,
                        'broker_access': 'DISABLED'}
            new_rows = len(quotes) - last['rows']
            if new_rows < policy.min_new_h1_bars:
                return {'schema': SCHEMA, 'status': 'WAIT_MORE_COMPLETED_H1_BARS',
                        'new_bars': new_rows, 'required': policy.min_new_h1_bars,
                        'run_count': previous['run_count'], 'approved_for_live_trading': False,
                        'broker_access': 'DISABLED'}
            before = previous['last']['candidate_keys']
            current = [gene_key(g) for g in genes]
            if not set(before).issubset(set(current)):
                raise ValueError('Evolution catalog dropped previously audited candidates')
            if catalog['historical_trials_total'] < last['historical_trials_total']:
                raise ValueError('Evolution exploration history was rolled back')
        run_id = digest(identity)[:24]
        runs = output / 'runs'
        final = runs / run_id
        if final.exists():
            raise ValueError('Existing immutable cycle artifact refuses overwrite')
        runs.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(dir=runs, prefix='.pending-'))
        try:
            ml_dir = tmp_dir / 'ml'
            ml_report = ml_retrain(quotes_path, provenance_path, ml_dir,
                                   allow_synthetic=allow_synthetic, cfg=ml,
                                   execution=execution, swap_ledger=swap_ledger)
            evolution = research_audit(quotes, meta, audit, genes, catalog, execution,
                                       rollovers=load_rollovers(swap_ledger), seed=policy.seed,
                                       reps=policy.bootstrap_reps)
            write_json(tmp_dir / 'evolution_audit.json', evolution)
            chart = export_overlay(Path(quotes_path), Path(provenance_path), ml_dir,
                                   tmp_dir / 'ml_annotations.json', allow_synthetic=allow_synthetic)
            if ml_report.get('approved_for_live_trading') is not False or evolution.get('approved_for_live_trading') is not False:
                raise ValueError('Forbidden model promotion flag detected')
            if evolution['quote_sha256'] != identity['quote_sha256'] or ml_report['quote_sha256'] != identity['quote_sha256']:
                raise ValueError('Report quote digest mismatch')
            screened = evolution['statistically_screened_genes_for_RESEARCH_ONLY']
            summary = {
                'schema': SCHEMA,
                'status': 'SYNTHETIC_TEST_ONLY' if audit['data_kind'] == 'SYNTHETIC_FIXTURE'
                          else 'USER_ATTESTED_BROKER_RESEARCH_ONLY',
                'data': identity,
                'cycle_number': 1 if previous is None else previous['run_count'] + 1,
                'new_observed_h1_bars': len(quotes) if previous is None else len(quotes) - previous['last']['rows'],
                'self_retraining': {'model_families': ml_report['model_families'],
                                    'validation': ml_report['sections']['validation'],
                                    'diagnostic_only': ml_report['sections']['diagnostic_only']},
                'self_evolution': {'candidate_family_size': len(genes),
                                   'historical_trials_total': catalog['historical_trials_total'],
                                   'audit_status': evolution['status'],
                                   'research_screened_count': len(screened)},
                'chart_overlay': {'hypothetical_trades': chart['trades'], 'predictions': chart['predictions']},
                'policy': asdict(policy),
            'quote_data_quality': {key: audit[key] for key in ('h1_consecutive_fraction', 'gap_count', 'max_gap_hours', 'open_spread_pips_p95')},
            'research_code_sha256': code_sha,
                'decision': 'HOLD_RESEARCH_ONLY_NO_AUTO_PROMOTION',
                'reason': 'Historical research and evolving candidates do not establish genuine forward profitability.',
                'broker_access': 'DISABLED', 'order_placement': 'DISABLED',
                'approved_for_live_trading': False,
                'eligible_for_paper_promotion': False,
            }
            summary['summary_sha256'] = digest(summary)
            write_json(tmp_dir / 'cycle_summary.json', summary)
            tmp_dir.rename(final)
            # State updates only after all immutable reports are published successfully.
            new_state = {'schema': STATE_SCHEMA, 'status': 'RESEARCH_ONLY_NO_PROMOTION',
                         'run_count': summary['cycle_number'], 'last_run_id': run_id,
                         'last': {**identity, 'prefix_sha256': _prefix_hash(quotes, len(quotes)),
                                  'candidate_keys': [gene_key(g) for g in genes],
                                  'historical_trials_total': catalog['historical_trials_total']}}
            _write_state_atomic(state_file, new_state)
            return {**summary, 'run_id': run_id, 'cycle_dir': str(final)}
        except Exception:
            # No state update, no partially published report, no automatic retry promotion.
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise
    finally:
        lock.rmdir()


def main() -> None:
    p = argparse.ArgumentParser(description='Offline, fail-closed self-evolving H1 research cycle (NO ORDERS)')
    p.add_argument('--quotes', type=Path, required=True)
    p.add_argument('--provenance', type=Path, required=True)
    p.add_argument('--universe', type=Path, required=True)
    p.add_argument('--out', type=Path, default=Path('private_reports/autonomous_research'))
    p.add_argument('--allow-synthetic', action='store_true')
    p.add_argument('--min-new-bars', type=int, default=24)
    p.add_argument('--bootstrap-reps', type=int, default=64)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--probability-threshold', type=float, default=.60)
    p.add_argument('--slippage-pips-per-side', type=float, required=True)
    p.add_argument('--commission-jpy-per-roundtrip', type=float, required=True)
    p.add_argument('--swap-ledger', type=Path, default=None)
    args = p.parse_args()
    result = run_cycle(quotes_path=args.quotes, provenance_path=args.provenance,
                       universe_path=args.universe, out=args.out, allow_synthetic=args.allow_synthetic,
                       policy=CyclePolicy(args.min_new_bars, args.bootstrap_reps, args.seed),
                       ml=RetrainConfig(probability_threshold=args.probability_threshold),
                       execution=ExecutionConfig(slippage_pips_per_side=args.slippage_pips_per_side,
                                                 commission_jpy_per_roundtrip=args.commission_jpy_per_roundtrip),
                       swap_ledger=args.swap_ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

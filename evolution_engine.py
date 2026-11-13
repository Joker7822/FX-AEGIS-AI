"""FX AEGIS AI v1.4 - sandboxed evolutionary agent search (research only).

The engine invents *new bounded combinations* of features, model families,
ensemble weights and vote thresholds. It does NOT invent provably novel ML theory,
execute generated Python, access a broker, or claim future profits.

Promoted agents can ONLY veto signals from the existing Council.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from custom_agents import ALLOWED_FEATURES
from main import Config, CouncilAgent, backtest, load_ohlc, make_features
from trading_conditions import add_market_regime, apply_gates

FAMILIES = ('logistic', 'random_forest', 'gradient_boosting')
OPERATORS = ('sum', 'difference', 'product', 'absolute_difference', 'tanh_difference')
CORE = tuple(sorted(ALLOWED_FEATURES))


@dataclass(frozen=True)
class Blueprint:
    """Data-only grammar; never allowed to embed Python code or arbitrary columns."""
    name: str
    features: tuple[str, ...]
    op: str
    left: str
    right: str
    primary: str
    secondary: str
    blend: float
    threshold: float
    seed: int = 42
    role: str = 'veto_only'
    version: int = 2

    def check(self) -> None:
        import re
        if not isinstance(self.name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{1,39}', self.name):
            raise ValueError('Invalid agent name')
        if not isinstance(self.features, tuple) or not 3 <= len(self.features) <= 9:
            raise ValueError('Feature count must be 3..9')
        if len(set(self.features)) != len(self.features) or any(x not in ALLOWED_FEATURES for x in self.features):
            raise ValueError('Unknown, future, or repeated features')
        if self.op not in OPERATORS or self.left not in self.features or self.right not in self.features:
            raise ValueError('Invalid feature-program operands')
        if self.primary not in FAMILIES or self.secondary not in FAMILIES:
            raise ValueError('Unknown estimator family')
        if type(self.blend) not in (int, float) or not np.isfinite(self.blend) or not 0.1 <= self.blend <= 0.9:
            raise ValueError('Invalid ensemble weight')
        if type(self.threshold) not in (int, float) or not np.isfinite(self.threshold) or not .51 <= self.threshold <= .80:
            raise ValueError('Invalid signal threshold')
        if type(self.seed) is not int or not 0 <= self.seed <= 2**32 - 1:
            raise ValueError('Invalid seed')
        if self.version != 2 or self.role != 'veto_only':
            raise ValueError('Only read-only veto agents version 2 are supported')

    def to_json(self) -> dict:
        return asdict(self)


def load_blueprint(path: str | Path) -> Blueprint:
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(d, dict) or set(d) != set(Blueprint.__dataclass_fields__):
        raise ValueError('Unexpected blueprint keys')
    if not isinstance(d['features'], list):
        raise ValueError('features must be JSON array')
    d['features'] = tuple(d['features'])
    b = Blueprint(**d)
    b.check()
    return b


def atomic_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.aegis-', suffix='.tmp', delete=False, encoding='utf-8') as out:
            temp = Path(out.name)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, 0o644)
        temp.replace(path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def build_features(data: pd.DataFrame, b: Blueprint) -> pd.DataFrame:
    b.check()
    x = data.loc[:, list(b.features)].copy()
    a = x[b.left].to_numpy(dtype=float)
    c = x[b.right].to_numpy(dtype=float)
    with np.errstate(over='ignore', divide='ignore', invalid='ignore'):
        if b.op == 'sum':
            composed = a + c
        elif b.op == 'difference':
            composed = a - c
        elif b.op == 'product':
            composed = a * c
        elif b.op == 'absolute_difference':
            composed = np.abs(a - c)
        else:
            composed = np.tanh(np.clip(a - c, -15, 15))
    # Numerical clipping is row-wise and does not learn from future rows.
    x['discovered_feature'] = np.clip(composed, -1e5, 1e5)
    if not np.isfinite(x.to_numpy(dtype=float)).all():
        raise ValueError('Non-finite input encountered')
    return x


def estimator(family: str, seed: int):
    if family == 'logistic':
        return make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=350, random_state=seed))
    if family == 'random_forest':
        return RandomForestClassifier(n_estimators=32, max_depth=5, min_samples_leaf=20,
                                      random_state=seed, n_jobs=1)
    if family == 'gradient_boosting':
        return HistGradientBoostingClassifier(max_iter=40, max_leaf_nodes=10,
                                               min_samples_leaf=25, early_stopping=False,
                                               random_state=seed)
    raise ValueError('Unsupported family')


class DiscoveredVetoAgent:
    """Fits two independently trained models and vetoes only Council decisions."""

    def __init__(self, blueprint: Blueprint):
        blueprint.check()
        self.blueprint = blueprint
        self.first = estimator(blueprint.primary, blueprint.seed)
        self.second = estimator(blueprint.secondary, blueprint.seed + 1)

    def fit(self, history: pd.DataFrame) -> None:
        if len(history) < 300 or history['up_target'].nunique() != 2:
            raise ValueError('At least 300 historical rows and two target classes required')
        x = build_features(history, self.blueprint)
        y = history['up_target'].astype(int)
        self.first.fit(x, y)
        self.second.fit(x, y)

    def predict(self, segment: pd.DataFrame) -> np.ndarray:
        x = build_features(segment, self.blueprint)
        a = self.first.predict_proba(x)[:, list(self.first.classes_).index(1)]
        b = self.second.predict_proba(x)[:, list(self.second.classes_).index(1)]
        return np.clip(self.blueprint.blend * a + (1 - self.blueprint.blend) * b, 0, 1)

    def veto(self, side: np.ndarray, probability: np.ndarray) -> np.ndarray:
        side = np.asarray(side, dtype=int)
        probability = np.asarray(probability, dtype=float)
        if side.shape != probability.shape or not np.isfinite(probability).all():
            raise ValueError('Malformed probability vector')
        p = self.blueprint.threshold
        return np.where(((side == 1) & (probability < p)) |
                        ((side == -1) & (probability > 1 - p)), 0, side)


def identity(b: Blueprint) -> str:
    d = b.to_json().copy()
    d.pop('name')
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def sample(rng: random.Random, name: str) -> Blueprint:
    features = tuple(sorted(rng.sample(CORE, rng.randint(4, 8))))
    left, right = rng.sample(features, 2)
    b = Blueprint(name=name, features=features, op=rng.choice(OPERATORS),
                  left=left, right=right, primary=rng.choice(FAMILIES),
                  secondary=rng.choice(FAMILIES), blend=rng.choice((.25, .5, .75)),
                  threshold=rng.choice((.55, .60, .65, .70)), seed=rng.randint(0, 99999))
    b.check()
    return b


def mutate(b: Blueprint, rng: random.Random, name: str) -> Blueprint:
    d = b.to_json()
    d['name'] = name
    change = rng.choice(('model', 'second', 'threshold', 'blend', 'op', 'features', 'operands'))
    if change == 'model':
        d['primary'] = rng.choice([x for x in FAMILIES if x != d['primary']])
    elif change == 'second':
        d['secondary'] = rng.choice([x for x in FAMILIES if x != d['secondary']])
    elif change == 'threshold':
        d['threshold'] = rng.choice((.55, .60, .65, .70, .75))
    elif change == 'blend':
        d['blend'] = rng.choice((.25, .5, .75))
    elif change == 'op':
        d['op'] = rng.choice([x for x in OPERATORS if x != d['op']])
    elif change == 'features':
        fs = set(d['features'])
        replacement = rng.choice(sorted(fs))
        fs.remove(replacement)
        fs.add(rng.choice(sorted(set(CORE) - fs)))
        d['features'] = sorted(fs)
        if d['left'] not in fs or d['right'] not in fs:
            d['left'], d['right'] = rng.sample(d['features'], 2)
    else:
        d['left'], d['right'] = rng.sample(d['features'], 2)
    d['features'] = tuple(d['features'])
    new = Blueprint(**d)
    new.check()
    return new


def crossover(a: Blueprint, b: Blueprint, rng: random.Random, name: str) -> Blueprint:
    features = tuple(sorted(set(rng.sample(a.features, min(3, len(a.features)))) |
                            set(rng.sample(b.features, min(3, len(b.features))))))
    left, right = rng.sample(features, 2)
    obj = replace(a, name=name, features=features, left=left, right=right,
                  secondary=b.secondary, blend=b.blend, threshold=rng.choice((a.threshold,b.threshold)))
    obj.check()
    return obj


def folds(n: int) -> list[tuple[int, int, int]]:
    """Disjoint chronological validation sections entirely within first 80%."""
    if n < 900:
        raise ValueError('At least 900 fully labeled feature rows are required')
    cut = [int(n * v) for v in (.42, .55, .67, .80)]
    return [(cut[i] - 2, cut[i], cut[i + 1]) for i in range(3)]


def prepare_folds(data: pd.DataFrame, cfg: Config) -> list[dict]:
    result = []
    for end_training, start, stop in folds(len(data)):
        history = data.iloc[:end_training]
        segment = data.iloc[start:stop]
        council = CouncilAgent(cfg)
        council.fit(history)
        council_pred = council.decide(segment)
        council_pred['fit_until_utc'] = str(history['exit_time'].iloc[-1])
        council_pred = apply_gates(data, council_pred, cfg)
        base, _ = backtest(data, council_pred, start, stop, cfg)
        result.append({'train':history, 'segment':segment, 'start':start, 'stop':stop,
                       'base_predictions':council_pred, 'baseline':base})
    return result


def apply_candidate(bundle: dict, b: Blueprint, cfg: Config, *, cost_factor: int = 1) -> dict:
    agent = DiscoveredVetoAgent(b)
    agent.fit(bundle['train'])
    proba = agent.predict(bundle['segment'])
    predictions = bundle['base_predictions'].copy()
    predictions['side'] = agent.veto(predictions['side'].to_numpy(), proba)
    # Existing risk and accounting path still decides final eligibility.
    tested_data = bundle['data'] if 'data' in bundle else None
    if tested_data is None:
        raise ValueError('Missing source data for backtest')
    current_cfg = cfg
    if cost_factor != 1:
        current_cfg = replace(cfg, spread_pips=cfg.spread_pips * cost_factor,
                              slippage_pips_per_side=cfg.slippage_pips_per_side * cost_factor)
        tested_data = tested_data.copy()
        if 'spread_pips' in tested_data:
            tested_data['spread_pips'] *= cost_factor
    return backtest(tested_data, predictions, bundle['start'], bundle['stop'], current_cfg)[0]


def assess(b: Blueprint, bundles: list[dict], data: pd.DataFrame, cfg: Config) -> dict:
    fold_records = []
    for bundle in bundles:
        record = dict(bundle)
        record['data'] = data
        sim = apply_candidate(record, b, cfg)
        stress = apply_candidate(record, b, cfg, cost_factor=2)
        fold_records.append({'range_utc':[str(record['segment']['time'].iloc[0]),
                                          str(record['segment']['time'].iloc[-1])],
                             'net_yen':sim['net_profit_yen'], 'baseline_net_yen':record['baseline']['net_profit_yen'],
                             'stress_2x_net_yen':stress['net_profit_yen'], 'trades':sim['trade_count'],
                             'max_dd_pct':sim['max_closed_trade_drawdown_pct'],
                             'profit_factor':sim['profit_factor']})
    total_net = round(sum(x['net_yen'] for x in fold_records), 2)
    total_baseline = round(sum(x['baseline_net_yen'] for x in fold_records), 2)
    stress_net = round(sum(x['stress_2x_net_yen'] for x in fold_records), 2)
    trades = sum(x['trades'] for x in fold_records)
    # Rank candidates by incremental performance, with a trade sample penalty.
    # Hard promotion rules below are separate from this search ranking.
    score = round(total_net - total_baseline - max(0, 20 - trades) * 50, 2)
    return {'name':b.name, 'id':identity(b), 'score':score, 'net_yen':total_net,
            'baseline_net_yen':total_baseline, 'stress_2x_net_yen':stress_net,
            'trade_count':trades,
            'positive_folds':sum(x['net_yen']>0 for x in fold_records),
            'better_than_baseline_folds':sum(x['net_yen']>x['baseline_net_yen'] for x in fold_records),
            'max_dd_pct':round(max(x['max_dd_pct'] for x in fold_records),4),
            'folds':fold_records}


def eligible(stat: dict, min_trades: int = 20) -> tuple[bool, list[str]]:
    failures=[]
    if stat['net_yen'] <= 0: failures.append('nonpositive_validation_net')
    if stat['stress_2x_net_yen'] <= 0: failures.append('nonpositive_double_cost_net')
    if stat['positive_folds'] < 2: failures.append('fewer_than_two_profitable_folds')
    if stat['better_than_baseline_folds'] < 2: failures.append('fewer_than_two_improved_folds')
    if stat['net_yen'] <= stat['baseline_net_yen']: failures.append('not_better_than_baseline')
    if stat['trade_count'] < min_trades: failures.append('insufficient_trade_sample')
    if stat['max_dd_pct'] > 5.0: failures.append('drawdown_exceeds_five_pct')
    return not failures, failures


def data_hash(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(2**20),b''):
            h.update(chunk)
    return h.hexdigest()


def discover(csv: Path, out: Path, *, population: int=6, generations: int=2,
             seed: int=42, auto_promote_paper: bool=False, min_trades: int=20) -> dict:
    if (population < 2 or population > 32 or generations < 1 or generations > 12 or
            population * generations > 96):
        raise ValueError('Search budget: population 2..32, generations 1..12, at most 96 evaluations')
    if not 1 <= min_trades <= 10000:
        raise ValueError('Invalid minimum trades')
    cfg = Config(seed=seed)
    data = add_market_regime(make_features(load_ohlc(csv)))
    all_folds = prepare_folds(data,cfg)
    rng = random.Random(seed)
    current = [sample(rng, f'Gen0_{i}') for i in range(population)]
    evaluated: dict[str,dict] = {}
    candidates: dict[str,Blueprint] = {}
    for g in range(generations):
        for candidate in current:
            key=identity(candidate)
            if key not in evaluated:
                stat=assess(candidate, all_folds, data, cfg)
                evaluated[key]=stat
                candidates[key]=candidate
        leaders=sorted(evaluated, key=lambda k:(evaluated[k]['score'],k), reverse=True)
        parents=[candidates[k] for k in leaders[:max(2,population//2)]]
        current=[]
        for j in range(population):
            if j%3 == 0:
                parent=rng.choice(parents)
                current.append(mutate(parent,rng,f'Gen{g+1}_{j}'))
            elif j%3 == 1:
                current.append(crossover(rng.choice(parents),rng.choice(parents),rng,f'Gen{g+1}_{j}'))
            else:
                current.append(sample(rng,f'Gen{g+1}_{j}'))
    leader_key=max(evaluated,key=lambda k:(evaluated[k]['score'],k))
    winner=candidates[leader_key]
    best=evaluated[leader_key]
    passed,failures=eligible(best,min_trades)
    out.mkdir(parents=True,exist_ok=True)
    candidate_path=out/'candidate_evolved.json'
    atomic_json(candidate_path, winner.to_json())
    incumbent_path=out/'incumbent_paper.json'
    incumbent_evaluation=None
    improved_incumbent=True
    if incumbent_path.exists():
        incumbent=load_blueprint(incumbent_path)
        incumbent_evaluation=assess(incumbent,all_folds,data,cfg)
        improved_incumbent=(best['net_yen'] > incumbent_evaluation['net_yen'] and
                            best['stress_2x_net_yen'] > incumbent_evaluation['stress_2x_net_yen'])
    promoted=False
    if auto_promote_paper and passed and improved_incumbent:
        atomic_json(incumbent_path,winner.to_json())
        promoted=True

    # The holdout is evaluated ONCE and NEVER participates in search, scoring,
    # incumbent comparison, or promotion. Repeated user re-optimization with
    # knowledge of this report still invalidates an "untouched" holdout.
    n=len(data); start=int(n*.80); train=data.iloc[:start-2]
    council=CouncilAgent(cfg); council.fit(train)
    p=council.decide(data.iloc[start:])
    p['fit_until_utc'] = str(train['exit_time'].iloc[-1])
    p=apply_gates(data,p,cfg)
    holdout_bundle={'train':train,'segment':data.iloc[start:],'start':start,'stop':n,
                    'base_predictions':p,'baseline':backtest(data,p,start,n,cfg)[0], 'data':data}
    holdout=apply_candidate(holdout_bundle,winner,cfg)
    holdout_stress=apply_candidate(holdout_bundle,winner,cfg,cost_factor=2)
    report={
        'project':'FX AEGIS AI', 'engine':'bounded_evolution_v1',
        'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'data_sha256':data_hash(csv), 'last_candle_utc':str(data['time'].iloc[-1]),
        'data_provenance':'USER_SUPPLIED_UNVERIFIED',
        'seed':seed,'population':population,'generations':generations,
        'candidate_count':len(evaluated),'selected_blueprint':winner.to_json(),
        'validation':best,'promotion_eligible':passed,'promotion_rejection_reasons':failures,
        'incumbent_validation':incumbent_evaluation,'improves_incumbent':improved_incumbent,
        'paper_promotion_requested':auto_promote_paper,'paper_promoted':promoted,
        'holdout_read_only':{'baseline':holdout_bundle['baseline'],'candidate':holdout,
                             'candidate_double_cost':holdout_stress},
        'limitations':[
            'Search recombines known ML families and safe mathematical operators; not a proof of new scientific algorithm discovery.',
            'No internet sources or future news enter historic features.',
            'Final 20% holdout is NEVER used for evolution or automatic promotion. Treat as consumed after viewing this report.',
            'Multiple tests and selection create overfitting risk, even with walk-forward folds.',
            'Closed-equity accounting omits intrabar drawdown, margin, swap, and real broker fills.',
            'Paper promotion changes only local JSON veto policy; real broker trading is entirely disabled.',
        ],
    }
    atomic_json(out/'evolution_report.json',report)
    atomic_json(out/'candidate_ranking.json',{'results':sorted(evaluated.values(),key=lambda x:x['score'],reverse=True)})
    return report


def drift_check(data: pd.DataFrame) -> dict:
    """Heuristic distribution shift (not a predictive success criterion)."""
    if len(data) < 400:
        return {'available':False}
    series = data['return_1'].to_numpy(dtype=float)
    former = series[-400:-200]
    recent = series[-200:]
    # Empirical CDF supremum. Pure numpy, fixed windows, no future data.
    grid = np.sort(np.concatenate((former, recent)))
    distance = float(np.max(np.abs(np.searchsorted(np.sort(former), grid, side='right') / len(former) -
                                   np.searchsorted(np.sort(recent), grid, side='right') / len(recent))))
    return {'available':True,'feature':'return_1','reference_bars':200,'recent_bars':200,
            'empirical_cdf_distance':round(distance,4),
            'research_flag':bool(distance >= 0.20),
            'warning':'0.20 is a research alert threshold, not a significance test or profit signal'}


def monitor(csv: Path, out: Path, *, population: int=6,generations:int=2,
            seed:int=42,auto_promote_paper:bool=False,min_trades:int=20)->dict:
    state_file=out/'monitor_state.json'
    previous=json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
    h=data_hash(csv)
    raw=load_ohlc(csv)
    last_time=str(raw['time'].iloc[-1])
    if previous.get('data_sha256') == h:
        return {'status':'unchanged', 'last_candle_utc':last_time}
    if previous.get('last_candle_utc') and last_time <= previous['last_candle_utc']:
        raise ValueError('Revised or non-extended history: refused automatic retraining')
    drift=drift_check(make_features(raw))
    report=discover(csv,out,population=population,generations=generations,
                    seed=seed,auto_promote_paper=auto_promote_paper,min_trades=min_trades)
    atomic_json(state_file,{'data_sha256':h,'last_candle_utc':last_time,
                            'latest_drift':drift,
                            'last_run_utc':report['created_at_utc'],
                            'last_paper_promoted':report['paper_promoted']})
    return {'status':'evaluated','paper_promoted':report['paper_promoted'],
            'candidate':report['selected_blueprint']['name'],
            'drift':drift,
            'validation_net_yen':report['validation']['net_yen'],
            'holdout_net_yen':report['holdout_read_only']['candidate']['net_profit_yen']}


def main() -> None:
    parser=argparse.ArgumentParser(description='Evolve bounded FX AI architectures; NO broker orders')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('discover','monitor'):
        p=sub.add_parser(name)
        p.add_argument('--csv',type=Path,required=True)
        p.add_argument('--out',type=Path,default=Path('results/evolution'))
        p.add_argument('--population',type=int,default=6)
        p.add_argument('--generations',type=int,default=2)
        p.add_argument('--seed',type=int,default=42)
        p.add_argument('--min-trades',type=int,default=20)
        p.add_argument('--auto-promote-paper',action='store_true',
                       help='Promote only after strict validation gates; JSON veto policy only')
    args=parser.parse_args()
    options=dict(population=args.population,generations=args.generations,seed=args.seed,
                 min_trades=args.min_trades,auto_promote_paper=args.auto_promote_paper)
    if args.command=='discover':
        result=discover(args.csv,args.out,**options)
        print(json.dumps({'candidate':result['selected_blueprint']['name'],
                          'validation':result['validation'],
                          'paper_promoted':result['paper_promoted'],
                          'report_path':str(args.out/'evolution_report.json')},ensure_ascii=False,indent=2))
    else:
        print(json.dumps(monitor(args.csv,args.out,**options),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

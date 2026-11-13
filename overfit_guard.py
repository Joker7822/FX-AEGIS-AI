"""Conservative, reproducible, non-execution statistical screening for FX AEGIS AI.

The pre-2021 FRED DEXJPUS daily reference series is NOT executable FX history.
This guard checks multiple comparisons *within the current candidate family* via
common-index, circular moving-block bootstrap. All evidence is exploratory:
bootstrap assumptions, repeated historic weekly searches, and price revisions
mean this is not proof of profits. No code here can authorize live orders.
"""
from __future__ import annotations

from dataclasses import asdict
from itertools import combinations
import math

import numpy as np

RESEARCH_START = '2010-01-01'
RESEARCH_END = '2021-01-01'  # never use repeatedly inspected 2021+ prices
SCHEMA = 'fx-aegis-overfit-guard-1'
BLOCK_LENGTH = 20
BOOTSTRAP_REPS = 512
MAX_AUDIT_CANDIDATES = 256  # fail closed, never silently discard trials


def log_returns(ind, gene, start=RESEARCH_START, end=RESEARCH_END, *, cost_pips=2.8):
    """Match self_evolve.simulate per-bar economics; completed price at t-1 only."""
    from self_evolve import _signal
    if not math.isfinite(cost_pips) or cost_pips < 0:
        raise ValueError('Cost must be finite and nonnegative')
    prices, dates = ind.prices, ind.dates
    start_at = max(gene.slow, gene.fast, gene.vol_window) + 5
    result, prior = [], 0
    for i in range(start_at, len(dates)):
        if dates[i] < start:
            continue
        if dates[i] >= end:
            break
        prev = i - 1
        side = _signal(ind, gene, prev)
        cost = abs(side-prior)*cost_pips*.01/prices[prev]*gene.allocation
        gross = gene.allocation*side*(prices[i]/prices[prev]-1)
        net_factor = 1. + gross - cost
        if not (math.isfinite(net_factor) and net_factor > 0):
            raise ValueError('Invalid return factor: cannot audit this strategy')
        result.append((dates[i], math.log(net_factor)))
        prior = side
    return result


def contiguous_folds(rows, count=8):
    """Equal-observation contiguous blocks, all strictly before 2021."""
    if len(rows) == 0 or count < 4 or len(rows) < count*60:
        raise ValueError('Insufficient dates for contiguous fold audit')
    n = len(rows)
    bounds = [i*n//count for i in range(count+1)]
    return [(bounds[i], bounds[i+1]) for i in range(count)]


def max_t_block_bootstrap(differentials: np.ndarray, *, seed: int = 42,
                          reps: int = BOOTSTRAP_REPS, block: int = BLOCK_LENGTH) -> dict:
    """Exploratory max-T test against fixed baseline with common blocks.

    H0: each candidate has no positive mean log-return improvement over the
    pre-registered baseline. Max across *all* current candidates corrects
    for selecting the best among them, conditional on candidate universe.
    Does not fix repeated searches or publication/time-series selection bias.
    """
    x = np.asarray(differentials, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] == 0 or not np.isfinite(x).all():
        raise ValueError('Expected finite observations by candidate matrix')
    n, m = x.shape
    if n < 252 or not 8 <= block <= n//4 or not 64 <= reps <= 10000:
        raise ValueError('Insufficient observations or invalid bootstrap controls')
    means = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    valid = sd > 1e-12
    scale = np.where(valid, sd, np.inf)
    observed = np.sqrt(n)*means/scale
    centered = x-means
    rng = np.random.default_rng(seed)
    counter = np.zeros(m, dtype=np.int64)
    blocks_per_rep = (n+block-1)//block
    offsets = np.arange(block, dtype=np.int64)
    # Batching bounds memory even for 256 competing models.
    for b in range(0, reps, 8):
        batch = min(8, reps-b)
        starts = rng.integers(0, n, size=(batch, blocks_per_rep))
        indices = ((starts[:,:,None]+offsets[None,None,:]) % n).reshape(batch,-1)[:,:n]
        null_means = centered[indices].mean(axis=1)
        null_max = np.max(np.sqrt(n)*null_means/scale, axis=1)
        counter += np.sum(null_max[:,None] >= observed[None,:]-1e-12, axis=0)
    adjusted = (counter+1.)/(reps+1.)
    adjusted = np.where((means<=0)|(~valid), 1., adjusted)
    return {
        'n_observations': n, 'candidate_count': m, 'replicates': reps,
        'block_length_observations': block, 'seed': seed,
        'candidate_mean_daily_log_excess': [round(float(y), 12) for y in means],
        'candidate_max_t_adjusted_p': [round(float(y), 8) for y in adjusted],
        'significance_is_provisional': True,
    }


def cscv_like_rank_instability(returns: np.ndarray) -> dict:
    """CSCV-inspired descriptive rank instability, NOT a calibrated PBO.

    Eight chronological blocks allow 70 train/test half-partitions. Using
    nonchronological combinations is only diagnostic, never an OOS test.
    """
    x = np.asarray(returns, dtype=float)
    if x.ndim != 2 or x.shape[1] < 2 or not np.isfinite(x).all():
        return {'state':'INSUFFICIENT_STRATEGY_DIVERSITY','fraction':None,'splits':0}
    folds = contiguous_folds(x, 8)
    sums = np.array([x[a:b].sum(axis=0) for a,b in folds])
    m = x.shape[1]
    unstable = 0
    trials = 0
    for training in combinations(range(8), 4):
        hold = [i for i in range(8) if i not in training]
        in_scores = sums[list(training)].sum(axis=0)
        out_scores = sums[hold].sum(axis=0)
        winner = int(np.argmax(in_scores))
        # Tie-handling: ties are not counted as rank failures.
        rank = int(np.sum(out_scores > out_scores[winner]+1e-12))+1
        unstable += int(rank > (m+1)//2)
        trials += 1
    return {'state':'DESCRIPTIVE_ONLY_NOT_PROBABILITY_OF_FUTURE_LOSS',
            'fraction':round(unstable/trials, 6), 'splits':trials,
            'folds':8,'strategy_count':m}


def chronological_reference_diagnostic(ind, *, cost_pips: float):
    """Walk-forward on 4 predeclared strategies, not post-hoc evolved candidates.

    At each block, choose a fixed-library model only using preceding blocks;
    evaluate the immediately following block without refitting on that block.
    This is an exploratory pre-2021 simulation, NOT a newly clean holdout.
    """
    from self_evolve import Genome
    fixed = (
        Genome('ema_cross', 15, 100, .25, False, 20, 2.),
        Genome('ema_cross', 50, 200, .25, True, 20, 99.),
        Genome('ema_slope', 15, 100, .20, False, 20, 2.),
        Genome('close_breakout', 20, 100, .20, False, 20, 2.),
    )
    rows = [log_returns(ind,g,cost_pips=cost_pips) for g in fixed]
    dates = [date for date,_ in rows[0]]
    if any([date for date,_ in x] != dates for x in rows[1:]):
        raise ValueError('Walk-forward candidate observation alignment failed')
    x = np.column_stack([[v for _,v in r] for r in rows])
    folds = contiguous_folds(dates, 8)
    assessments=[]
    compounded=0.
    for k in range(2,8):
        # Train on all earlier blocks. Current future block is not consulted.
        start_idx = folds[0][0]
        stop_idx = folds[k][0]
        trained = x[start_idx:stop_idx].sum(axis=0)
        choice = int(np.argmax(trained))
        a,b=folds[k]
        future_log = float(x[a:b,choice].sum())
        compounded += future_log
        assessments.append({'fold':k+1,'train_through':dates[a-1],
                            'test_start':dates[a],'test_end':dates[b-1],
                            'chosen_fixed_library_index':choice,
                            'reference_return_pct':round(100*math.expm1(future_log),6)})
    return {'state':'EXPLORATORY_WALK_FORWARD_PRE2021_ONLY',
            'fixed_library_size':len(fixed),'periods':assessments,
            'compounded_reference_return_pct':round(100*math.expm1(compounded),6),
            'used_to_select_evolved_candidate':False}


def audit_candidates(ind, genes, *, base_cost: float = 1.4, seed: int = 42,
                     total_search_trials: int | None = None, reps: int = BOOTSTRAP_REPS) -> dict:
    """Audit entire unique candidate universe. Fail closed on incomplete record."""
    from self_evolve import Genome, gene_key
    if not genes or len(genes) > MAX_AUDIT_CANDIDATES:
        return {'schema':SCHEMA,'status':'INCOMPLETE_CANDIDATE_UNIVERSE',
                'approved_for_live_trading':False,'passed_genes':[]}
    if not math.isfinite(base_cost) or base_cost < 0:
        raise ValueError('Invalid base cost')
    unique = {gene_key(g):g for g in genes}
    if len(unique) != len(genes):
        return {'schema':SCHEMA,'status':'DUPLICATE_OR_MISSING_TRIALS',
                'approved_for_live_trading':False,'passed_genes':[]}
    ordered = sorted(unique.items())
    baseline = Genome('ema_cross',15,100,.25,False,20,2.)
    price_cost = base_cost*2
    base_series = log_returns(ind,baseline,cost_pips=price_cost)
    keys = [k for k,_ in ordered]
    date_axis = [d for d,_ in base_series]
    if not date_axis or date_axis[-1] >= RESEARCH_END:
        raise ValueError('Research period boundary violated')
    series=[]
    for _, gene in ordered:
        recs=log_returns(ind,gene,cost_pips=price_cost)
        if [d for d,_ in recs] != date_axis:
            raise ValueError('Candidate series does not align with benchmark')
        series.append([v for _,v in recs])
    returns = np.array(series,dtype=np.float64).T
    benchmark = np.asarray([v for _,v in base_series])
    vs_benchmark=returns-benchmark[:,None]
    test=max_t_block_bootstrap(vs_benchmark,seed=seed,reps=reps)
    n=len(keys)
    searched = total_search_trials if total_search_trials is not None else n
    if not isinstance(searched,int) or searched<n:
        raise ValueError('Search trial history is incomplete')
    # Previous weekly exploration outcomes are not retained as a complete tested
    # universe, so current-batch maxT cannot certify all-time selection.
    family_complete = searched == n
    passed=[]
    candidate_audits=[]
    for i,key in enumerate(keys):
        p=test['candidate_max_t_adjusted_p'][i]
        # Explicitly fail closed if family history exceeds current batch.
        ok=bool(family_complete and p<=0.05 and test['candidate_mean_daily_log_excess'][i]>0)
        if ok:passed.append(key)
        candidate_audits.append({'gene':asdict(ordered[i][1]),'adjusted_p':p,
                                 'mean_daily_log_excess':test['candidate_mean_daily_log_excess'][i],
                                 'statistical_gate':ok})
    return {'schema':SCHEMA,'status':'SCREENED_CURRENT_FAMILY' if family_complete else 'HISTORIC_TRIAL_FAMILY_INCOMPLETE',
            'research_start':date_axis[0],'research_end':date_axis[-1],
            'reference_benchmark':asdict(baseline),
            'tested_candidates_current':n,'tracked_search_trials_total':searched,
            'bootstrap':test,'candidate_tests':candidate_audits,
            'rank_instability':cscv_like_rank_instability(returns),
            'walk_forward_fixed_library':chronological_reference_diagnostic(ind,cost_pips=price_cost),
            'passed_genes':passed,'approved_for_live_trading':False,
            'warning':'Daily reference-rate, repeated historic model selection; not an executable backtest or future-profit estimate.'}

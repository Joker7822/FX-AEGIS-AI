"""FX AEGIS AI v2.1: research-only full-universe H1 Bid/Ask statistical audit.

This evaluates previously enumerated strategy genomes on a broker-supplied H1
Bid/Ask dataset, not on FRED's non-executable daily reference prices.
Never sends orders and NEVER promotes a candidate to live or paper trading.

The last chronological fifth is a descriptive diagnostic: once inspected it
is no longer a pristine holdout. All selected candidates remain research-only.
CSV hashes are integrity checks, NOT proof that an external broker is genuine.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

from execution_lab import ExecutionConfig, load_quotes, load_rollovers, simulate, write_json
from overfit_guard import max_t_block_bootstrap, cscv_like_rank_instability
from repro_lab import canonical, sha256
from self_evolve import Genome, Indicators, _signal, gene_key

SCHEMA = "fx-aegis-h1-statistical-execution-audit-v1"
CATALOG_SCHEMA = "fx-aegis-h1-evolution-candidate-universe-v1"
BASELINE = Genome('ema_cross', 15, 100, .25, False, 20, 2.)
MAX_CANDIDATES = 128
MIN_BARS = 1200


def catalog_payload(genes: list[Genome], *, source_kind: str,
                    historical_trials_total: int, selected_before: str) -> dict:
    if not genes or len(genes) > MAX_CANDIDATES:
        raise ValueError("Missing or overlarge full candidate universe")
    keys = [gene_key(g) for g in genes]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate candidates in full candidate universe")
    if not isinstance(historical_trials_total, int) or historical_trials_total < len(keys):
        raise ValueError("Historic search trial count is invalid")
    p = {"schema": CATALOG_SCHEMA, "selection_source_kind": source_kind,
         "selected_before": selected_before, "historical_trials_total": historical_trials_total,
         "candidates": [asdict(g) for _,g in sorted(zip(keys,genes))],
         "paper_only": True, "approved_for_live_trading": False}
    p["payload_sha256"] = sha256(canonical(p))
    return p


def load_catalog(path: Path) -> tuple[list[Genome], dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Candidate catalog must be an object")
    saved = obj.get("payload_sha256")
    other = {k:v for k,v in obj.items() if k != "payload_sha256"}
    if obj.get("schema") != CATALOG_SCHEMA or saved != sha256(canonical(other)):
        raise ValueError("Candidate catalog hash/schema mismatch")
    if obj.get("approved_for_live_trading") is not False or obj.get("paper_only") is not True:
        raise ValueError("Unsafe or mismatched candidate catalog")
    raw = obj.get("candidates")
    if not isinstance(raw,list) or not 1 <= len(raw) <= MAX_CANDIDATES:
        raise ValueError("Missing/too many audited candidates")
    genes=[]
    for g in raw:
        if not isinstance(g,dict) or set(g) != set(Genome.__dataclass_fields__):
            raise ValueError("Unexpected or missing genome fields")
        genes.append(Genome(**g))
    if len({gene_key(g) for g in genes}) != len(genes):
        raise ValueError("Candidate catalog contains duplicates")
    total = obj.get("historical_trials_total")
    if type(total) is not int or total < len(genes):
        raise ValueError("Historical trial count is less than candidates")
    return genes, obj


def fixture_catalog(path: Path) -> dict:
    genes=[BASELINE, Genome('ema_cross', 50, 200, .25, True,20,99.),
           Genome('ema_slope', 15, 100, .2, False,20,2.),
           Genome('close_breakout',20,100,.2,False,20,2.)]
    p=catalog_payload(genes,source_kind='SYNTHETIC_TEST_FIXTURE',
                      historical_trials_total=len(genes), selected_before='TEST_ONLY')
    write_json(path,p)
    return p


def signal_book(quotes: pd.DataFrame, gene: Genome, start: int, stop: int, *, indicators: Indicators | None = None) -> dict[int,int]:
    """Signal uses only closed bar i; both entry(i+1), exit(i+2) are in the same fold."""
    ind=indicators if indicators is not None else Indicators([
        {'date':t.isoformat(),'close':float(c)}
        for t,c in zip(quotes['time'], (quotes['bid_close']+quotes['ask_close'])/2)
    ])
    warm=max(gene.slow, gene.fast, gene.vol_window)+5
    return {i:_signal(ind,gene,i) for i in range(max(start,warm),max(start,stop-2))}


def fold_log_returns(quotes: pd.DataFrame, gene: Genome, cfg: ExecutionConfig,
                     start: int, stop: int, *, rollovers=None, indicators: Indicators | None = None) -> tuple[np.ndarray,dict]:
    """Reset capital and stop state on fold boundary; no trade crosses folds."""
    book=signal_book(quotes,gene,start,stop,indicators=indicators)
    r=simulate(quotes,book,cfg,rollovers=rollovers)
    clock={t.isoformat():i for i,t in enumerate(quotes['time'])}
    by_exit: dict[int,list[dict]]={}
    for trade in r['ledger']:
        idx=clock[trade['exit_time_utc']]
        if not start <= idx < stop:
            raise AssertionError("Trade crossed a frozen fold boundary")
        by_exit.setdefault(idx,[]).append(trade)
    values=np.zeros(stop-start, dtype=float)
    equity=cfg.capital_jpy
    for index in range(start,stop):
        for trade in by_exit.get(index,[]):
            new_equity=equity+float(trade['net_pnl_jpy'])
            if new_equity <= 0 or not math.isfinite(new_equity):
                raise ValueError("Audit cannot use invalid capital path")
            values[index-start] += math.log(new_equity/equity)
            equity=new_equity
    if abs(equity-float(r['final_capital_jpy'])) > .02:
        raise AssertionError("H1 trade-ledger returns disagree with original engine")
    detail={"return_pct":round(100*math.expm1(float(values.sum())),6),
            "max_closed_equity_drawdown_pct":r['max_closed_equity_drawdown_pct'],
            "trades":r['trades'],"skips_by_reason":r['skips_by_reason']}
    return values,detail


def boundaries(n:int)->dict[str,tuple[int,int]]:
    if n < MIN_BARS:
        raise ValueError(f"At least {MIN_BARS} H1 bars required for frozen folds")
    a,b=int(n*.6),int(n*.8)
    return {"research":(0,a),"validation":(a,b),"diagnostic_only":(b,n)}


def research_audit(quotes: pd.DataFrame, meta: dict, quote_audit: dict,
                   genes: list[Genome], catalog: dict, cfg: ExecutionConfig, *,
                   rollovers=None, seed:int=42, reps:int=128)->dict:
    """Full candidate-family comparison with chronological, reset folds.

    The diagnostic fifth is NEVER used in any gate. The current H1 audit is
    not a fresh-forward experiment and cannot authorize paper/live promotion.
    """
    cfg.check()
    if len(quotes) < MIN_BARS:
        raise ValueError("Insufficient H1 bars")
    if cfg.allocation != BASELINE.allocation:
        # Compare at each genome's recorded allocation, not a hidden override.
        raise ValueError("Audit reference allocation must remain 0.25")
    if not genes:
        raise ValueError("Candidate family cannot be empty")
    bound=boundaries(len(quotes))
    data_kind=quote_audit['data_kind']
    if data_kind == 'SYNTHETIC_FIXTURE' and catalog['selection_source_kind'] != 'SYNTHETIC_TEST_FIXTURE':
        raise ValueError("Fixture must use an explicitly fixture-only candidate catalog")
    if data_kind != 'SYNTHETIC_FIXTURE' and (catalog['selection_source_kind'] != 'FRED_DAILY_REFERENCE_NON_EXECUTABLE'
                                            or catalog['selected_before'] != '2021-01-01'):
        raise ValueError("Real H1 audit requires archived FRED-selection candidate universe fixed before 2021")
    indicators=Indicators([{'date':t.isoformat(),'close':float(c)}
                           for t,c in zip(quotes['time'], (quotes['bid_close']+quotes['ask_close'])/2)])
    models=[BASELINE]+[g for g in genes if gene_key(g)!=gene_key(BASELINE)]
    per_model={}
    for model in models:
        by_fold={}
        for section,(a,b) in bound.items():
            cfg_gene=ExecutionConfig(**{**asdict(cfg),"allocation":model.allocation})
            r,detail=fold_log_returns(quotes,model,cfg_gene,a,b,rollovers=rollovers,indicators=indicators)
            by_fold[section]={"returns":r,"metrics":detail}
            if section=='validation':
                stressed={}
                for mult in (2,5):
                    stress=ExecutionConfig(**{**asdict(cfg_gene),
                        "slippage_pips_per_side":cfg.slippage_pips_per_side*mult,
                        "commission_jpy_per_roundtrip":cfg.commission_jpy_per_roundtrip*mult})
                    _,d=fold_log_returns(quotes,model,stress,a,b,rollovers=rollovers,indicators=indicators)
                    stressed[str(mult)]=d
                by_fold[section]['cost_stress']=stressed
        per_model[gene_key(model)]=by_fold
    bm=per_model[gene_key(BASELINE)]
    candidate_keys=[gene_key(g) for g in genes]
    train=np.column_stack([per_model[k]['research']['returns'] for k in candidate_keys])
    baseline=bm['research']['returns']
    bootstrap=max_t_block_bootstrap(train-baseline[:,None],seed=seed,reps=reps,block=20)
    historical_complete=catalog['historical_trials_total']==len(genes)
    selected=[]
    evals=[]
    for idx,g in enumerate(genes):
        blocks=per_model[gene_key(g)]
        val=blocks['validation']['metrics']; base_val=bm['validation']['metrics']
        stresses=blocks['validation']['cost_stress']
        p=bootstrap['candidate_max_t_adjusted_p'][idx]
        audit_pass=(historical_complete and data_kind!='SYNTHETIC_FIXTURE'
                    and p<=.05 and bootstrap['candidate_mean_daily_log_excess'][idx]>0
                    and val['return_pct']>0 and val['return_pct']>base_val['return_pct']
                    and val['trades']>=10 and val['max_closed_equity_drawdown_pct']<=8
                    and all(v['return_pct']>0 for v in stresses.values()))
        if audit_pass:
            selected.append(gene_key(g))
        evals.append({'genome':asdict(g), 'max_t_adjusted_p':p,
                      'research':blocks['research']['metrics'], 'validation':val,
                      'validation_stress_2x_5x':stresses,
                      'diagnostic_only':blocks['diagnostic_only']['metrics'],
                      'research_screen_pass':bool(audit_pass)})
    # A descriptive rank measure on research only. Never used for selection.
    rank=cscv_like_rank_instability(train) if train.shape[1]>=2 else {'state':'NOT_APPLICABLE'}
    result={
        'schema':SCHEMA,'quote_source_kind':data_kind,'quote_source':meta['source_name'],
        'quote_sha256':quote_audit['quotes_sha256'],
        'universe_sha256':catalog['payload_sha256'],
        'historical_trials_total':catalog['historical_trials_total'],
        'current_unique_candidates':len(genes),
        'status':('SYNTHETIC_TEST_ONLY' if data_kind=='SYNTHETIC_FIXTURE'
                  else ('HISTORICAL_CANDIDATE_UNIVERSE_INCOMPLETE' if not historical_complete
                        else ('RESEARCH_SCREEN_PASSED_NOT_FORWARD_APPROVED' if selected
                              else 'NO_ELIGIBLE_H1_RESEARCH_CANDIDATE'))),
        'source_authenticity_independently_verified':False,
        'real_h1_data_verified_by_quote_hash_only':data_kind!='SYNTHETIC_FIXTURE',
        'h1_bidask_ohlc_provided':data_kind!='SYNTHETIC_FIXTURE',
        'fold_policy':'chronological_60_20_20_no_cross_boundary_trades',
        'fold_dates':{k:{'start':quotes.iloc[a]['time'].isoformat(),
                          'end_inclusive':quotes.iloc[b-1]['time'].isoformat(),
                          'bars':b-a} for k,(a,b) in bound.items()},
        'research_bootstrap':bootstrap, 'research_rank_instability_diagnostic':rank,
        'baseline_gene':asdict(BASELINE),
        'baseline_by_fold':{k:v['metrics'] for k,v in bm.items()},
        'candidate_evaluations':evals,
        'statistically_screened_genes_for_RESEARCH_ONLY':selected,
        'eligible_for_paper_promotion':False,
        'approved_for_live_trading':False,
        'broker_connection':'DISABLED',
        'limitations':[
            'No live or paper orders; a passed screen is NOT a trade recommendation.',
            'Last 20% is descriptive only, never used to accept a candidate; it loses pristine status after inspection.',
            'Quote hash does not prove market data source authenticity or licensing rights.',
            'H1 bar opens cannot represent tick-level liquidity, margin call mechanics or realized execution.',
            'Spread is already encoded in Bid/Ask; slippage and commission are stressed, not spread counted twice.',
            'Daily financing without an exact broker swap ledger is vetoed near rollover; do not infer zero swap.',
            'Historic model-family searches absent from catalog block selection.',
        ]}
    result['report_sha256']=sha256(canonical(result))
    return result


def main():
    p=argparse.ArgumentParser(description='Full-universe H1 Bid/Ask self-evolution audit; NEVER issues orders')
    sub=p.add_subparsers(dest='action',required=True)
    fx=sub.add_parser('fixture-catalog',help='Create explicitly synthetic-only comparison catalog')
    fx.add_argument('--out',type=Path,required=True)
    au=sub.add_parser('audit',help='Screen registered genomes on H1 Bid/Ask quotes')
    au.add_argument('--quotes',type=Path,required=True)
    au.add_argument('--provenance',type=Path,required=True)
    au.add_argument('--universe',type=Path,required=True)
    au.add_argument('--allow-synthetic',action='store_true')
    au.add_argument('--swap-ledger',type=Path)
    au.add_argument('--slippage-pips-per-side',type=float,required=True)
    au.add_argument('--commission-jpy-per-roundtrip',type=float,required=True)
    au.add_argument('--max-spread-pips',type=float,default=2.)
    au.add_argument('--seed',type=int,default=42)
    au.add_argument('--bootstrap-reps',type=int,default=128)
    au.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.action=='fixture-catalog':
        o=fixture_catalog(a.out)
        print(json.dumps({'catalog':str(a.out),'count':len(o['candidates']),'test_only':True}))
        return
    quotes,meta,q_audit=load_quotes(a.quotes,a.provenance,allow_synthetic=a.allow_synthetic)
    genes,catalog=load_catalog(a.universe)
    cfg=ExecutionConfig(max_spread_pips=a.max_spread_pips,
                       slippage_pips_per_side=a.slippage_pips_per_side,
                       commission_jpy_per_roundtrip=a.commission_jpy_per_roundtrip)
    rollovers=load_rollovers(a.swap_ledger)
    report=research_audit(quotes,meta,q_audit,genes,catalog,cfg,
                          rollovers=rollovers,seed=a.seed,reps=a.bootstrap_reps)
    write_json(a.out,report)
    print(json.dumps({'status':report['status'],'screened':len(report['statistically_screened_genes_for_RESEARCH_ONLY']),
                      'trading_enabled':False,'report':str(a.out)},ensure_ascii=False))

if __name__=='__main__':
    main()

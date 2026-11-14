"""Locked-model, newly-observed H1 Bid/Ask paper evaluator. NO broker orders.

A freeze records the wall-clock lock time, immutable training prefix hash,
versions, and model parameter settings. Re-evaluations refit from exactly that
frozen prefix, never from newly available data. Only observations whose bar
*opened after the freeze* are eligible as forward observations; opening/closing
quotes for entry and exit must already be in the supplied dataset.

Paper simulation remains idealized: H1 opens are not guaranteed executable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from execution_lab import COLUMNS, ExecutionConfig, load_quotes, simulate, write_json
from h1_ml_retrainer import RetrainConfig, _fit_agents, causal_design

SCHEMA = "fx-aegis-locked-paper-v1"
MODULES = ("forward_paper.py", "h1_ml_retrainer.py", "execution_lab.py")
PROJECT = Path(__file__).resolve().parent


def _sha(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _code_hash() -> str:
    h=hashlib.sha256()
    for name in MODULES:
        h.update(name.encode("utf-8"));h.update((PROJECT/name).read_bytes())
    return h.hexdigest()


def _row_prefix_hash(quotes: pd.DataFrame, count: int) -> str:
    h = hashlib.sha256()
    for row in quotes.iloc[:count][COLUMNS].itertuples(index=False, name=None):
        h.update(str(row[0].isoformat()).encode("utf-8"))
        h.update(("," + ",".join(format(float(n), ".10f") for n in row[1:]) + "\n").encode("utf-8"))
    return h.hexdigest()


def _locked_training(quotes: pd.DataFrame, count: int, cfg: RetrainConfig):
    x,y,valid=causal_design(quotes.iloc[:count].copy())
    idx=np.flatnonzero(valid.iloc[:count-2].to_numpy())
    if len(idx) > cfg.max_training_bars:
        idx=idx[-cfg.max_training_bars:]
    if len(idx) < cfg.min_training_bars:
        raise ValueError("Not enough label-matured H1 examples for frozen model")
    labels=y.iloc[idx]
    if labels.nunique()!=2 or min(labels.value_counts())<20:
        raise ValueError("Frozen model labels are degenerate")
    models=_fit_agents(x,y,idx,cfg.seed)
    return models, x, idx


def freeze(quotes_path: Path, provenance: Path, lock_path: Path, *,
           cfg: RetrainConfig = RetrainConfig(), execution: ExecutionConfig = ExecutionConfig(),
           allow_synthetic: bool=False, _test_asof:datetime|None=None) -> dict:
    cfg.check();execution.check()
    if lock_path.exists():
        raise ValueError("Freeze lock already exists; preserve immutable research trail")
    quotes,meta,audit=load_quotes(quotes_path,provenance,allow_synthetic=allow_synthetic)
    if _test_asof is not None and not allow_synthetic:
        raise ValueError("Clock override is test-only for synthetic data")
    now=_test_asof or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Freeze clock must be timezone aware")
    now=now.astimezone(timezone.utc)
    if quotes.iloc[-1]["time"].to_pydatetime()+timedelta(hours=1)>now:
        raise ValueError("The last H1 bar is not complete as of model freeze")
    _,_,idx=_locked_training(quotes,len(quotes),cfg)
    record={"schema":SCHEMA,"frozen_at_utc":now.isoformat(),
            "frozen_last_bar_time_utc":quotes.iloc[-1]["time"].isoformat(),
            "frozen_rows":len(quotes),"prefix_sha256":_row_prefix_hash(quotes,len(quotes)),
            "source_kind":meta["source_kind"],"source_name":meta["source_name"],
            "source_license_scope":meta["license_scope"],
            "frozen_quote_sha256":audit["quotes_sha256"],
            "last_label_matured_at_utc":quotes.iloc[int(idx[-1])+2]["time"].isoformat(),
            "matured_train_samples":len(idx),
            "model_cfg":asdict(cfg),"execution_cfg":asdict(execution),
            "code_sha256":_code_hash(),"numpy_version":np.__version__,
            "pandas_version":pd.__version__,"sklearn_version":sklearn.__version__,
            "paper_only":True,"approved_for_live_trading":False}
    record["lock_sha256"]=_sha(record)
    write_json(lock_path,record)
    return record


def evaluate(quotes_path:Path, provenance:Path, lock_path:Path, out:Path, *,
             allow_synthetic:bool=False) -> dict:
    record=json.loads(lock_path.read_text(encoding="utf-8"))
    expected=record.pop("lock_sha256",None)
    if record.get("schema")!=SCHEMA or expected!=_sha(record):
        raise ValueError("Paper freeze lock integrity failed")
    if record.get("code_sha256")!=_code_hash() or record.get("numpy_version")!=np.__version__ or record.get("pandas_version")!=pd.__version__ or record.get("sklearn_version")!=sklearn.__version__:
        raise ValueError("Frozen model source or dependency version changed; cannot silently replay")
    cfg=RetrainConfig(**record["model_cfg"]); ex=ExecutionConfig(**record["execution_cfg"])
    cfg.check();ex.check()
    quotes,meta,audit=load_quotes(quotes_path,provenance,allow_synthetic=allow_synthetic)
    n=int(record["frozen_rows"])
    if len(quotes)<n or _row_prefix_hash(quotes,n)!=record["prefix_sha256"]:
        raise ValueError("Frozen training quote prefix changed or is incomplete")
    if meta["source_kind"]!=record["source_kind"] or meta["source_name"]!=record["source_name"] or meta["license_scope"]!=record["source_license_scope"]:
        raise ValueError("Provider/source/license provenance changed")
    if record.get("paper_only") is not True or record.get("approved_for_live_trading") is not False:
        raise ValueError("Unsafe trading permissions in paper lock")
    now=datetime.now(timezone.utc)
    fully_completed=quotes.loc[(quotes["time"]+pd.Timedelta(hours=1))<=pd.Timestamp(now)].copy()
    if len(fully_completed)<n:
        raise ValueError("Frozen candles are no longer completed; inconsistent dataset")
    models,_,train_idx=_locked_training(quotes,n,cfg)
    x,_,_ = causal_design(fully_completed)
    locked_at=pd.Timestamp(record["frozen_at_utc"])
    earliest=max(n, int((fully_completed["time"]<locked_at).sum()))
    # Signals require i, i+1 and i+2 completed, contiguous H1 observations.
    signals={}; decisions=[]
    for i in range(earliest,len(fully_completed)-2):
        if any(fully_completed.iloc[i+j+1]["time"]-fully_completed.iloc[i+j]["time"]!=pd.Timedelta(hours=1) for j in (0,1)):
            continue
        if x.iloc[i].isna().any():
            continue
        p=[float(m.predict_proba(x.iloc[[i]])[0,1]) for m in models]
        side=(1 if min(p)>=cfg.probability_threshold else
              -1 if max(p)<=1-cfg.probability_threshold else 0)
        if (float(x.iloc[i]["spread_close_pips"]) > cfg.max_signal_spread_pips):
            side=0
        signals[i]=side
        decisions.append({"signal_bar_open_utc":fully_completed.iloc[i]["time"].isoformat(),
                          "entry_bar_open_utc":fully_completed.iloc[i+1]["time"].isoformat(),
                          "exit_bar_open_utc":fully_completed.iloc[i+2]["time"].isoformat(),
                          "side":side,"logistic_p_up":round(p[0],8),"boost_p_up":round(p[1],8)})
    # Limited conservative logic from execution_lab rejects unknown rollover.
    result=simulate(fully_completed.reset_index(drop=True),signals,ex)
    report={"schema":"fx-aegis-forward-paper-result-v1", "status": "SYNTHETIC_TEST_ONLY" if meta["source_kind"]=="SYNTHETIC_TEST_FIXTURE" else "BROKER_RESEARCH_PAPER_ONLY",
            "frozen_at_utc":record["frozen_at_utc"],"frozen_rows":n,
            "new_completed_bars":len(fully_completed)-n,
            "new_bars_after_wall_clock_lock":sum(t>=locked_at for t in fully_completed.iloc[n:]["time"]),
            "audited_signals":len(decisions),"trades":result["trades"],
            "return_pct":result["return_pct"],"max_closed_equity_drawdown_pct":result["max_closed_equity_drawdown_pct"],
            "skips_by_reason":result["skips_by_reason"],
            "current_quotes_sha256":audit["quotes_sha256"],"frozen_prefix_sha256":record["prefix_sha256"],
            "locked_model_sample_count":len(train_idx),
            "approved_for_live_trading":False,"eligible_for_paper_promotion":False,
            "notes":["Paper-only; no broker order routing or actual execution proof.",
                     "Model refitted strictly on immutable pre-freeze history; no online retuning.",
                     "Historical candles cannot be relabeled as new forward observations.",
                     "Simulated H1-open execution ignores intra-hour liquidity, latency and margin.",
                     "Missing rollover information vetoes overlapping positions."]}
    out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(decisions).to_csv(out/"paper_signals.csv",index=False)
    pd.DataFrame(result["ledger"]).to_csv(out/"paper_trades.csv",index=False)
    write_json(out/"paper_forward_report.json",report)
    return report


def main():
    parser=argparse.ArgumentParser(description="Freeze a H1 ML model, then evaluate truly subsequent private H1 bars")
    sub=parser.add_subparsers(dest="cmd",required=True)
    freeze_cmd=sub.add_parser("freeze")
    review=sub.add_parser("evaluate")
    for p in (freeze_cmd,review):
        p.add_argument("--quotes",type=Path,required=True)
        p.add_argument("--provenance",type=Path,required=True)
        p.add_argument("--allow-synthetic",action="store_true")
    freeze_cmd.add_argument("--lock",type=Path,required=True)
    freeze_cmd.add_argument("--probability-threshold",type=float,default=.60)
    freeze_cmd.add_argument("--slippage-pips-per-side",type=float,required=True)
    freeze_cmd.add_argument("--commission-jpy-per-roundtrip",type=float,required=True)
    review.add_argument("--lock",type=Path,required=True)
    review.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if args.cmd=="freeze":
        result=freeze(args.quotes,args.provenance,args.lock,
                      cfg=RetrainConfig(probability_threshold=args.probability_threshold),
                      execution=ExecutionConfig(slippage_pips_per_side=args.slippage_pips_per_side,
                                                commission_jpy_per_roundtrip=args.commission_jpy_per_roundtrip),
                      allow_synthetic=args.allow_synthetic)
        print(json.dumps({"frozen_at_utc":result["frozen_at_utc"],"frozen_rows":result["frozen_rows"],
                          "status":"FROZEN_PAPER_ONLY","approved_for_live_trading":False}))
    else:
        result=evaluate(args.quotes,args.provenance,args.lock,args.out,allow_synthetic=args.allow_synthetic)
        print(json.dumps({k:result[k] for k in ("status","new_completed_bars","audited_signals","trades","return_pct","approved_for_live_trading")},ensure_ascii=False))


if __name__=="__main__":
    main()

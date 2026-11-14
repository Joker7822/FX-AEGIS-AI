"""Point-in-time H1 Bid/Ask ML retraining. RESEARCH ONLY; never places orders."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from execution_lab import ExecutionConfig, ema_signals, load_quotes, load_rollovers, simulate, write_json
from h1_evolution_guard import boundaries

SCHEMA = "fx-aegis-h1-ml-walkforward-v1"
FEATURES = ("return1", "return3", "return6", "ema_gap", "zscore20",
            "volatility12", "bid_range", "spread_close_pips", "hour_sin", "hour_cos")
WARMUP = 40


@dataclass(frozen=True)
class RetrainConfig:
    retrain_every_bars: int = 72
    min_training_bars: int = 300
    max_training_bars: int = 2500
    probability_threshold: float = .60
    max_signal_spread_pips: float = 2.
    seed: int = 42

    def check(self):
        if not (type(self.retrain_every_bars) is int and 1 <= self.retrain_every_bars <= 2000
                and type(self.min_training_bars) is int and 100 <= self.min_training_bars <= 100000
                and type(self.max_training_bars) is int
                and self.min_training_bars <= self.max_training_bars <= 100000
                and math.isfinite(self.probability_threshold)
                and .50 < self.probability_threshold < 1
                and math.isfinite(self.max_signal_spread_pips)
                and 0 <= self.max_signal_spread_pips <= 100
                and type(self.seed) is int and 0 <= self.seed <= 2**32-1):
            raise ValueError("Invalid H1 retraining configuration")


def causal_design(quotes: pd.DataFrame):
    """Causal features; caller MUST embargo future-dependent labels at fit time."""
    close = (quotes["bid_close"] + quotes["ask_close"]) / 2
    op = (quotes["bid_open"] + quotes["ask_open"]) / 2
    fast = close.ewm(span=8, adjust=False).mean()
    slow = close.ewm(span=24, adjust=False).mean()
    avg = close.rolling(20).mean()
    std = close.rolling(20).std()
    ret = close.pct_change(fill_method=None)
    times = quotes["time"]
    contiguous = (times.diff().dt.total_seconds()/3600).eq(1).rolling(6, min_periods=6).sum().eq(6)
    label_contiguous = ((times.shift(-1)-times)==pd.Timedelta(hours=1)) & (
        (times.shift(-2)-times.shift(-1))==pd.Timedelta(hours=1))
    x = pd.DataFrame({
        "return1":ret,
        "return3":close.pct_change(3, fill_method=None),
        "return6":close.pct_change(6, fill_method=None),
        "ema_gap":(fast-slow)/close,
        "zscore20":(close-avg)/std.replace(0,np.nan),
        "volatility12":ret.rolling(12).std(),
        "bid_range":(quotes["bid_high"]-quotes["bid_low"])/close,
        "spread_close_pips":(quotes["ask_close"]-quotes["bid_close"])/.01,
        "hour_sin":np.sin(2*np.pi*times.dt.hour/24),
        "hour_cos":np.cos(2*np.pi*times.dt.hour/24),
    }).replace([np.inf,-np.inf],np.nan)
    good = x.notna().all(axis=1) & contiguous & x["spread_close_pips"].ge(0)
    x.loc[~good,:]=np.nan
    label = pd.Series(np.where(op.shift(-2)>op.shift(-1),1,0),dtype="int64")
    valid = good & label_contiguous
    valid.iloc[-2:]=False
    return x,label,valid


def _fit(x,y,idx,seed):
    model1=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=450,random_state=seed))
    model2=HistGradientBoostingClassifier(max_iter=65,max_leaf_nodes=10,
                min_samples_leaf=20,l2_regularization=.5,random_state=seed)
    model1.fit(x.iloc[idx],y.iloc[idx])
    model2.fit(x.iloc[idx],y.iloc[idx])
    return model1,model2


# Backward-compatible fit entry point for immutable forward-paper replay.
# Same model family, seed, training indices, and causal features as _fit.
_fit_agents = _fit


def predict_fold(quotes,start,stop,cfg):
    """Train only on matured labels; trades cannot cross a frozen fold boundary."""
    cfg.check()
    if not 0<=start<stop<=len(quotes):
        raise ValueError("Invalid frozen fold boundaries")
    x,y,valid=causal_design(quotes)
    signals,decisions,fits={},[],[]
    models=None
    last_fit=None
    for i in range(start,stop-2):
        if i<WARMUP or x.iloc[i].isna().any():
            continue
        if models is None or i-last_fit>=cfg.retrain_every_bars:
            # Label j reads OPEN(j+1) and OPEN(j+2); require j+2<=i.
            indices=np.flatnonzero(valid.iloc[:i-1].to_numpy())
            if len(indices)>cfg.max_training_bars:
                indices=indices[-cfg.max_training_bars:]
            if len(indices)<cfg.min_training_bars:
                continue
            labels=y.iloc[indices]
            if labels.nunique()!=2 or min(labels.value_counts())<20:
                models=None
                continue
            models=_fit(x,y,indices,cfg.seed)
            last_fit=i
            fits.append({
                "signal_index_fit_at":i,
                "fit_as_of_utc":quotes.iloc[i]["time"].isoformat(),
                "last_train_signal_index":int(indices[-1]),
                "last_train_label_exit_index":int(indices[-1]+2),
                "last_train_label_exit_utc":quotes.iloc[int(indices[-1]+2)]["time"].isoformat(),
                "train_samples":int(len(indices)),
                "train_up_fraction":round(float(labels.mean()),6),
                "seed":cfg.seed,
            })
        if models is None:
            continue
        probs=[float(m.predict_proba(x.iloc[[i]])[0,1]) for m in models]
        th=cfg.probability_threshold
        side=1 if min(probs)>=th else -1 if max(probs)<=1-th else 0
        if float(x.iloc[i]["spread_close_pips"])>cfg.max_signal_spread_pips:
            side=0
        signals[i]=side
        decisions.append({
            "signal_index":i,
            "signal_bar_time_utc":quotes.iloc[i]["time"].isoformat(),
            "entry_bar_time_utc":quotes.iloc[i+1]["time"].isoformat(),
            "exit_bar_time_utc":quotes.iloc[i+2]["time"].isoformat(),
            "p_up_logistic":round(probs[0],8),
            "p_up_boost":round(probs[1],8),
            "side":side,
            "fit_as_of_utc":fits[-1]["fit_as_of_utc"],
            "last_train_label_exit_utc":fits[-1]["last_train_label_exit_utc"],
            "last_train_signal_index":fits[-1]["last_train_signal_index"],
        })
    return signals,decisions,fits


def run(quotes_path,provenance_path,out,*,allow_synthetic,cfg,execution,swap_ledger=None):
    cfg.check()
    execution.check()
    quotes,meta,audit=load_quotes(Path(quotes_path),Path(provenance_path),
                                  allow_synthetic=allow_synthetic)
    folds=boundaries(len(quotes))
    rollovers=load_rollovers(swap_ledger)
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    sections={}
    for name in ("validation","diagnostic_only"):
        a,b=folds[name]
        signals,decisions,fits=predict_fold(quotes,a,b,cfg)
        result=simulate(quotes,signals,execution,rollovers=rollovers)
        if any(not a<=d["signal_index"]<b-2 for d in decisions):
            raise AssertionError("Signal escaped frozen fold")
        start,end=quotes.iloc[a]["time"],quotes.iloc[b-1]["time"]
        for trade in result["ledger"]:
            if not start<=pd.Timestamp(trade["entry_time_utc"])<=end:
                raise AssertionError("Entry escaped frozen fold")
            if pd.Timestamp(trade["exit_time_utc"])>end:
                raise AssertionError("Exit escaped frozen fold")
        pd.DataFrame(decisions).to_csv(out/f"{name}_predictions.csv",index=False)
        pd.DataFrame(fits).to_csv(out/f"{name}_retraining_events.csv",index=False)
        pd.DataFrame(result["ledger"]).to_csv(out/f"{name}_trades.csv",index=False)
        sections[name]={
            "start_utc":start.isoformat(),"end_utc":end.isoformat(),"bars":b-a,
            "retrain_events":len(fits),"signal_predictions":len(decisions),
            "buy_signals":sum(d["side"]==1 for d in decisions),
            "sell_signals":sum(d["side"]==-1 for d in decisions),
            "wait_signals":sum(d["side"]==0 for d in decisions),
            "trades":result["trades"],"return_pct":result["return_pct"],
            "max_closed_equity_drawdown_pct":result["max_closed_equity_drawdown_pct"],
            "skips_by_reason":result["skips_by_reason"],
        }
    a,b=folds["validation"]
    reference=ema_signals(quotes,15,100,allow_short=False)
    reference={i:v for i,v in reference.items() if a<=i<b-2}
    baseline=simulate(quotes,reference,execution,rollovers=rollovers)
    report={
        "schema":SCHEMA,"source_kind":audit["data_kind"],
        "source_name":meta["source_name"],"quote_sha256":audit["quotes_sha256"],
        "quote_metadata_sha256":hashlib.sha256(Path(provenance_path).read_bytes()).hexdigest(),
        "quote_rows":len(quotes),
        "model_families":["LogisticRegression","HistGradientBoostingClassifier"],
        "target":"mid_open[i+2]>mid_open[i+1] for completed-bar signal i",
        "embargo":"training label j uses only open j+2, and j<=prediction_signal_index-2",
        "retrain_config":asdict(cfg),"execution_config":asdict(execution),
        "split_policy":"chronological_60pct_research_20pct_validation_20pct_diagnostic_only",
        "validation_ema_15_100_long_only_baseline":{
            "trades":baseline["trades"],"return_pct":baseline["return_pct"],
            "max_closed_equity_drawdown_pct":baseline["max_closed_equity_drawdown_pct"],
        },
        "sections":sections,
        "status":"SYNTHETIC_TEST_ONLY" if audit["data_kind"]=="SYNTHETIC_FIXTURE"
                 else "BROKER_USER_ATTESTED_RESEARCH_ONLY",
        "source_authenticity_independently_verified":False,
        "eligible_for_paper_promotion":False,"approved_for_live_trading":False,
        "limitations":[
            "No broker connection, no paper/live promotion, no claim of future profit.",
            "Last 20pct is descriptive only; repeated inspection contaminates it.",
            "Next H1 open entry ignores actual model inference and order latency.",
            "Missing rollover financing vetoes uncertain trades.",
            "No tick fills, partial orders, margin calls or observed slippage.",
            "Price hash is integrity only, not independent broker authentication.",
            "ML probabilities are not calibrated; fixed threshold is exploratory.",
        ],
    }
    raw=json.dumps(report,sort_keys=True,ensure_ascii=False,allow_nan=False,
                   separators=(",",":")).encode("utf-8")
    report["report_sha256"]=hashlib.sha256(raw).hexdigest()
    write_json(out/"h1_ml_retraining_report.json",report)
    write_json(out/"quote_audit.json",audit)
    return report


def cli():
    p=argparse.ArgumentParser(description="H1 Bid/Ask walk-forward ML retraining: research only")
    p.add_argument("--quotes",type=Path,required=True)
    p.add_argument("--provenance",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--allow-synthetic",action="store_true")
    p.add_argument("--retrain-every-bars",type=int,default=72)
    p.add_argument("--min-training-bars",type=int,default=300)
    p.add_argument("--max-training-bars",type=int,default=2500)
    p.add_argument("--probability-threshold",type=float,default=.60)
    p.add_argument("--max-signal-spread-pips",type=float,default=2.)
    p.add_argument("--seed",type=int,default=42)
    p.add_argument("--capital-jpy",type=float,default=1000000)
    p.add_argument("--allocation",type=float,default=.25)
    p.add_argument("--max-spread-pips",type=float,default=2.)
    p.add_argument("--slippage-pips-per-side",type=float,required=True)
    p.add_argument("--commission-jpy-per-roundtrip",type=float,required=True)
    p.add_argument("--swap-ledger",type=Path)
    a=p.parse_args()
    config=RetrainConfig(a.retrain_every_bars,a.min_training_bars,
                         a.max_training_bars,a.probability_threshold,
                         a.max_signal_spread_pips,a.seed)
    execution=ExecutionConfig(capital_jpy=a.capital_jpy,allocation=a.allocation,
                              max_spread_pips=a.max_spread_pips,
                              slippage_pips_per_side=a.slippage_pips_per_side,
                              commission_jpy_per_roundtrip=a.commission_jpy_per_roundtrip)
    result=run(a.quotes,a.provenance,a.out,allow_synthetic=a.allow_synthetic,
               cfg=config,execution=execution,swap_ledger=a.swap_ledger)
    print(json.dumps({"status":result["status"],"validation":result["sections"]["validation"],
                      "diagnostic_only":result["sections"]["diagnostic_only"],
                      "approved_for_live_trading":False},ensure_ascii=False))

if __name__=="__main__":
    cli()

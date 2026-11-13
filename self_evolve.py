"""FX AEGIS AI — deterministic, risk-gated self-evolution *research* agent.

Generates novel combinations of predefined signal formulas, parameter settings,
and volatility-control policies. Not an invention of a new ML optimization theory.

Critical temporal boundary: every candidate is *selected exclusively* from
pre-2021 history. Previously inspected 2021–2026 data cannot be called a fresh
holdout. A candidate may only advance to a PAPER champion after accumulating
new observations strictly AFTER its locked discovery date; never place orders.
FRED DEXJPUS values are non-tradeable NY-noon reference quotations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from github_pipeline import DATASET, META, ema_series
from repro_lab import atomic_json, canonical, sha256, validate_source

PROJECT = Path(__file__).resolve().parent
SCHEMA = "fx-aegis-self-evolve-1"
FAMILIES = ("ema_cross", "ema_slope", "close_breakout")
FAST = (5, 10, 15, 20, 30, 40, 50, 75)
SLOW = (80, 100, 150, 200, 250)
ALLOC = (.10, .20, .25, .30)
VOLWINDOW = (10, 20, 40)
VOLCAP = (.6, 1.2, 2., 99.)   # daily realized return standard deviation %, not a calibrated risk estimate
# All candidate-fitness windows end before 2021; no search on 2021+ observations.
TRAIN = ("2007-01-01", "2010-01-01")
VALIDATION = (
    ("2010-01-01", "2013-01-01"),
    ("2013-01-01", "2016-01-01"),
    ("2016-01-01", "2019-01-01"),
    ("2019-01-01", "2021-01-01"),
)
SHADOW_MIN_BARS = 126        # require ~6 months' genuinely new business-day reference observations
SHADOW_MIN_CHANGES = 2


@dataclass(frozen=True)
class Genome:
    family: str = "ema_cross"
    fast: int = 15
    slow: int = 100
    allocation: float = .25
    allow_short: bool = False
    vol_window: int = 20
    vol_cap_pct: float = 2.

    def __post_init__(self):
        if self.family not in FAMILIES or self.fast not in FAST or self.slow not in SLOW or self.fast >= self.slow:
            raise ValueError("Invalid strategy gene or EMA period")
        if self.allocation not in ALLOC or type(self.allow_short) is not bool:
            raise ValueError("Invalid risk allocation gene")
        if self.vol_window not in VOLWINDOW or self.vol_cap_pct not in VOLCAP:
            raise ValueError("Invalid volatility gate gene")


def gene_key(g: Genome) -> str:
    return json.dumps(asdict(g), sort_keys=True, separators=(",", ":"))


def random_gene(rng: random.Random) -> Genome:
    while True:
        f, s = rng.choice(FAST), rng.choice(SLOW)
        if f < s:
            return Genome(rng.choice(FAMILIES), f, s, rng.choice(ALLOC),
                          rng.choice((False, True)), rng.choice(VOLWINDOW), rng.choice(VOLCAP))


def mutate(g: Genome, rng: random.Random) -> Genome:
    d = asdict(g)
    fields = {
        "family": FAMILIES, "fast": FAST, "slow": SLOW, "allocation": ALLOC,
        "allow_short": (False, True), "vol_window": VOLWINDOW, "vol_cap_pct": VOLCAP,
    }
    for _ in range(12):
        field = rng.choice(tuple(fields))
        values = [v for v in fields[field] if v != d[field]]
        d[field] = rng.choice(values)
        try:
            return Genome(**d)
        except ValueError:
            d = asdict(g)
    return g


def crossover(a: Genome, b: Genome, rng: random.Random) -> Genome:
    for _ in range(8):
        genes = {k: rng.choice((v, getattr(b, k))) for k, v in asdict(a).items()}
        try:
            return Genome(**genes)
        except ValueError:
            continue
    return mutate(a, rng)


def _rolling_vol(prices: list[float], n: int) -> list[float]:
    # Uses only returns known at index t (predict t+1), no future labels.
    rets = [0.] + [100 * (prices[i]/prices[i-1]-1) for i in range(1, len(prices))]
    out = [99.] * len(rets)
    for i in range(n, len(rets)):
        window = rets[i-n+1:i+1]
        out[i] = statistics.pstdev(window)
    return out


class Indicators:
    def __init__(self, points: list[dict]):
        self.prices = [p["close"] for p in points]
        self.dates = [p["date"] for p in points]
        self.ema = {n: ema_series(self.prices, n) for n in sorted(set(FAST + SLOW))}
        self.vol = {n: _rolling_vol(self.prices, n) for n in VOLWINDOW}


def _signal(ind: Indicators, g: Genome, prev: int) -> int:
    if ind.vol[g.vol_window][prev] > g.vol_cap_pct:
        return 0
    prices, fast, slow = ind.prices, ind.ema[g.fast], ind.ema[g.slow]
    if g.family == "ema_cross":
        direction = 1 if fast[prev] > slow[prev] else -1 if fast[prev] < slow[prev] else 0
    elif g.family == "ema_slope":
        if prev < 5:
            return 0
        up = prices[prev] > slow[prev] and fast[prev] > fast[prev-5]
        down = prices[prev] < slow[prev] and fast[prev] < fast[prev-5]
        direction = 1 if up else -1 if down else 0
    else:
        if prev < g.fast:
            return 0
        history = prices[prev-g.fast:prev]  # excludes close[prev], no future access
        direction = 1 if prices[prev] > max(history) else -1 if prices[prev] < min(history) else 0
    return direction if direction > 0 or g.allow_short else 0


def simulate(ind: Indicators, g: Genome, start: str, end: str, *, cost_pips: float = 2.8) -> dict:
    """Research-only close-to-close scenario with reset at window boundary."""
    if not math.isfinite(cost_pips) or cost_pips < 0:
        raise ValueError("Invalid assumed trading cost")
    equity, high, dd, prior = 1., 1., 0., 0
    changes = observations = exposure = 0
    for i in range(max(g.slow, g.fast, g.vol_window) + 5, len(ind.dates)):
        if ind.dates[i] < start:
            continue
        if ind.dates[i] >= end:
            break
        previous = i - 1
        side = _signal(ind, g, previous)
        change = abs(side - prior)
        trading_cost = change * cost_pips * .01 / ind.prices[previous] * g.allocation
        period_return = g.allocation * side * (ind.prices[i]/ind.prices[previous]-1) - trading_cost
        equity *= max(0.000001, 1 + period_return)
        high = max(high, equity)
        dd = max(dd, 100 * (1 - equity / high))
        changes += int(change > 0)
        observations += 1
        exposure += int(side != 0)
        prior = side
    return {"return_pct": round(100 * (equity-1), 6), "max_dd_pct": round(dd, 6),
            "position_changes": changes, "observations": observations,
            "exposure_pct": round(100 * exposure/max(1, observations), 2)}


def evaluate(ind: Indicators, g: Genome, *, base_cost: float) -> dict:
    train = simulate(ind, g, *TRAIN, cost_pips=base_cost*2)
    folded = [simulate(ind, g, *f, cost_pips=base_cost*2) for f in VALIDATION]
    fold5 = [simulate(ind, g, *f, cost_pips=base_cost*5) for f in VALIDATION]
    returns = [f["return_pct"] for f in folded]
    draws = [f["max_dd_pct"] for f in folded]
    # Gate thresholds are fixed by code and independent of the previously inspected 2021+ interval.
    passes = (train["return_pct"] > 0 and sum(r > 0 for r in returns) >= 3
              and min(returns) >= -2 and sum(returns) > 4
              and max(draws) <= 8 and all(f["position_changes"] >= 2 for f in folded)
              and sum(f["position_changes"] for f in folded) >= 12
              and min(f["exposure_pct"] for f in folded) > 20
              and sum(f["return_pct"] for f in fold5) > 0)
    # Penalize worst fold and drawdown; score is for ordering, not a prediction.
    score = (statistics.median(returns) + 1.4*min(returns)
             - .6*max(draws) - .01*sum(f["position_changes"] for f in folded))
    return {"gene": asdict(g), "score": round(score, 6), "eligible": bool(passes),
            "train": train, "validation_double_cost": folded,
            "validation_fivefold_cost": fold5}


def evolve(ind: Indicators, *, seed: int = 42, population: int = 16,
           generations: int = 5, base_cost: float = 1.4) -> dict:
    if not 4 <= population <= 64 or not 1 <= generations <= 20:
        raise ValueError("Population/generations outside compute limits")
    if not 0 <= base_cost <= 20 or not math.isfinite(base_cost):
        raise ValueError("Cost outside allowed range")
    if not ind.dates or ind.dates[-1] < "2021-01-01":
        raise ValueError("Need complete pre-2021 validation history")
    rng = random.Random(seed)
    current = [Genome(), Genome("ema_cross", 50, 200, .25, True, 20, 99.),
               Genome("ema_slope", 15, 100, .2, False, 20, 2.)]
    while len(current) < population:
        current.append(random_gene(rng))
    history: dict[str, dict] = {}
    generations_summary = []
    for i in range(generations):
        scores = []
        for g in current:
            key = gene_key(g)
            if key not in history:
                history[key] = evaluate(ind, g, base_cost=base_cost)
            scores.append((g, history[key]))
        scores.sort(key=lambda z: (not z[1]["eligible"], -z[1]["score"], gene_key(z[0])))
        winner = scores[0]
        generations_summary.append({"generation": i+1, "best_gene": asdict(winner[0]),
                                    "best_score": winner[1]["score"],
                                    "eligible_population": sum(x[1]["eligible"] for x in scores)})
        elites = [x[0] for x in scores[:max(2, population//4)]]
        next_gen = list(elites)
        while len(next_gen) < population:
            child = crossover(rng.choice(elites), rng.choice(elites), rng)
            if rng.random() < .85:
                child = mutate(child, rng)
            next_gen.append(child)
        current = next_gen
    ordered = sorted(history.values(), key=lambda x: (not x["eligible"], -x["score"], json.dumps(x["gene"],sort_keys=True)))
    return {"eligible_count": sum(z["eligible"] for z in ordered),
            "evaluated": len(history), "best": ordered[0], "top": ordered[:10],
            "generations": generations_summary}


def prefix_hash(points: list[dict], upto: str) -> str:
    return sha256(canonical([p for p in points if p["date"] <= upto]))


def shadow_status(ind: Indicators, g: Genome, discovery_end: str,
                  *, base_cost: float) -> dict:
    first_new = next((i for i, d in enumerate(ind.dates) if d > discovery_end), None)
    count = 0 if first_new is None else len(ind.dates) - first_new
    if count < SHADOW_MIN_BARS:
        return {"state": "AWAITING_NEW_OBSERVATIONS", "new_observations": count,
                "required": SHADOW_MIN_BARS, "approved_for_live_trading": False}
    record = simulate(ind, g, ind.dates[first_new], "9999-12-31", cost_pips=base_cost*2)
    passes = (record["return_pct"] > 0 and record["max_dd_pct"] <= 8
              and record["position_changes"] >= SHADOW_MIN_CHANGES and record["exposure_pct"] > 20)
    return {"state": "PAPER_ELIGIBLE" if passes else "SHADOW_REJECTED",
            "new_observations": count, "required": SHADOW_MIN_BARS,
            "metrics_double_cost": record, "approved_for_live_trading": False}


def update_archive(path: Path, *, seed_mode: str, week_id: str, seed: int,
                   results: dict, source_sha: str) -> dict:
    """Persistent, integrity-checked research lineage; no holdout-based selection."""
    if path.exists():
        archive = json.loads(path.read_text(encoding="utf-8"))
        digest = archive.pop("archive_sha256", None)
        if archive.get("schema") != SCHEMA or digest != sha256(canonical(archive)):
            raise ValueError("Exploration archive has been modified or is incompatible")
    else:
        archive = {"schema": SCHEMA, "seed_mode": seed_mode, "weekly_runs": []}
    if archive["seed_mode"] != seed_mode:
        raise ValueError("Seed policy changed: choose a separate research archive")
    if not any(x["week_id"] == week_id for x in archive["weekly_runs"]):
        archive["weekly_runs"].append({
            "week_id": week_id, "seed": seed, "source_sha256": source_sha,
            "evaluated": results["evaluated"], "eligible_count": results["eligible_count"],
            "best_gene": results["best"]["gene"],
            "best_score": results["best"]["score"],
        })
    archive["candidate_evaluations_total"] = sum(x["evaluated"] for x in archive["weekly_runs"])
    archive["archive_sha256"] = sha256(canonical(archive))
    atomic_json(path, archive)
    return archive


def run(dataset: Path, metadata: Path, report_file: Path, challenger_file: Path,
        paper_file: Path, site_file: Path, *, fixture: bool = False,
        seed: int = 42, population: int = 16, generations: int = 5,
        base_cost: float = 1.4, seed_mode: str = "fixed",
        archive_file: Path | None = None) -> dict:
    points, source = validate_source(dataset, metadata, fixture=fixture)
    ind = Indicators(points)
    if seed_mode not in ("fixed", "weekly"):
        raise ValueError("Unknown seed policy")
    calendar = date.fromisoformat(points[-1]["date"]).isocalendar()
    week_id = f"{calendar.year}-W{calendar.week:02d}"
    effective_seed = seed if seed_mode == "fixed" else seed + calendar.year * 100 + calendar.week
    result = evolve(ind, seed=effective_seed, population=population,
                    generations=generations, base_cost=base_cost)
    archive = update_archive(archive_file, seed_mode=seed_mode, week_id=week_id,
                             seed=effective_seed, results=result,
                             source_sha=source["data_sha256"]) if archive_file else None
    challenger = None
    shadow = {"state": "NO_RESEARCH_ELIGIBLE_CANDIDATE", "approved_for_live_trading": False}
    if challenger_file.exists():
        challenger = json.loads(challenger_file.read_text(encoding="utf-8"))
        if challenger.get("schema") != SCHEMA or challenger.get("mode") != "paper-only":
            raise ValueError("Malformed challenger record")
        if challenger.get("fixture") != fixture:
            raise ValueError("Source class changed since challenger was locked")
        if challenger.get("snapshot_prefix_sha256") != prefix_hash(points, challenger["discovery_end"]):
            raise ValueError("Historical dataset was revised after candidate lock; manual review required")
        # Never overwrite the challenger or reselect based on forward outcomes.
        shadow = shadow_status(ind, Genome(**challenger["gene"]), challenger["discovery_end"], base_cost=base_cost)
        if shadow["state"] == "PAPER_ELIGIBLE" and not paper_file.exists():
            atomic_json(paper_file, {"schema": SCHEMA, "mode": "paper-only",
                                     "gene": challenger["gene"],
                                     "discovery_end": challenger["discovery_end"],
                                     "forward_evidence": shadow,
                                     "no_live_orders": True})
    elif result["best"]["eligible"]:
        challenger = {"schema": SCHEMA, "mode": "paper-only", "fixture": fixture,
                      "gene": result["best"]["gene"], "discovery_end": points[-1]["date"],
                      "snapshot_prefix_sha256": prefix_hash(points, points[-1]["date"]),
                      "source_kind": source["kind"], "no_live_orders": True}
        atomic_json(challenger_file, challenger)
        shadow = {"state": "NEW_CHALLENGER_LOCKED", "new_observations": 0,
                  "required": SHADOW_MIN_BARS, "approved_for_live_trading": False}
    summary = {"schema": SCHEMA, "source": source, "observation_count": len(points),
               "end_observation": points[-1]["date"], "search_window_end_exclusive": "2021-01-01",
               "warning": "Selection uses pre-2021 reference data only. 2021+ was previously inspected and is not fresh validation.",
               "random_seed": effective_seed, "seed_policy": seed_mode, "exploration_week": week_id,
               "exploration_history": ({"weekly_runs": len(archive["weekly_runs"]),
                                          "candidate_evaluations_total": archive["candidate_evaluations_total"],
                                          "archive_sha256": archive["archive_sha256"]} if archive else None),
               "population": population, "generations_count": generations,
               "base_assumed_cost_pips": base_cost, "eligible_count": result["eligible_count"],
               "evaluated": result["evaluated"], "best": result["best"], "top": result["top"],
               "generation_history": result["generations"], "shadow": shadow,
               "paper_champion_present": paper_file.exists(),
               "approved_for_live_trading": False, "trading_connection": "DISABLED"}
    summary["sha256"] = sha256(canonical(summary))
    atomic_json(report_file, summary)
    atomic_json(site_file, {"schema": SCHEMA, "source_kind": source["kind"],
                            "data_sha256": source["data_sha256"],
                            "report_sha256": summary["sha256"],
                            "data_to": points[-1]["date"], "candidate_count": result["evaluated"],
                            "exploration_history_runs": len(archive["weekly_runs"]) if archive else 0,
                            "eligible_count": result["eligible_count"],
                            "best": result["best"]["gene"],
                            "shadow_state": shadow["state"],
                            "paper_champion_present": paper_file.exists(),
                            "approved_for_live_trading": False,
                            "warning": "Not a reliable forecast or executable backtest; FRED daily reference only."})
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Self-evolving, reproducible FX research only. NO BROKER ORDERS")
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--metadata", type=Path, default=META)
    p.add_argument("--report", type=Path, default=PROJECT / "research/evolution_latest.json")
    p.add_argument("--challenger", type=Path, default=PROJECT / "research/evolution_challenger.json")
    p.add_argument("--paper", type=Path, default=PROJECT / "research/paper_champion.json")
    p.add_argument("--site", type=Path, default=PROJECT / "docs/data/evolution.json")
    p.add_argument("--fixture", action="store_true", help="Explicit synthetic tests only")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--seed-mode", choices=("fixed", "weekly"), default="fixed",
                   help="weekly=repeatably explore a different seeded population each ISO week")
    p.add_argument("--archive", type=Path, default=PROJECT / "research/evolution_archive.json")
    p.add_argument("--population", type=int, default=16)
    p.add_argument("--generations", type=int, default=5)
    p.add_argument("--spread-pips", type=float, default=1.4)
    a = p.parse_args()
    outcome = run(a.dataset, a.metadata, a.report, a.challenger, a.paper, a.site,
                  fixture=a.fixture, seed=a.seed, population=a.population,
                  generations=a.generations, base_cost=a.spread_pips,
                  seed_mode=a.seed_mode, archive_file=a.archive)
    print(json.dumps({"evaluated": outcome["evaluated"],
                      "eligible_count": outcome["eligible_count"],
                      "best": outcome["best"]["gene"],
                      "best_score": outcome["best"]["score"],
                      "seed_policy": outcome["seed_policy"],
                      "exploration_history": outcome["exploration_history"],
                      "shadow": outcome["shadow"],
                      "approved_for_live_trading": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

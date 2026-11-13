"""Declarative, auditable custom ML agents for FX AEGIS AI.

Agents are configuration files, not arbitrary Python source/plugins. No broker
credentials or live orders; generated agents can only veto existing signals.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# Explicit allowlist: no time, future target, entry/exit prices or unknown columns.
ALLOWED_FEATURES = frozenset({
    "return_1", "return_3", "return_6", "ema_gap", "ma_slope",
    "range_pct", "zscore20", "rsi14", "volatility12", "hour_sin", "hour_cos",
})
TEMPLATES = {
    "momentum": ["return_1", "return_3", "return_6", "ema_gap", "ma_slope", "range_pct"],
    "reversal": ["return_1", "return_3", "zscore20", "rsi14", "range_pct"],
    "session": ["hour_sin", "hour_cos", "volatility12", "range_pct", "return_1"],
}
MODELS = ("logistic", "random_forest", "gradient_boosting")


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    features: list[str]
    threshold: float = 0.60
    seed: int = 42
    role: str = "veto_only"
    version: int = 1

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,39}", self.name):
            raise ValueError("name must be 2-40 ASCII letters, digits, _ or -, beginning with a letter")
        if self.model not in MODELS:
            raise ValueError(f"model must be one of {MODELS}")
        if not isinstance(self.features, list) or not (2 <= len(self.features) <= len(ALLOWED_FEATURES)):
            raise ValueError("features must be a list of 2+ allowed feature names")
        if len(set(self.features)) != len(self.features) or any(f not in ALLOWED_FEATURES for f in self.features):
            raise ValueError("features contain unknown, duplicated or potentially future-data columns")
        if type(self.threshold) not in (int, float) or not np.isfinite(self.threshold) or not (0.5 < self.threshold < 1.0):
            raise ValueError("threshold must be strictly between 0.50 and 1.0")
        if type(self.seed) is not int or self.seed < 0 or self.seed > 2**32 - 1:
            raise ValueError("seed must be a nonnegative 32-bit integer")
        if self.role != "veto_only" or self.version != 1:
            raise ValueError("only version=1, role=veto_only is currently supported")


def load_spec(path: str | Path) -> AgentSpec:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != set(AgentSpec.__dataclass_fields__):
        raise ValueError("agent config has missing or unknown keys")
    spec = AgentSpec(**raw)
    spec.validate()
    return spec


def create_spec(directory: str | Path, name: str, template: str,
                model: str = "logistic", threshold: float = 0.60,
                seed: int = 42) -> Path:
    if template not in TEMPLATES:
        raise ValueError(f"template must be one of {tuple(TEMPLATES)}")
    spec = AgentSpec(name, model, list(TEMPLATES[template]), threshold, seed)
    spec.validate()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.name}.json"
    # Never overwrite another agent definition inadvertently.
    with path.open("x", encoding="utf-8") as f:
        json.dump(asdict(spec), f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


class CustomDirectionAgent:
    """Independent direction model with one vote, usable solely as a veto."""

    def __init__(self, spec: AgentSpec):
        spec.validate()
        self.spec = spec
        if spec.model == "logistic":
            self.model = make_pipeline(StandardScaler(), LogisticRegression(
                max_iter=500, C=0.5, random_state=spec.seed))
        elif spec.model == "random_forest":
            self.model = RandomForestClassifier(
                n_estimators=60, max_depth=5, min_samples_leaf=25,
                random_state=spec.seed, n_jobs=1)
        else:
            self.model = HistGradientBoostingClassifier(
                max_iter=65, max_leaf_nodes=12, min_samples_leaf=30,
                random_state=spec.seed)

    def fit(self, history: pd.DataFrame) -> None:
        if history["up_target"].nunique() != 2:
            raise ValueError("training labels must contain 0 and 1")
        values = history[self.spec.features].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("training features must be finite")
        self.model.fit(history[self.spec.features], history["up_target"])

    def predict(self, segment: pd.DataFrame) -> np.ndarray:
        values = segment[self.spec.features].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("prediction features must be finite")
        return self.model.predict_proba(segment[self.spec.features])[:, 1]

    def veto(self, side: np.ndarray, proba: np.ndarray) -> np.ndarray:
        result = side.copy()
        th = self.spec.threshold
        result[(result == 1) & (proba < th)] = 0
        result[(result == -1) & (proba > 1 - th)] = 0
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate safe custom FX direction AI agents (backtest only)")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a new JSON agent definition")
    create.add_argument("--name", required=True)
    create.add_argument("--template", choices=tuple(TEMPLATES), default="momentum")
    create.add_argument("--model", choices=MODELS, default="logistic")
    create.add_argument("--threshold", type=float, default=0.60)
    create.add_argument("--seed", type=int, default=42)
    create.add_argument("--directory", type=Path, default=Path("agents"))
    check = sub.add_parser("validate", help="Validate one custom agent definition")
    check.add_argument("file", type=Path)
    list_cmd = sub.add_parser("list", help="List valid custom agents")
    list_cmd.add_argument("--directory", type=Path, default=Path("agents"))
    args = parser.parse_args()
    if args.command == "create":
        path = create_spec(args.directory, args.name, args.template, args.model, args.threshold, args.seed)
        print(f"Created: {path}")
    elif args.command == "validate":
        print(json.dumps(asdict(load_spec(args.file)), ensure_ascii=False, indent=2))
    elif args.command == "list":
        for p in sorted(args.directory.glob("*.json")):
            agent = load_spec(p)
            print(f"{agent.name}\t{agent.model}\t{agent.role}\tthreshold={agent.threshold:.2f}")


if __name__ == "__main__":
    main()

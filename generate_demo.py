"""Creates 100% synthetic OHLC. NEVER treat this as historical market evidence."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def generate(path: str | Path, n: int = 2400, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    # Regime-dependent random path solely for checking code execution.
    regime = np.repeat([0.02, -0.015, 0.0, 0.01], repeats=n // 4 + 1)[:n]
    increments = regime + rng.normal(0, 0.055, size=n)
    mid = 150.0 + np.cumsum(increments)
    opens = mid + rng.normal(0, 0.008, size=n)
    closes = opens + rng.normal(0, 0.035, size=n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0, 0.015, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0, 0.015, size=n))
    times = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
    d = pd.DataFrame({"time": times.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "open": opens, "high": highs, "low": lows, "close": closes})
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(file, index=False)
    print(f"Synthetic demo generated: {file}, {n} bars. NOT real FX quotes.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="data/demo_synthetic_usdjpy.csv")
    p.add_argument("--rows", type=int, default=2400)
    args = p.parse_args()
    generate(args.output, args.rows)

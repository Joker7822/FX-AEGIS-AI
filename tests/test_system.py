"""Unit checks for chronology, cost, veto and data parsing."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from generate_demo import generate
from main import Config, RiskAgent, backtest, load_ohlc, make_features, walk_forward_predictions


class FXResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.demo = Path(cls.tempdir.name) / "synthetic.csv"
        generate(cls.demo, n=1300)
        cls.data = make_features(load_ohlc(cls.demo))

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_validation_of_timestamp(self):
        raw = pd.read_csv(self.demo)
        raw.loc[0, "time"] = "2023-01-01 00:00:00"
        bad = Path(self.tempdir.name) / "bad.csv"
        raw.to_csv(bad, index=False)
        with self.assertRaises(ValueError):
            load_ohlc(bad)

    def test_features_do_not_look_ahead(self):
        raw = load_ohlc(self.demo)
        original = make_features(raw)
        changed = raw.copy()
        for c in ["open", "high", "low", "close"]:
            changed.loc[1000:, c] = changed.loc[1000:, c] * 1.20
        altered = make_features(changed)
        # Before row 990, all predictive features must be identical.
        a = original.loc[original["time"] < raw.loc[990, "time"], :]
        b = altered.loc[altered["time"] < raw.loc[990, "time"], :]
        self.assertEqual(len(a), len(b))
        np.testing.assert_allclose(a[["return_1", "zscore20", "ema_gap"]],
                                   b[["return_1", "zscore20", "ema_gap"]])

    def test_walk_forward_training_purge(self):
        data = self.data
        start = int(len(data) * 0.60)
        pred = walk_forward_predictions(data, start, start + 15,
                                        Config(retrain_every_bars=8))
        self.assertEqual(len(pred), 15)
        self.assertTrue(pred["side"].isin([-1, 0, 1]).all())
        # Latest training target exits at or before first block's signal time.
        self.assertLessEqual(pd.Timestamp(pred.iloc[0]["fit_until_utc"]), data.loc[start, "time"])

    def test_risk_notional_and_loss_stop(self):
        cfg = Config()
        r = RiskAgent(cfg)
        t = pd.Timestamp("2024-01-01T12:00:00Z")
        self.assertEqual(r.accept(t, 150.0), "OK")
        self.assertEqual(r.accept(t, 1000.0), "NOTIONAL_LIMIT")
        r.realize(-110_000)
        self.assertEqual(r.accept(t, 150.0), "HALTED_DRAWDOWN")

    def test_transaction_costs_reduce_profit(self):
        start = 600
        d = self.data
        # Create a single forced trade, so the cost difference is observable.
        signals = pd.DataFrame(index=range(start, start + 4), data={
            "p_up_trend": 0.6, "p_up_reversion": 0.6,
            "predicted_abs_return": 0.001, "volatility_cap": 0.01,
            "volatility_veto": False, "side": [1, 0, 0, 0],
            "fit_until_utc": "2023-01-01T00:00:00+00:00",
        })
        cheap, _ = backtest(d, signals, start, start + 4,
                            Config(spread_pips=0, slippage_pips_per_side=0))
        costly, _ = backtest(d, signals, start, start + 4,
                             Config(spread_pips=2, slippage_pips_per_side=1))
        self.assertEqual(cheap["trade_count"], 1)
        self.assertEqual(costly["trade_count"], 1)
        self.assertAlmostEqual(cheap["final_yen"] - costly["final_yen"], 40.0, places=2)


if __name__ == "__main__":
    unittest.main()

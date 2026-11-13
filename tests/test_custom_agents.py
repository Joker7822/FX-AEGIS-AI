import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from custom_agents import create_spec, load_spec, CustomDirectionAgent
from main import Config, make_features, walk_forward_predictions


class TestCustomAgents(unittest.TestCase):
    def test_generate_and_validate(self):
        with tempfile.TemporaryDirectory() as td:
            path = create_spec(td, "MyTrendAI", "momentum", "logistic", 0.61)
            spec = load_spec(path)
            self.assertEqual(spec.name, "MyTrendAI")
            self.assertEqual(spec.threshold, 0.61)
            with self.assertRaises(FileExistsError):
                create_spec(td, "MyTrendAI", "momentum")

    def test_prevents_future_feature_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                create_spec(td, "../escape", "momentum")
            p = create_spec(td, "SafeAgent", "momentum")
            obj = json.loads(p.read_text())
            obj["features"] += ["future_return"]
            p.write_text(json.dumps(obj))
            with self.assertRaises(ValueError):
                load_spec(p)

    def test_veto_never_creates_a_new_trade(self):
        with tempfile.TemporaryDirectory() as td:
            p = create_spec(td, "SafetyVeto", "reversal", threshold=.7)
            model = CustomDirectionAgent(load_spec(p))
            before = np.array([1, -1, 0, 1, -1])
            after = model.veto(before, np.array([.8, .8, .4, .4, .2]))
            np.testing.assert_array_equal(after, np.array([1, 0, 0, 0, -1]))

    def test_walk_forward_custom_is_historical_and_auditable(self):
        with tempfile.TemporaryDirectory() as td:
            p = create_spec(td, "CustomTrend", "momentum", threshold=.6)
            # No need to run a full backtest here: use synthetic OHLC from existing generator.
            from main import load_ohlc
            out = Path(td) / 'ohlc.csv'
            from generate_demo import generate
            generate(out, 1100)
            data = make_features(load_ohlc(out))
            start = 640
            pred = walk_forward_predictions(data, start, start + 8,
                Config(retrain_every_bars=8), (str(p),))
            self.assertIn('custom_CustomTrend_p_up', pred)
            self.assertEqual(len(pred), 8)
            self.assertTrue((pred['side'] == 0).all() or pred['side'].isin([-1, 0, 1]).all())
            self.assertLessEqual(str(pred.iloc[0]['fit_until_utc']), str(data.iloc[start]['time']))


if __name__ == '__main__':
    unittest.main()

"""Cross-module contract: locked-paper fit must not drift from H1 retrainer."""
import unittest
from h1_ml_retrainer import _fit, _fit_agents
from forward_paper import freeze, evaluate

class ModelApiContractTests(unittest.TestCase):
    def test_frozen_model_uses_existing_retrainer_fit(self):
        self.assertIs(_fit_agents, _fit)
        self.assertTrue(callable(freeze))
        self.assertTrue(callable(evaluate))

    def test_frozen_model_remains_paper_only(self):
        from forward_paper import SCHEMA
        self.assertIn('locked-paper', SCHEMA)

if __name__ == '__main__':
    unittest.main()

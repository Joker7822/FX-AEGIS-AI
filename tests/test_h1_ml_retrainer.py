"""Synthetic-only H1 ML checks: chronological, provenance and safe execution."""
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
import pandas as pd
from execution_lab import ExecutionConfig, make_fixture, load_quotes
from h1_evolution_guard import boundaries
from h1_ml_retrainer import RetrainConfig, predict_fold, causal_design, run

class H1MLRetrainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=Path(cls.temp.name)
        cls.csv,cls.prov=make_fixture(cls.path,n=1250,seed=42)
        cls.quotes,_,_=load_quotes(cls.csv,cls.prov,allow_synthetic=True)
        cls.cfg=RetrainConfig(retrain_every_bars=72,min_training_bars=300,
                              max_training_bars=700,probability_threshold=.51)
        cls.result=run(cls.csv,cls.prov,cls.path/"out",allow_synthetic=True,
                       cfg=cls.cfg,execution=ExecutionConfig(slippage_pips_per_side=.2))
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def test_retrained_multiple_times(self):
        self.assertGreater(self.result["sections"]["validation"]["retrain_events"],1)
    def test_no_future_training_labels(self):
        for section in ("validation","diagnostic_only"):
            data=pd.read_csv(self.path/"out"/f"{section}_retraining_events.csv")
            self.assertTrue((data["last_train_label_exit_index"]<=data["signal_index_fit_at"]).all())
    def test_future_quotes_cannot_change_validation_predictions(self):
        a,b=boundaries(len(self.quotes))["validation"]
        changed=self.quotes.copy()
        for col in changed.columns:
            if col!="time":
                changed.loc[b:,col]*=1.2
        self.assertEqual(predict_fold(self.quotes,a,b,self.cfg)[1],
                         predict_fold(changed,a,b,self.cfg)[1])
    def test_synthetic_requires_opt_in(self):
        with self.assertRaisesRegex(ValueError,"Synthetic"):
            run(self.csv,self.prov,self.path/"blocked",allow_synthetic=False,
                cfg=self.cfg,execution=ExecutionConfig())
    def test_no_paper_or_live_approval(self):
        self.assertEqual(self.result["status"],"SYNTHETIC_TEST_ONLY")
        self.assertFalse(self.result["eligible_for_paper_promotion"])
        self.assertFalse(self.result["approved_for_live_trading"])
    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            replace(self.cfg,probability_threshold=1.01).check()

if __name__=="__main__":
    unittest.main()

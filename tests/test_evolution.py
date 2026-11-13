"""Safety, chronology, mutation, and auto-promotion safeguards."""
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from evolution_engine import (Blueprint, DiscoveredVetoAgent, assess, atomic_json,
                              build_features, crossover, discover, eligible, folds,
                              load_blueprint, monitor, mutate, sample)
from generate_demo import generate
from main import Config, load_ohlc, make_features


class EvolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.csv = Path(cls.tmp.name) / 'demo.csv'
        generate(cls.csv, n=1300, seed=7)
        cls.data = make_features(load_ohlc(cls.csv))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_safe_grammar_rejects_unknown_columns_and_code(self):
        b=sample(random.Random(9),'SafeAgent')
        from dataclasses import replace
        with self.assertRaises(ValueError):
            replace(b,features=tuple(sorted(set(b.features)|{'up_target'}))).check()
        with self.assertRaises(ValueError):
            replace(b,op='__import__("os").system("whoami")').check()
        with self.assertRaises(ValueError):
            replace(b,role='can_place_orders').check()
        with self.assertRaises(ValueError):
            replace(b,name='../bad').check()

    def test_gene_mutation_and_crossover_stay_in_allowlist(self):
        rng=random.Random(42)
        parents=[sample(rng,'ParentA'),sample(rng,'ParentB')]
        for i in range(50):
            a=mutate(parents[i%2],rng,f'Mutation{i}')
            c=crossover(a,parents[(i+1)%2],rng,f'Cross{i}')
            a.check(); c.check()
            self.assertNotIn('future_return', c.features)

    def test_feature_compiler_uses_no_future_rows(self):
        b=sample(random.Random(10),'RowOnly')
        x=build_features(self.data,b)
        changed=self.data.copy()
        changed.loc[1000:,list(b.features)] *= 6.2
        y=build_features(changed,b)
        np.testing.assert_allclose(x.iloc[:990],y.iloc[:990])

    def test_veto_never_creates_buys_or_sells(self):
        b=sample(random.Random(12),'VetoAgent')
        veto=DiscoveredVetoAgent(b)
        side=np.array([1,0,-1,1,-1,0])
        prob=np.array([.49,.99,.52,.9,.02,0.])
        outcome=veto.veto(side,prob)
        self.assertTrue(all(i==j or j==0 for i,j in zip(side,outcome)))

    def test_fold_boundaries_and_purge(self):
        n=len(self.data)
        ranges=folds(n)
        self.assertEqual(len(ranges),3)
        self.assertEqual(ranges[-1][2],int(n*.80))
        for end_training,start,stop in ranges:
            self.assertEqual(end_training,start-2)
            self.assertLess(stop,n)
            self.assertLess(pd.Timestamp(self.data.iloc[end_training-1]['exit_time']),
                            pd.Timestamp(self.data.iloc[start]['time']))

    def test_disallow_promotion_without_sufficient_evidence(self):
        base={'net_yen':30.,'baseline_net_yen':-200.,'stress_2x_net_yen':-100.,
              'positive_folds':1,'better_than_baseline_folds':2,'trade_count':10,
              'max_dd_pct':2.}
        ok,fail=eligible(base)
        self.assertFalse(ok)
        self.assertIn('nonpositive_double_cost_net',fail)
        self.assertIn('insufficient_trade_sample',fail)

    def test_blueprint_io_validates_and_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as root:
            b=sample(random.Random(2),'ReadSafe')
            path=Path(root)/'b.json'
            atomic_json(path,b.to_json())
            self.assertEqual(load_blueprint(path),b)
            raw=json.loads(path.read_text())
            raw['arbitrary_python']='print(123)'
            atomic_json(path,raw)
            with self.assertRaises(ValueError):
                load_blueprint(path)

    def test_evolution_and_monitor_dont_promote_bad_strategy(self):
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'run'
            report=discover(self.csv,output,population=2,generations=1,seed=42,
                            min_trades=10000,auto_promote_paper=True)
            self.assertLessEqual(report['candidate_count'],2)
            self.assertFalse(report['paper_promoted'])
            self.assertFalse((output/'incumbent_paper.json').exists())
            self.assertTrue((output/'candidate_evolved.json').exists())
            self.assertEqual(report['holdout_read_only']['candidate']['start_utc'],
                             str(self.data.iloc[int(len(self.data)*.8)]['time']))
            first=monitor(self.csv,output,population=2,generations=1,seed=42,
                          min_trades=10000,auto_promote_paper=True)
            second=monitor(self.csv,output,population=2,generations=1,seed=42,
                           min_trades=10000,auto_promote_paper=True)
            self.assertEqual(first['status'],'evaluated')
            self.assertEqual(second['status'],'unchanged')
            # Any retroactive file rewrite, without a new candle, fails closed.
            change=pd.read_csv(self.csv)
            change.at[0,'close']+=.001
            edited=Path(root)/'revised.csv'
            change.to_csv(edited,index=False)
            with self.assertRaisesRegex(ValueError,'Revised or non-extended'):
                monitor(edited,output,population=2,generations=1,seed=42)


if __name__=='__main__':
    unittest.main()

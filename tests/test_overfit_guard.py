"""Conservative multiple-testing and temporal-boundary tests (synthetic prices only)."""
import math
import unittest
from dataclasses import asdict

import numpy as np
import pandas as pd

from self_evolve import Genome, Indicators, simulate, evolve, gene_key
from overfit_guard import (audit_candidates, log_returns, max_t_block_bootstrap,
                           cscv_like_rank_instability, chronological_reference_diagnostic)


class OverfitGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = pd.bdate_range('2006-11-13','2026-11-06')
        rng = np.random.default_rng(130)
        price = 120*np.exp(np.cumsum(rng.normal(0.00011,.0022,size=len(ds))))
        cls.points=[{'date':d.strftime('%Y-%m-%d'),'close':float(v)} for d,v in zip(ds,price)]
        cls.ind=Indicators(cls.points)
        cls.genes=[Genome(),Genome('ema_cross',50,200,.25,True,20,99.),
                   Genome('close_breakout',20,100,.2,False,20,2.)]

    def test_daily_returns_reproduce_simulate(self):
        gene=self.genes[1]
        periods=(('2010-01-01','2013-01-01'),('2019-01-01','2021-01-01'))
        for start,end in periods:
            rec=log_returns(self.ind,gene,start,end,cost_pips=2.8)
            baseline=simulate(self.ind,gene,start,end,cost_pips=2.8)
            self.assertAlmostEqual(100*math.expm1(sum(v for _,v in rec)),baseline['return_pct'],places=5)
            self.assertTrue(all(start<=d<end for d,_ in rec))

    def test_block_bootstrap_is_seed_deterministic(self):
        x=np.random.default_rng(42).normal(0,.002,(1000,4))
        a=max_t_block_bootstrap(x,seed=21,reps=128)
        b=max_t_block_bootstrap(x,seed=21,reps=128)
        self.assertEqual(a,b)
        self.assertEqual(a['candidate_count'],4)
        self.assertEqual(a['block_length_observations'],20)

    def test_identical_or_uninformative_candidate_does_not_pass(self):
        x=np.zeros((500,3))
        r=max_t_block_bootstrap(x,reps=128)
        self.assertEqual(r['candidate_max_t_adjusted_p'],[1.,1.,1.])
        self.assertEqual(r['candidate_mean_daily_log_excess'],[0.,0.,0.])

    def test_current_family_only_and_historic_trials_fail_closed(self):
        fresh=audit_candidates(self.ind,self.genes,reps=128,seed=40,
                               total_search_trials=len(self.genes))
        older=audit_candidates(self.ind,self.genes,reps=128,seed=40,
                               total_search_trials=len(self.genes)+3)
        self.assertEqual(fresh['status'],'SCREENED_CURRENT_FAMILY')
        self.assertEqual(older['status'],'HISTORIC_TRIAL_FAMILY_INCOMPLETE')
        self.assertEqual(older['passed_genes'],[])
        self.assertFalse(older['approved_for_live_trading'])

    def test_future_price_mutation_cannot_change_audit(self):
        old=audit_candidates(self.ind,self.genes,reps=128,seed=41)
        changed=[p.copy() for p in self.points]
        for p in changed:
            if p['date']>='2021-01-01': p['close']*=5.
        next_=audit_candidates(Indicators(changed),self.genes,reps=128,seed=41)
        self.assertEqual(old,next_)

    def test_duplicate_or_truncated_trial_set_refused(self):
        r=audit_candidates(self.ind,[self.genes[0],self.genes[0]],reps=128)
        self.assertEqual(r['status'],'DUPLICATE_OR_MISSING_TRIALS')
        self.assertEqual(r['passed_genes'],[])
        self.assertEqual(audit_candidates(self.ind,[],reps=128)['status'],
                         'INCOMPLETE_CANDIDATE_UNIVERSE')

    def test_rank_audit_is_descriptive_only(self):
        zeros=np.zeros((1200,4))
        r=cscv_like_rank_instability(zeros)
        self.assertEqual(r['splits'],70)
        self.assertIn('DESCRIPTIVE_ONLY',r['state'])
        self.assertGreaterEqual(r['fraction'],0)
        self.assertLessEqual(r['fraction'],1)

    def test_chronological_fixed_library_cannot_train_on_test(self):
        r=chronological_reference_diagnostic(self.ind,cost_pips=2.8)
        self.assertEqual(len(r['periods']),6)
        self.assertFalse(r['used_to_select_evolved_candidate'])
        for fold in r['periods']:
            self.assertLess(fold['train_through'],fold['test_start'])
            self.assertLessEqual(fold['test_end'],'2020-12-31')


if __name__=='__main__':unittest.main()

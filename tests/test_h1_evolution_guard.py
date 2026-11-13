"""Deterministic fail-closed integration tests; all quote prices here are synthetic."""
import json
import math
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from execution_lab import ExecutionConfig, load_quotes, make_fixture, sha256_file, write_json
from h1_evolution_guard import (BASELINE, catalog_payload, fixture_catalog, fold_log_returns,
                                load_catalog, research_audit, signal_book, boundaries)
from self_evolve import Genome


class H1EvolutionGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        cls.csv,cls.prov=make_fixture(cls.root,n=1200,seed=42)
        cls.quotes,cls.meta,cls.audit=load_quotes(cls.csv,cls.prov,allow_synthetic=True)
        cls.cat_file=cls.root/'universe.json'
        cls.cat=fixture_catalog(cls.cat_file)
        cls.genes,cls.cat=load_catalog(cls.cat_file)
        cls.cfg=ExecutionConfig(slippage_pips_per_side=.2,commission_jpy_per_roundtrip=1)
        cls.report=research_audit(cls.quotes,cls.meta,cls.audit,cls.genes,cls.cat,cls.cfg,reps=64)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_never_approves_fixture_and_has_chronological_folds(self):
        r=self.report
        self.assertEqual(r['status'],'SYNTHETIC_TEST_ONLY')
        self.assertFalse(r['approved_for_live_trading'])
        self.assertFalse(r['eligible_for_paper_promotion'])
        self.assertEqual(r['statistically_screened_genes_for_RESEARCH_ONLY'],[])
        self.assertEqual(r['fold_dates']['research']['bars'],720)
        self.assertEqual(r['fold_dates']['validation']['bars'],240)
        self.assertEqual(r['fold_dates']['diagnostic_only']['bars'],240)
        self.assertEqual(r['research_bootstrap']['candidate_count'],len(self.genes))

    def test_determinism_of_statistical_report(self):
        r=research_audit(self.quotes,self.meta,self.audit,self.genes,self.cat,self.cfg,reps=64)
        self.assertEqual(self.report,r)

    def test_catalog_checksum_and_duplicate_rejected(self):
        bad=self.root/'tampered.json'
        altered=self.cat.copy();altered['historical_trials_total']+=1
        write_json(bad,altered)
        with self.assertRaisesRegex(ValueError, 'hash'):
            load_catalog(bad)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            catalog_payload([BASELINE,BASELINE],source_kind='SYNTHETIC_TEST_FIXTURE',
                            historical_trials_total=2,selected_before='TEST_ONLY')

    def test_incomplete_historical_trials_block_selection(self):
        cat=catalog_payload(self.genes,source_kind='SYNTHETIC_TEST_FIXTURE',
                            historical_trials_total=len(self.genes)+10,selected_before='TEST_ONLY')
        r=research_audit(self.quotes,self.meta,self.audit,self.genes,cat,self.cfg,reps=64)
        self.assertEqual(r['statistically_screened_genes_for_RESEARCH_ONLY'],[])
        self.assertFalse(r['approved_for_live_trading'])

    def test_future_diagnostic_quotes_never_influence_research_or_validation(self):
        changed=self.quotes.copy(deep=True)
        cutoff=boundaries(len(changed))['diagnostic_only'][0]
        # Signal formulas use strictly historical closes. Modify only diagnostic data.
        for name in ['bid_open','bid_high','bid_low','bid_close','ask_open','ask_high','ask_low','ask_close']:
            changed.loc[cutoff:,name]*=1.03
        original=self.report
        new=research_audit(changed,self.meta,self.audit,self.genes,self.cat,self.cfg,reps=64)
        self.assertEqual(original['research_bootstrap'],new['research_bootstrap'])
        self.assertEqual([v['validation'] for v in original['candidate_evaluations']],
                         [v['validation'] for v in new['candidate_evaluations']])

    def test_returns_match_execution_ledger(self):
        ret,details=fold_log_returns(self.quotes,BASELINE,self.cfg,0,720)
        self.assertAlmostEqual(details['return_pct'],100*math.expm1(float(ret.sum())),places=5)
        self.assertTrue(np.isfinite(ret).all())
        self.assertEqual(len(ret),720)

    def test_no_trade_crosses_split_and_no_leak_from_future_signals(self):
        signals=signal_book(self.quotes,BASELINE,720,960)
        self.assertTrue(all(720<=i and i+2<960 for i in signals))
        a=signal_book(self.quotes,BASELINE,0,720)
        altered=self.quotes.copy()
        altered.loc[720:,'bid_close']+=20
        altered.loc[720:,'ask_close']+=20
        b=signal_book(altered,BASELINE,0,720)
        self.assertEqual(a,b)

    def test_externally_claimed_broker_requires_fred_derived_catalog(self):
        cat=catalog_payload(self.genes,source_kind='SYNTHETIC_TEST_FIXTURE',
                            historical_trials_total=len(self.genes),selected_before='TEST_ONLY')
        with self.assertRaisesRegex(ValueError,'archived FRED-selection'):
            research_audit(self.quotes, self.meta,
                           {**self.audit,'data_kind':'BROKER_BID_ASK_H1'},
                           self.genes,cat,self.cfg,reps=64)

    def test_synthetic_quote_provenance_cannot_be_silently_accepted_as_real(self):
        with self.assertRaisesRegex(ValueError,'allow-synthetic'):
            load_quotes(self.csv,self.prov)
        self.assertEqual(sha256_file(self.csv),self.audit['quotes_sha256'])

if __name__=='__main__':unittest.main()

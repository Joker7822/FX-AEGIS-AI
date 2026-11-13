"""Self-evolution controls use synthetic data only; no market performance claims."""
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from self_evolve import (Genome, Indicators, crossover, evaluate, evolve, gene_key,
                         mutate, prefix_hash, random_gene, run, shadow_status,
                         simulate)
import random


class SelfEvolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name)
        cls.dates = pd.bdate_range("2006-11-13", "2026-11-06")
        cls.points = [{"date": f"{d:%Y-%m-%d}",
                       "close": 110.0 + .007*i + (i%190)/65.0}
                      for i,d in enumerate(cls.dates)]
        cls.csv = cls.path/"fixture.csv"
        cls.csv.write_text("date,close\n"+"".join(f"{p['date']},{p['close']:.6f}\n" for p in cls.points),encoding="utf-8")

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_invalid_genomes_rejected(self):
        for g in ({"fast": 150}, {"family": "exec-python"}, {"allocation": 1.0},
                  {"allow_short": "true"}, {"vol_cap_pct": -2}):
            with self.assertRaises(ValueError): Genome(**g)

    def test_mutation_and_crossover_always_valid(self):
        rng = random.Random(42)
        for _ in range(100):
            a, b = random_gene(rng), random_gene(rng)
            child = crossover(a, b, rng)
            another = mutate(child, rng)
            self.assertIsInstance(another, Genome)
            self.assertEqual(another, Genome(**asdict(another)))

    def test_same_seed_same_candidate_results(self):
        ind = Indicators(self.points)
        a = evolve(ind, population=6, generations=2, seed=123)
        b = evolve(ind, population=6, generations=2, seed=123)
        self.assertEqual(a, b)
        self.assertGreaterEqual(a['evaluated'], 3)

    def test_future_mutation_does_not_change_selection(self):
        original = evolve(Indicators(self.points), seed=2, population=7, generations=2)
        changed = [p.copy() for p in self.points]
        for p in changed:
            if p['date'] >= '2021-01-01':
                p['close'] = 160.0 + float(p['date'][-2:])/3
        retest = evolve(Indicators(changed), seed=2, population=7, generations=2)
        self.assertEqual(original, retest)

    def test_preceding_price_only_matters_for_previous_signal(self):
        gene = Genome(family='ema_cross',fast=15,slow=100)
        base = simulate(Indicators(self.points),gene,'2016-01-01','2019-01-01')
        changed = [p.copy() for p in self.points]
        for p in changed:
            if p['date'] >= '2019-01-01': p['close']*=4
        self.assertEqual(base, simulate(Indicators(changed),gene,'2016-01-01','2019-01-01'))

    def test_no_fresh_data_never_passes_shadow_gate(self):
        ind = Indicators(self.points)
        r = shadow_status(ind,Genome(),self.points[-1]['date'],base_cost=1.4)
        self.assertEqual(r['state'],'AWAITING_NEW_OBSERVATIONS')
        self.assertFalse(r['approved_for_live_trading'])

    def test_fixture_run_produces_only_research_report(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            r=run(self.csv, root/'no_metadata', root/'report.json',root/'challenge.json',
                  root/'paper.json',root/'site.json',fixture=True, population=6,generations=2)
            data=json.loads((root/'site.json').read_text())
            self.assertFalse(r['approved_for_live_trading'])
            self.assertFalse(data['approved_for_live_trading'])
            self.assertEqual(data['source_kind'],'SYNTHETIC_FIXTURE_NOT_FOR_TRADING')
            self.assertEqual(r['search_window_end_exclusive'],'2021-01-01')
            self.assertEqual(r['sha256'],json.loads((root/'report.json').read_text())['sha256'])

    def test_missing_source_provenance_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaisesRegex(ValueError,'Missing FRED'):
                run(self.csv,root/'missing.json',root/'report.json',root/'challenger.json',
                    root/'paper.json',root/'site.json',population=4,generations=1)

    def test_challenger_historical_revision_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            challenger={"schema":"fx-aegis-self-evolve-1","mode":"paper-only","fixture":True,
                        "gene":asdict(Genome()),"discovery_end":"2026-11-06",
                        "snapshot_prefix_sha256":"not-a-valid-hash","no_live_orders":True}
            (root/'challenger.json').write_text(json.dumps(challenger))
            with self.assertRaisesRegex(ValueError,'revised'):
                run(self.csv,root/'missing',root/'report.json',root/'challenger.json',
                    root/'paper.json',root/'site.json',fixture=True,population=4,generations=1)

    def test_shadow_threshold_not_bypassed(self):
        # 2027 future rows newly arriving, fewer than 126 observations.
        p=self.points.copy()
        p.extend({'date':f'{d:%Y-%m-%d}','close':180+i/1000}
                 for i,d in enumerate(pd.bdate_range('2026-11-09',periods=100)))
        r=shadow_status(Indicators(p),Genome(),'2026-11-06',base_cost=1.4)
        self.assertEqual(r['state'],'AWAITING_NEW_OBSERVATIONS')
        self.assertEqual(r['new_observations'],100)


if __name__=='__main__':unittest.main()

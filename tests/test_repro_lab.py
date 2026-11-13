"""No-network reproducibility tests, using explicitly synthetic observations."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from github_pipeline import simulate_reference
from repro_lab import canonical, capture, config, evaluate, scan, sha256, simulate_window, verify


class ReproducibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        dates = pd.bdate_range('2006-11-13', '2026-11-06')
        cls.data = cls.root / 'synthetic.csv'
        cls.data.write_text('date,close\n' + ''.join(
            f'{day:%Y-%m-%d},{(116 + i*.008 + 6*((i%103)/103)):.6f}\n'
            for i, day in enumerate(dates)), encoding='utf-8')
        cls.points = [{'date': day.strftime('%Y-%m-%d'),
                       'close': 116 + i*.008 + 6*((i%103)/103)}
                      for i, day in enumerate(dates)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_refuse_unattributed_dataset(self):
        with self.assertRaisesRegex(ValueError, 'Missing FRED'):
            capture(self.data, self.root / 'missing.json', self.root/'proof.json', config())

    def test_same_run_same_digest_and_verify(self):
        a = capture(self.data, self.root/'absent', self.root/'a.json', config(), fixture=True)
        b = capture(self.data, self.root/'absent', self.root/'b.json', config(), fixture=True)
        self.assertEqual(a['record_sha256'], b['record_sha256'])
        self.assertEqual(verify(self.root/'a.json', self.data, self.root/'absent')['verified'], True)
        self.assertEqual(a['record']['manifest']['source']['kind'], 'SYNTHETIC_FIXTURE_NOT_FOR_TRADING')

    def test_dataset_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.csv'; p.write_bytes(self.data.read_bytes())
            proof=Path(d)/'proof.json'
            capture(p, self.root/'missing', proof, config(), fixture=True)
            p.write_text(p.read_text().replace('116.000000','116.100000',1))
            with self.assertRaisesRegex(ValueError,'differ'):
                verify(proof,p,self.root/'missing')

    def test_proof_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            proof=Path(d)/'proof.json'; capture(self.data,self.root/'missing',proof,config(),fixture=True)
            raw=json.loads(proof.read_text());raw['record']['result']['approved_for_live_trading']=True
            proof.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'modified'):
                verify(proof,self.data,self.root/'missing')

    def test_window_equivalent_to_original_on_full_sample(self):
        cfg=config(15,100,.25,1.4,True)
        original=simulate_reference(self.points,fast=15,slow=100,allocation=.25,spread_pips=1.4)
        res=simulate_window(self.points,cfg,'2006-11-13','9999-01-01')
        self.assertAlmostEqual(res['return_pct'], original['return_pct'],places=3)
        self.assertAlmostEqual(res['max_dd_pct'],original['max_drawdown_pct'],places=3)
        self.assertEqual(res['position_changes'],original['position_changes'])

    def test_future_changed_does_not_change_prior_window(self):
        cfg=config()
        baseline=simulate_window(self.points,cfg,'2016-01-01','2021-01-01')
        revised=[p.copy() for p in self.points]
        revised[-1]['close']=2000
        res=simulate_window(revised,cfg,'2016-01-01','2021-01-01')
        self.assertEqual(baseline,res)

    def test_scan_selects_only_on_training_and_validation(self):
        first=scan(self.data,self.root/'missing',self.root/'scan1.json',fixture=True)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.csv'; rows=self.data.read_text().splitlines()
            # Modify ONLY holdout data; ranking must remain identical.
            p.write_text('\n'.join(
                x if i==0 or x[:10]<'2021-01-01' else x[:11]+'181.000000'
                for i,x in enumerate(rows))+'\n')
            second=scan(p,self.root/'missing',Path(d)/'scan2.json',fixture=True)
        self.assertEqual(first['candidates_ranked_without_holdout'],second['candidates_ranked_without_holdout'])
        self.assertEqual(first['holdout_examined_once_for_top_candidate']['config'],
                         second['holdout_examined_once_for_top_candidate']['config'])
        self.assertFalse(first['approved_for_live_trading'])

    def test_invalid_config_rejected(self):
        for args in ({'fast':100,'slow':30},{'allocation':1.2},{'spread_pips':-1}):
            with self.assertRaises(ValueError): config(**args)


if __name__=='__main__': unittest.main()

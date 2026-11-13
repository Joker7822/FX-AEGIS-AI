"""Offline tests: no network and no unverified data misrepresented as real."""
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from github_pipeline import read_reference, make_site, simulate_reference, refresh_fred


class GitHubPipelineTests(unittest.TestCase):
    def setUp(self):
        self.points = [
            {'date': day.strftime('%Y-%m-%d'),'close':100 + i*.02 + (i%11)*.08}
            for i,day in enumerate(pd.date_range('2006-11-13',periods=700,freq='B'))
        ]

    def test_no_future_leakage_and_costs_change_result(self):
        a=simulate_reference(self.points,fast=20,slow=80,spread_pips=0)
        b=simulate_reference(self.points,fast=20,slow=80,spread_pips=5)
        self.assertGreaterEqual(a['final_capital'],b['final_capital'])
        self.assertEqual(a['first_sim_date'],self.points[81]['date'])
        self.assertEqual(a['last_sim_date'],self.points[-1]['date'])
        self.assertTrue(a['data_kind'].startswith('FRED'))

    def test_future_changes_do_not_affect_prior_curve(self):
        left=simulate_reference(self.points[:-1],fast=20,slow=80)
        changed=self.points.copy();changed[-1]={**changed[-1],'close':190}
        right=simulate_reference(changed,fast=20,slow=80)
        self.assertEqual(left['curve'],right['curve'][:-1])

    def test_reject_invalid_simulation_parameters(self):
        for args in ({'fast':100,'slow':30}, {'spread_pips':-1}, {'allocation':1.5}, {'capital':-1}):
            with self.assertRaises(ValueError):simulate_reference(self.points,**args)

    def test_empty_site_never_fabricates_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); result=make_site(root/'docs',root/'missing.csv',root/'missing.json')
            self.assertEqual(result['status'],'NO_VERIFIED_HISTORY')
            payload=json.loads((root/'docs/data/research.json').read_text())
            self.assertEqual(payload['reference_daily'],[])
            self.assertIsNone(payload['default_simulation'])

    def test_source_checksum_protected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'data.csv'; meta=root/'meta.json'
            p.write_text('date,close\n2006-11-13,100\n2006-11-14,101\n')
            meta.write_text(json.dumps({'csv_sha256':'wrong'}))
            with self.assertRaisesRegex(ValueError,'hash'):make_site(root/'docs',p,meta)
            meta.write_text(json.dumps({'csv_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
            out=make_site(root/'docs',p,meta)
            self.assertEqual(out['status'],'FRED_REFERENCE_AVAILABLE')

    def test_refresh_official_metadata_and_csv_no_fabrication(self):
        data=pd.DataFrame({'date':pd.date_range('2006-11-13',periods=300,freq='B'),'close':[115.0+i*.01 for i in range(300)]})
        source={'payload_sha256':'abc' * 21 + 'a'}
        with tempfile.TemporaryDirectory() as d,patch('github_pipeline.fetch_fred',return_value=(data,source)) as fetch:
            file=Path(d)/'dataset.csv';meta=Path(d)/'meta.json'
            record=refresh_fred(file,meta,utc_today=date(2026,11,13))
            self.assertEqual(record['rows'],300)
            self.assertEqual(read_reference(file)[0]['date'],'2006-11-13')
            self.assertEqual(record['csv_sha256'],hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertEqual(fetch.call_args.kwargs['end'],date(2026,11,13))
            self.assertNotIn('open',file.read_text().splitlines()[0])


if __name__=='__main__':unittest.main()

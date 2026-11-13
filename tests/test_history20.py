"""Tests for 20-year reference data handling and strict hourly coverage gates."""
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from generate_demo import generate
from history20 import (fred_table, crop_fred, daily_report, validate_hourly,
                       import_hourly, trade_years, fetch_fred, save_daily_results)
from main import Config, load_ohlc, make_features, walk_forward_predictions


class History20Tests(unittest.TestCase):
    def test_fred_csv_nan_and_reference_only(self):
        payload = (b'observation_date,DEXJPUS\n2006-11-13,117.40\n'
                   b'2006-11-14,.\n2006-11-15,117.02\n')
        series = fred_table(payload)
        self.assertEqual(series['close'].notna().sum(), 2)
        self.assertEqual(series.iloc[1]['date'].strftime('%Y-%m-%d'), '2006-11-14')
        self.assertEqual(series.iloc[2]['close'], 117.02)
        with self.assertRaises(ValueError):
            fred_table(b'DATE,DEXJPUS\n2006-11-14,10\n2006-11-14,11\n')
        with self.assertRaises(ValueError):
            fred_table(b'DATE,DEXJPUS\n2006-11-14,-10\n')
        with self.assertRaises(ValueError):
            fred_table(b'other,DEXJPUS\n2006-11-14,10\n')

    def test_fred_yearly_summary_never_creates_ohlc(self):
        dates = pd.bdate_range('2006-11-13', periods=400)
        raw = pd.DataFrame({'date': dates, 'close': 110 + np.arange(len(dates))*.01})
        with tempfile.TemporaryDirectory() as tmp:
            info = save_daily_results(raw, {'source': 'SYNTHETIC_TEST_FIXTURE'}, tmp,
                                      date(2006,11,13), date(2008,11,13))
            self.assertFalse(info['has_full_requested_span'])
            self.assertEqual(info['provenance']['source'],'SYNTHETIC_TEST_FIXTURE')
            self.assertTrue((Path(tmp)/'reference_regimes.csv').is_file())
            self.assertNotIn('open', pd.read_csv(Path(tmp)/'fred_reference_daily.csv').columns)
            self.assertEqual(len(pd.read_csv(Path(tmp)/'reference_years.csv')), 3)

    def test_hourly_timezone_must_be_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'input.csv'
            output = Path(root)/'output.csv'
            bars = pd.date_range('2025-01-01',periods=1005,freq='h')
            pd.DataFrame({'Time': bars.strftime('%Y-%m-%d %H:%M:%S'),
                          'Open':150.0,'High':150.2,'Low':149.9,'Close':150.1}).to_csv(path,index=False)
            with self.assertRaisesRegex(ValueError,'--source-timezone'):
                import_hourly(path, output)
            record=import_hourly(path, output, 'UTC')
            self.assertEqual(record['rows'],1005)
            self.assertTrue(pd.read_csv(output).iloc[0]['time'].endswith('Z'))
            audit=validate_hourly(output)
            self.assertFalse(audit['coverage_ready_for_20y_research'])
            self.assertFalse(audit['is_20y_data_authenticated'])

    def test_hourly_frequency_gate_rejects_daily_disguised_as_h1(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'fake_hour.csv'
            times=pd.date_range('2010-01-01',periods=1100,freq='D',tz='UTC')
            pd.DataFrame({'time':times.strftime('%Y-%m-%dT%H:%M:%SZ'),
                          'open':100,'high':101,'low':99,'close':100}).to_csv(p,index=False)
            with self.assertRaisesRegex(ValueError,'plausible H1'):
                validate_hourly(p)

    def test_dashboard_exposes_separate_reference_series(self):
        from dashboard import build_payload
        with tempfile.TemporaryDirectory() as root:
            tmp=Path(root)
            daily=tmp/'fred_reference_daily.csv'
            pd.DataFrame([{'date':'2006-11-13','close':117.40},
                          {'date':'2006-11-14','close':None},
                          {'date':'2006-11-15','close':117.02}]).to_csv(daily,index=False)
            summary=tmp/'history20_summary.json'
            summary.write_text(json.dumps({'actual_start':'2006-11-13',
                                           'actual_end':'2006-11-15',
                                           'rows_with_reference_rate':2,
                                           'has_full_requested_span':False}))
            project=Path(__file__).resolve().parents[1]
            p=build_payload(project/'results/demo_trend',
                            project/'data/demo_synthetic_usdjpy.csv',
                            'test', history_report=summary, reference_daily=daily)
            self.assertEqual(len(p['reference_daily']),2)
            self.assertEqual(p['history20']['daily_observations'],2)
            self.assertFalse(p['history20']['daily_full_span'])
            self.assertEqual(p['market'][0]['o'] is not None,True)

    def test_official_api_requires_key_without_network(self):
        with self.assertRaisesRegex(ValueError,'FRED_API_KEY'):
            fetch_fred('api', None)

    def test_trade_years_from_existing_logs(self):
        with tempfile.TemporaryDirectory() as root:
            r=Path(root)/'res'; r.mkdir()
            pd.DataFrame([{'exit_time_utc':'2022-01-04T00:00:00Z','net_pnl_yen':100},
                          {'exit_time_utc':'2022-06-04T00:00:00Z','net_pnl_yen':-70}]).to_csv(r/'validation_trades.csv',index=False)
            pd.DataFrame([{'exit_time_utc':'2025-08-08T00:00:00Z','net_pnl_yen':-40}]).to_csv(r/'test_trades.csv',index=False)
            info=trade_years(r,Path(root)/'years')
            self.assertEqual(len(info['negative_years']),1)
            self.assertEqual(info['negative_years'][0]['year'],2025)

    def test_bounded_history_training_does_not_reference_future(self):
        # Exercise rolling-history slicing while preserving one-label purge.
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'gen.csv'
            generate(p,n=1100,seed=21)
            d=make_features(load_ohlc(p))
            cfg=Config(max_training_bars=320,retrain_every_bars=100)
            pred=walk_forward_predictions(d,710,715,cfg)
            self.assertEqual(int(pred['training_rows'].iloc[0]), 320)
            self.assertLessEqual(pd.Timestamp(pred['fit_until_utc'].iloc[0]),pd.Timestamp(d.iloc[710]['time']))


if __name__ == '__main__':
    unittest.main()

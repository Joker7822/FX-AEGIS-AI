"""Extra unit checks for as-of timestamps, offline filters and web parsing."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from main import Config, backtest
from trading_conditions import apply_gates, load_events, add_market_regime
from research_agent import parse_bls_calendar, search


class ResearchAgentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.t = pd.Timestamp('2026-11-13T13:00:00Z')
        self.entry = self.t + pd.Timedelta(hours=1)
        self.d = pd.DataFrame({
            'time': [self.t], 'entry_time': [self.entry],
            'close': [150.], 'ema50': [151.], 'ema200': [149.],
            'adx14': [32.], 'atr14_pips': [20.], 'spread_pips': [.6]
        })
        self.pred = pd.DataFrame({
            'side': [1], 'predicted_abs_return': [.001],
            'p_up_trend': [.63], 'p_up_reversion': [.62],
            'volatility_veto': [False], 'volatility_cap': [.01]
        })

    def tearDown(self):
        self.tempdir.cleanup()

    def events(self, known_at):
        path = self.root/'events.csv'
        pd.DataFrame([{
            'event_time_utc': (self.entry + pd.Timedelta(minutes=30)).isoformat(),
            'known_at_utc': pd.Timestamp(known_at).isoformat(),
            'event': 'Employment Situation',
            'source_url': 'https://www.bls.gov/schedule/news_release/bls.ics',
        }]).to_csv(path,index=False)
        return load_events(path)

    def test_news_veto_only_if_schedule_was_known_at_signal(self):
        p = apply_gates(self.d, self.pred, Config(), self.events('2026-11-10T00:00:00Z'))
        self.assertEqual(int(p.at[0,'side']), 0)
        self.assertTrue(bool(p.at[0,'event_veto']))
        self.assertEqual(p.at[0,'filter_reason'], 'KNOWN_HIGH_IMPACT_EVENT')
        # The same announcement observed tomorrow cannot affect today's decision.
        future_known = self.events('2026-11-13T13:30:00Z')
        p = apply_gates(self.d, self.pred, Config(), future_known)
        self.assertEqual(int(p.at[0,'side']), 1)
        self.assertFalse(bool(p.at[0,'event_veto']))

    def test_news_untrusted_host_rejected(self):
        path = self.root/'bad.csv'
        pd.DataFrame([{
            'event_time_utc': self.entry.isoformat(),
            'known_at_utc': self.t.isoformat(),
            'event': 'fake', 'source_url': 'https://bls.gov.evil.example/announcement'
        }]).to_csv(path, index=False)
        with self.assertRaises(ValueError):
            load_events(path)

    def test_spread_and_trend_veto_independent(self):
        p = apply_gates(self.d.assign(spread_pips=3.), self.pred, Config())
        self.assertEqual(p.at[0, 'filter_reason'], 'SPREAD_VETO')
        p = apply_gates(self.d.assign(ema50=140.), self.pred, Config())
        self.assertEqual(p.at[0, 'filter_reason'], 'TREND_REGIME_VETO')

    def test_no_late_indicators_leak(self):
        x = np.linspace(145, 155, 400)
        df = pd.DataFrame({'close': x, 'high': x + .2, 'low': x - .2})
        a = add_market_regime(df)
        df.loc[350:, ['close','high','low']] *= 1.1
        b = add_market_regime(df)
        for c in ('ema50','ema200','adx14','atr14_pips'):
            np.testing.assert_allclose(a[c].iloc[210:340], b[c].iloc[210:340], equal_nan=True)

    def test_bls_calendar_ics_and_asof(self):
        sample = '''BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261204T083000
SUMMARY:Employment Situation for November 2026
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261203T083000
SUMMARY:Unimportant Example
END:VEVENT
END:VCALENDAR'''
        date = datetime(2026,11,13,0,0,tzinfo=timezone.utc)
        d = parse_bls_calendar(sample, date)
        self.assertEqual(len(d), 1)
        self.assertEqual(pd.Timestamp(d.iloc[0]['event_time_utc']), pd.Timestamp('2026-12-04T13:30:00Z'))
        self.assertEqual(pd.Timestamp(d.iloc[0]['known_at_utc']), pd.Timestamp(date))

    def test_search_requires_key(self):
        with self.assertRaises(ValueError):
            search('FOMC', self.root/'out.json', '')


if __name__ == '__main__':
    unittest.main()

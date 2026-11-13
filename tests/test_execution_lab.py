"""No broker credentials, network, or real orders. Unit-test fill/cost safeguards."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from execution_lab import (ExecutionConfig, SCHEMA, COLUMNS, ema_signals, financing,
                           load_quotes, load_rollovers, load_signals, make_fixture,
                           sha256_file, simulate, uncertain_rollover_window, write_json)


class ExecutionLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.quotes, self.meta = make_fixture(self.root, n=230, seed=7)

    def audit(self):
        return load_quotes(self.quotes, self.meta, allow_synthetic=True)

    def test_fixture_is_deterministic_and_provenance_matches(self):
        q2, m2 = make_fixture(self.root/'again', n=230, seed=7)
        self.assertEqual(sha256_file(q2), sha256_file(self.quotes))
        df, meta, audit = self.audit()
        self.assertEqual(len(df), 230)
        self.assertEqual(audit['data_kind'], 'SYNTHETIC_FIXTURE')
        self.assertEqual(meta['sha256'], audit['quotes_sha256'])

    def test_synthetic_requires_explicit_flag(self):
        with self.assertRaisesRegex(ValueError, 'allow-synthetic'):
            load_quotes(self.quotes, self.meta)

    def test_tampering_fails_hash_check(self):
        with self.quotes.open('a') as f:
            f.write('\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.audit()

    def test_fred_or_unknown_kind_rejected(self):
        data = json.loads(self.meta.read_text())
        data['source_kind'] = 'FRED_DAILY_REFERENCE'
        write_json(self.meta,data)
        with self.assertRaisesRegex(ValueError, 'FRED'):
            self.audit()

    def test_invalid_spread_rejected_even_if_hash_is_updated(self):
        d = pd.read_csv(self.quotes)
        d.loc[3,'ask_open'] = d.loc[3,'bid_open'] - .01
        d.to_csv(self.quotes,index=False)
        data = json.loads(self.meta.read_text())
        data['sha256'] = sha256_file(self.quotes)
        write_json(self.meta,data)
        with self.assertRaisesRegex(ValueError, 'Ask cannot'):
            self.audit()

    def test_no_naive_utc_timestamp(self):
        d = pd.read_csv(self.quotes)
        d.loc[3,'time'] = '2023-01-02 03:00:00'
        d.to_csv(self.quotes,index=False)
        data = json.loads(self.meta.read_text())
        data['sha256'] = sha256_file(self.quotes)
        write_json(self.meta,data)
        with self.assertRaisesRegex(ValueError, 'explicit UTC'):
            self.audit()

    def test_non_h1_sample_is_rejected(self):
        d = pd.read_csv(self.quotes)
        times = pd.date_range('2023-01-02', periods=len(d), freq='D',tz='UTC')
        d['time'] = [t.isoformat() for t in times]
        d.to_csv(self.quotes,index=False)
        data = json.loads(self.meta.read_text())
        data['sha256'] = sha256_file(self.quotes)
        write_json(self.meta,data)
        with self.assertRaisesRegex(ValueError, 'H1'):
            self.audit()

    def test_quote_fill_long_pays_spread_and_slippage(self):
        d,_,_ = self.audit()
        # Isolate exactly one trade; no overnight, no weekend in first three bars.
        cfg=ExecutionConfig(allocation=.25,lot_step_usd=1000,slippage_pips_per_side=.2)
        r=simulate(d,{0:1},cfg)
        self.assertEqual(r['trades'],1)
        t=r['ledger'][0]
        self.assertAlmostEqual(t['entry_fill_jpy_per_usd'],d.loc[1,'ask_open']+.002,places=5)
        self.assertAlmostEqual(t['exit_fill_jpy_per_usd'],d.loc[2,'bid_open']-.002,places=5)
        expected=(d.loc[2,'bid_open']-.002-d.loc[1,'ask_open']-.002)*t['units_usd']
        self.assertAlmostEqual(t['net_pnl_jpy'], expected,places=3)

    def test_short_uses_bid_entry_and_ask_exit(self):
        d,_,_=self.audit()
        r=simulate(d,{0:-1},ExecutionConfig(slippage_pips_per_side=.2))
        t=r['ledger'][0]
        self.assertAlmostEqual(t['entry_fill_jpy_per_usd'],d.loc[1,'bid_open']-.002,places=5)
        self.assertAlmostEqual(t['exit_fill_jpy_per_usd'],d.loc[2,'ask_open']+.002,places=5)

    def test_high_commission_reduces_net_pnl(self):
        d,_,_=self.audit()
        a=simulate(d,{0:1},ExecutionConfig(commission_jpy_per_roundtrip=0))
        b=simulate(d,{0:1},ExecutionConfig(commission_jpy_per_roundtrip=200))
        self.assertAlmostEqual(a['ledger'][0]['net_pnl_jpy']-b['ledger'][0]['net_pnl_jpy'],200,places=3)

    def test_rollover_without_ledger_vetoes_even_same_utc_date(self):
        d,_,_=self.audit()
        self.assertTrue(uncertain_rollover_window(d.iloc[21]['time'],d.iloc[22]['time']))
        r=simulate(d,{20:1},ExecutionConfig())
        self.assertEqual(r['trades'],0)
        self.assertEqual(r['skips_by_reason'].get('UNKNOWN_ROLLOVER'),1)

    def test_rollover_schedule_applies_signed_swap_per_units(self):
        d,_,_=self.audit()
        # The signal at 20:00 enters 21:00, exits 22:00; event inside holding period.
        row={'date_utc':'2023-01-02','rollover_time_utc':'2023-01-02T21:30:00Z',
             'swap_long_jpy_per_1000usd':-5.0,'swap_short_jpy_per_1000usd':1.0}
        p=self.root/'rollovers.csv'
        pd.DataFrame([row]).to_csv(p,index=False)
        ledger=load_rollovers(p)
        r=simulate(d,{20:1},ExecutionConfig(),rollovers=ledger)
        self.assertEqual(r['trades'],1)
        self.assertAlmostEqual(r['ledger'][0]['swap_jpy'],-5*r['ledger'][0]['units_usd']/1000.)

    def test_missing_rollover_coverage_vetoes(self):
        d,_,_=self.audit()
        r=simulate(d,{20:1},ExecutionConfig(),rollovers={})
        self.assertEqual(r['trades'],0)
        self.assertEqual(r['skips_by_reason']['MISSING_SWAP_COVERAGE'],1)

    def test_gap_blocks_idealized_fill(self):
        d,_,_=self.audit()
        gap_idx=next(i for i in range(1,len(d)) if d.iloc[i]['time']-d.iloc[i-1]['time']>pd.Timedelta(hours=1))
        result=simulate(d,{gap_idx-2:1},ExecutionConfig())
        self.assertEqual(result['skips_by_reason'].get('GAP_OR_WEEKEND'),1)

    def test_ema_signals_use_only_completed_bar(self):
        d,_,_=self.audit()
        first=ema_signals(d,5,30,allow_short=True)
        shifted=d.copy()
        shifted.loc[180:,'bid_close']+=10
        shifted.loc[180:,'ask_close']+=10
        second=ema_signals(shifted,5,30,allow_short=True)
        for i in range(30,180):
            self.assertEqual(first[i],second[i])

    def test_external_signal_rejects_duplicate_timestamps(self):
        d,_,_=self.audit()
        p=self.root/'signals.csv'
        pd.DataFrame([{'bar_open_time':d.iloc[1]['time'].isoformat(),'side':1},
                      {'bar_open_time':d.iloc[1]['time'].isoformat(),'side':-1}]).to_csv(p,index=False)
        with self.assertRaisesRegex(ValueError, 'unique'):
            load_signals(p,d)

    def test_risk_rejects_overly_large_allocation(self):
        with self.assertRaisesRegex(ValueError,'Out-of-range'):
            ExecutionConfig(allocation=1).check()


if __name__ == '__main__':
    unittest.main()

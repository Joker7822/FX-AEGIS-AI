"""Offline contract tests for ML-to-chart translation (no real market data)."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from execution_lab import load_quotes, make_fixture
from ml_chart_overlay import export_overlay


class ChartExportContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.q, self.meta = make_fixture(root/'fixture', n=1250)
        quotes, metadata, audit = load_quotes(self.q, self.meta, allow_synthetic=True)
        self.quotes = quotes
        self.folder = root/'ml'
        self.folder.mkdir()
        self.output = root/'annotations.json'
        self.ival = next(i for i in range(750, 998)
                         if quotes.iloc[i+2]['time']-quotes.iloc[i]['time'] == pd.Timedelta(hours=2))
        i = self.ival
        t = lambda j: quotes.iloc[j]['time'].isoformat()
        self.pred = dict(signal_index=i, signal_bar_time_utc=t(i), entry_bar_time_utc=t(i+1),
                         exit_bar_time_utc=t(i+2), p_up_logistic=.8, p_up_boost=.6,
                         side=1, fit_as_of_utc=t(i-1), last_train_label_exit_utc=t(i-3),
                         last_train_signal_index=i-5)
        self.trade = dict(signal_bar_time_utc=t(i), entry_time_utc=t(i+1), exit_time_utc=t(i+2),
                          side=1, entry_fill_jpy_per_usd=quotes.iloc[i+1]['ask_open']+.002,
                          exit_fill_jpy_per_usd=quotes.iloc[i+2]['bid_open']-.002)
        self.report = dict(schema='fx-aegis-h1-ml-walkforward-v1',
            quote_sha256=audit['quotes_sha256'], quote_rows=len(quotes),
            quote_metadata_sha256=hashlib.sha256(self.meta.read_bytes()).hexdigest(),
            approved_for_live_trading=False, eligible_for_paper_promotion=False,
            status='SYNTHETIC_TEST_ONLY',
            target='mid_open[i+2]>mid_open[i+1] for completed-bar signal i',
            split_policy='chronological_60pct_research_20pct_validation_20pct_diagnostic_only',
            execution_config=dict(slippage_pips_per_side=.2), sections={})
        for section, (a, b) in dict(validation=(750, 1000), diagnostic_only=(1000, 1250)).items():
            self.report['sections'][section] = dict(bars=b-a,
                start_utc=t(a), end_utc=t(b-1),
                signal_predictions=1 if section=='validation' else 0,
                trades=1 if section=='validation' else 0)
        self._save()

    def _csv(self, section, suffix, rows):
        p=self.folder/f'{section}_{suffix}.csv'
        if not rows:
            p.write_text('',encoding='utf-8')
            return
        with p.open('w', newline='', encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

    def _save(self, pred=None, trade=None):
        self._csv('validation','predictions',[self.pred if pred is None else pred])
        self._csv('validation','trades',[self.trade if trade is None else trade])
        self._csv('diagnostic_only','predictions',[])
        self._csv('diagnostic_only','trades',[])
        payload=json.dumps(self.report, sort_keys=True, ensure_ascii=False, allow_nan=False,
                           separators=(',',':')).encode('utf-8')
        self.report['report_sha256']=hashlib.sha256(payload).hexdigest()
        # report digest must exclude itself
        r={k:v for k,v in self.report.items() if k!='report_sha256'}
        self.report['report_sha256']=hashlib.sha256(json.dumps(r,sort_keys=True,
            ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()
        (self.folder/'h1_ml_retraining_report.json').write_text(json.dumps(self.report),encoding='utf-8')

    def _run(self, **kw):
        return export_overlay(self.q, self.meta, self.folder, self.output,
                              allow_synthetic=True, **kw)

    def test_h1_ml_overlay_auto_conversion(self):
        r=self._run()
        self.assertEqual((r['trades'], r['predictions']), (1,1))
        doc=json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(doc['schema'],'fx-aegis-h1-research-overlay-v1')
        self.assertAlmostEqual(doc['predictions'][0]['probability_up'],.7)
        self.assertEqual(doc['trades'][0]['side'],'long')
        self.assertNotIn('stop_loss',doc['trades'][0])
        self.assertNotIn('take_profit',doc['trades'][0])
        self.assertTrue(doc['synthetic'])
        self.assertFalse(doc['approved_for_live_trading'])

    def test_synthetic_requires_explicit_allow(self):
        with self.assertRaises(ValueError):
            export_overlay(self.q,self.meta,self.folder,self.output)

    def test_modified_quote_dataset_is_rejected(self):
        with self.q.open('a',encoding='utf-8') as f:f.write('\n')
        with self.assertRaisesRegex(ValueError,'hash|SHA|CSV|checksum|digest'):
            self._run()

    def test_modified_ml_report_rejected(self):
        self.report['sections']['validation']['trades']=2
        (self.folder/'h1_ml_retraining_report.json').write_text(json.dumps(self.report),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'digest'):
            self._run()

    def test_future_training_leak_rejected(self):
        bad={**self.pred,'last_train_label_exit_utc':self.quotes.iloc[self.ival+1]['time'].isoformat()}
        self._save(pred=bad)
        with self.assertRaisesRegex(ValueError,'future'):
            self._run()

    def test_price_tampering_rejected(self):
        self._save(trade={**self.trade,'entry_fill_jpy_per_usd':999})
        with self.assertRaisesRegex(ValueError,'fill'):
            self._run()

    def test_side_mismatch_rejected(self):
        self._save(trade={**self.trade,'side':-1})
        with self.assertRaisesRegex(ValueError,'signal'):
            self._run()

    def test_quote_time_shift_rejected(self):
        self._save(pred={**self.pred,'entry_bar_time_utc':self.quotes.iloc[self.ival+2]['time'].isoformat()})
        with self.assertRaisesRegex(ValueError,'timestamps'):
            self._run()

    def test_existing_immutable_output_rejected(self):
        self._run()
        with self.assertRaisesRegex(ValueError,'exists'):
            self._run()

    def test_public_path_rejected(self):
        dest=Path(__file__).resolve().parents[1]/'docs'/'do-not-write-annotations.json'
        with self.assertRaisesRegex(ValueError,'public'):
            export_overlay(self.q,self.meta,self.folder,dest,allow_synthetic=True)

    def test_fold_metadata_mismatch_rejected(self):
        self.report['sections']['validation']['bars']=1
        self._save()
        with self.assertRaisesRegex(ValueError,'Fold'):
            self._run()


if __name__=='__main__':
    unittest.main()
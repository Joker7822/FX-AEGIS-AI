"""Forward-state tests use only artificial fixtures and a controlled test clock."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import pandas as pd

from execution_lab import COLUMNS, make_fixture, sha256_file, write_json
from forward_paper import freeze, evaluate
from h1_ml_retrainer import RetrainConfig


class LockedPaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        data,meta=make_fixture(cls.root/"base",n=1250,seed=35)
        cls.original=pd.read_csv(data)
        cls.meta=json.loads(meta.read_text())
        cls.snap=cls.root/"frozen";cls.snap.mkdir()
        cls.quotes=cls.snap/"bidask.csv";cls.prov=cls.snap/"provenance.json"
        cls.original.iloc[:850].to_csv(cls.quotes,index=False,float_format="%.6f")
        m=cls.meta.copy();m["sha256"]=sha256_file(cls.quotes);write_json(cls.prov,m)
        cls.lock=cls.snap/"immutable_lock.json"
        cls.asof=pd.Timestamp(cls.original.iloc[849]["time"])+pd.Timedelta(hours=1)
        cls.record=freeze(cls.quotes,cls.prov,cls.lock,allow_synthetic=True,
                          cfg=RetrainConfig(min_training_bars=300,max_training_bars=700,probability_threshold=.51),
                          _test_asof=cls.asof.to_pydatetime())

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def _extended(self,out:Path,shift_future:float=0):
        out.mkdir(parents=True,exist_ok=True)
        frame=self.original.copy()
        if shift_future:
            for col in COLUMNS[1:]:
                frame.loc[950:,col]+=shift_future
        quotes,meta=out/"quotes.csv",out/"meta.json"
        frame.to_csv(quotes,index=False,float_format="%.6f")
        record=self.meta.copy();record["sha256"]=sha256_file(quotes);write_json(meta,record)
        return quotes,meta

    def test_only_post_freeze_bars_are_forward(self):
        q,m=self._extended(self.root/"extended")
        result=evaluate(q,m,self.lock,self.root/"output",allow_synthetic=True)
        self.assertEqual(result["status"],"SYNTHETIC_TEST_ONLY")
        self.assertEqual(result["new_completed_bars"],400)
        self.assertFalse(result["approved_for_live_trading"])
        signals=pd.read_csv(self.root/"output"/"paper_signals.csv")
        if len(signals):
            t=pd.to_datetime(signals["signal_bar_open_utc"],utc=True)
            self.assertTrue((t>=self.asof).all())
        self.assertEqual(result["locked_model_sample_count"],self.record["matured_train_samples"])

    def test_future_quotes_cannot_modify_lock_or_model_training(self):
        q,m=self._extended(self.root/"altered",shift_future=1.7)
        result=evaluate(q,m,self.lock,self.root/"alteredout",allow_synthetic=True)
        self.assertEqual(result["locked_model_sample_count"],self.record["matured_train_samples"])
        self.assertEqual(result["frozen_prefix_sha256"],self.record["prefix_sha256"])

    def test_no_new_bars_means_zero_forward_trades(self):
        r=evaluate(self.quotes,self.prov,self.lock,self.root/"empty",allow_synthetic=True)
        self.assertEqual(r["new_completed_bars"],0)
        self.assertEqual(r["audited_signals"],0)
        self.assertEqual(r["trades"],0)

    def test_old_quotes_change_causes_fail_closed(self):
        q,m=self._extended(self.root/"tampered")
        df=pd.read_csv(q)
        for c in COLUMNS[1:]:df.loc[400,c]+=1.0
        df.to_csv(q,index=False,float_format="%.6f")
        j=json.loads(m.read_text());j["sha256"]=sha256_file(q);write_json(m,j)
        with self.assertRaisesRegex(ValueError,"prefix changed"):
            evaluate(q,m,self.lock,self.root/"tamperout",allow_synthetic=True)

    def test_modified_lock_digest_causes_fail_closed(self):
        changed=self.root/"changed_lock.json"
        j=json.loads(self.lock.read_text());j["frozen_rows"]+=1;write_json(changed,j)
        with self.assertRaisesRegex(ValueError,"integrity"):
            evaluate(self.quotes,self.prov,changed,self.root/"lockout",allow_synthetic=True)

    def test_reject_historical_snapshot_replacement(self):
        with self.assertRaisesRegex(ValueError,"already exists"):
            freeze(self.quotes,self.prov,self.lock,allow_synthetic=True,
                   _test_asof=self.asof.to_pydatetime())

    def test_future_bar_cannot_be_frozen_as_completed(self):
        lock=self.root/"bad_lock.json"
        with self.assertRaisesRegex(ValueError,"not complete"):
            freeze(self.quotes,self.prov,lock,allow_synthetic=True,
                   _test_asof=(self.asof-timedelta(hours=2)).to_pydatetime())


if __name__=="__main__":unittest.main()

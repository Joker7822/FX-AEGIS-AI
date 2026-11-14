"""All provider responses are in-memory mocks, NEVER account data or downloads."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from broker_h1_oanda import (fetch_oanda, normalize_response, save_private_quotes,
                             utc_instant, _private_destination)
from execution_lab import load_quotes, make_fixture


class OandaIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        path,_=make_fixture(cls.root/"mock-source",n=1250,seed=6)
        cls.fixture=pd.read_csv(path)
        cls.start=utc_instant(cls.fixture.iloc[0]["time"])
        cls.end=cls.start+timedelta(days=18)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def getter(self,url,token):
        self.assertEqual(token,"test-only-placeholder")
        parsed=urlparse(url)
        self.assertEqual(parsed.hostname,"api-fxpractice.oanda.com")
        self.assertEqual(parsed.path,"/v3/instruments/USD_JPY/candles")
        params=parse_qs(parsed.query)
        self.assertEqual(params["price"],["BA"])
        self.assertEqual(params["granularity"],["H1"])
        start=pd.Timestamp(params["from"][0]);end=pd.Timestamp(params["to"][0])
        df=self.fixture
        select=df.loc[(pd.to_datetime(df["time"],utc=True)>=start)&(pd.to_datetime(df["time"],utc=True)<end)]
        candles=[]
        for _,r in select.iterrows():
            candles.append({"time":r["time"],"complete":True,"bid":{
               "o":str(r["bid_open"]),"h":str(r["bid_high"]),"l":str(r["bid_low"]),"c":str(r["bid_close"])},
               "ask":{"o":str(r["ask_open"]),"h":str(r["ask_high"]),"l":str(r["ask_low"]),"c":str(r["ask_close"])}})
        return {"instrument":"USD_JPY","granularity":"H1","candles":candles}

    def test_reject_naive_and_unaligned_timestamps(self):
        for value in ("2025-01-01T09:00:00", "2025-01-01T09:15:00Z"):
            with self.assertRaises(ValueError):utc_instant(value)
        self.assertEqual(utc_instant("2025-01-01"),datetime(2025,1,1,tzinfo=timezone.utc))

    def test_mocked_oanda_candles_convert_to_private_provenance(self):
        rows, source=fetch_oanda(self.start,self.end,environment="practice",token="test-only-placeholder",getter=self.getter)
        self.assertGreater(len(rows),200)
        self.assertEqual(source["requests"],1)
        out=self.root/"private-mock"
        quotes,meta,audit=save_private_quotes(out,rows,source,license_scope="private mock test only")
        self.assertEqual(audit["rows"],len(rows))
        self.assertFalse(audit["can_validate_real_profitability"])
        self.assertEqual(load_quotes(quotes,meta)[2]["rows"],len(rows))
        self.assertNotIn("test-only-placeholder",meta.read_text())
        with self.assertRaisesRegex(ValueError,"already exists"):
            save_private_quotes(out,rows,source,license_scope="private mock test only")

    def test_empty_token_and_untrusted_environment_rejected(self):
        for env,token in (("practice",""),("localhost","test")):
            with self.assertRaises(ValueError):
                fetch_oanda(self.start,self.end,environment=env,token=token,getter=self.getter)

    def test_reject_provider_without_both_bid_ask(self):
        payload={"instrument":"USD_JPY","granularity":"H1","candles":[{"complete":True,"time":self.start.isoformat(),"bid":{"o":1,"h":2,"l":.5,"c":1}}]}
        with self.assertRaisesRegex(ValueError,"Both Bid and Ask"):
            normalize_response(payload,self.start,self.end)

    def test_incomplete_bar_is_not_stored(self):
        p=self.getter("https://api-fxpractice.oanda.com/v3/instruments/USD_JPY/candles?price=BA&granularity=H1&from=2023-01-02T00%3A00%3A00Z&to=2023-01-02T04%3A00%3A00Z","test-only-placeholder")
        p["candles"][0]["complete"]=False
        self.assertEqual(len(normalize_response(p,self.start,self.start+timedelta(hours=4))),3)

    def test_reject_public_directory_and_unacknowledged_license(self):
        with self.assertRaisesRegex(ValueError,"inside project"):
            _private_destination(Path(__file__).resolve().parents[1]/"docs"/"data"/"broker")
        rows,source=fetch_oanda(self.start,self.end,environment="practice",token="test-only-placeholder",getter=self.getter)
        with self.assertRaisesRegex(ValueError,"licensed"):
            save_private_quotes(self.root/"reject",rows,source,license_scope="public")


if __name__=="__main__":unittest.main()

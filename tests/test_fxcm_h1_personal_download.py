"""Offline only; all H1 candles are generated test fixtures, NEVER market observations."""
import contextlib
import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
import types
import urllib.error
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import fxcm_h1_personal_download as downloader

from fxcm_h1_personal_download import (
    BASE, COLUMNS, acquire, belongs_to_fxcm_trading_week, download_week,
    parse_week_csv, private_destination, read_timestamp, url_for_week, weeks_ending,
)

HEADER='DateTime,BidOpen,BidHigh,BidLow,BidClose,AskOpen,AskHigh,AskLow,AskClose,TickQty'


def week_data(y,w,*,modify=None):
    monday=date.fromisocalendar(y,w,1)
    lines=[HEADER]
    for day in range(5):
        for hour in range(24):
            stamp=datetime.combine(monday+timedelta(days=day),datetime.min.time())+timedelta(hours=hour)
            i=24*day+hour
            bid=108.0+i/1000
            c=bid+.002
            high=c+.006
            low=bid-.006
            ask=lambda x:x+.012
            row=[stamp.strftime('%m/%d/%Y %H:%M:%S'),bid,high,low,c,ask(bid),ask(high),ask(low),ask(c),100+i]
            if modify is not None:
                row=modify(row,i)
            lines.append(','.join(str(v) for v in row))
    return ('\n'.join(lines)+'\n').encode()


def week_data_with_sunday(y, w, hours=(21, 22, 23)):
    """Synthetic Sunday opens belonging to the following trading week."""
    original = week_data(y, w).decode().splitlines()
    sunday = date.fromisocalendar(y, w, 1) - timedelta(days=1)
    added = []
    for hour in hours:
        stamp = datetime.combine(sunday, datetime.min.time()) + timedelta(hours=hour)
        bid = 107.5 + hour / 10000
        close = bid + .002
        high = close + .006
        low = bid - .006
        ask = lambda x: x + .012
        fields = [stamp.strftime('%m/%d/%Y %H:%M:%S'),
                  bid, high, low, close,
                  ask(bid), ask(high), ask(low), ask(close), 100 + hour]
        added.append(','.join(str(x) for x in fields))
    return ('\n'.join([original[0], *added, *original[1:]]) + '\n').encode()


class FakeResponse:
    def __init__(self,url,body):self.url,self.body=url,body
    def geturl(self):return self.url
    def read(self,amount):return self.body[:amount]
    def __enter__(self):return self
    def __exit__(self,*args):pass


class FxcmPrivateTests(unittest.TestCase):
    def test_week_range_is_iso_calendar_aware(self):
        self.assertEqual(weeks_ending(2021,1,2),[(2020,53),(2021,1)])
        with self.assertRaises(ValueError):weeks_ending(2021,53,1)
        with self.assertRaises(ValueError):weeks_ending(2020,10,9)

    def test_sunday_21_to_23_utc_belongs_to_next_fxcm_week(self):
        year, week = 2026, 46
        rows = parse_week_csv(week_data_with_sunday(year, week), year, week)
        self.assertEqual(len(rows), 123)
        self.assertEqual(rows[0]['time'], '2026-11-08T21:00:00Z')
        self.assertEqual(rows[2]['time'], '2026-11-08T23:00:00Z')
        self.assertEqual(rows[3]['time'], '2026-11-09T00:00:00Z')
        for hour in (21, 22, 23):
            self.assertTrue(belongs_to_fxcm_trading_week(
                read_timestamp(f'2026-11-08T{hour}:00:00Z'), year, week))

    def test_sunday_window_rejects_unrelated_dates_and_week(self):
        year, week = 2026, 46
        self.assertFalse(belongs_to_fxcm_trading_week(
            read_timestamp('2026-11-08T20:00:00Z'), year, week))
        self.assertFalse(belongs_to_fxcm_trading_week(
            read_timestamp('2026-11-15T21:00:00Z'), year, week))
        self.assertFalse(belongs_to_fxcm_trading_week(
            read_timestamp('2026-11-16T00:00:00Z'), year, week))
        with self.assertRaisesRegex(ValueError, 'outside') as before:
            parse_week_csv(week_data_with_sunday(year, week, hours=(20,)), year, week)
        self.assertEqual(downloader.safe_failure_code(before.exception),
                         'SOURCE_CSV_WEEK_BEFORE')
        with self.assertRaisesRegex(ValueError, 'outside') as after:
            parse_week_csv(week_data_with_sunday(year, week + 1), year, week)
        self.assertEqual(downloader.safe_failure_code(after.exception),
                         'SOURCE_CSV_WEEK_AFTER')
        with self.assertRaisesRegex(ValueError, 'outside'):
            parse_week_csv(week_data_with_sunday(year, week), year, week - 1)

    def test_sunday_trading_start_crosses_iso_year_boundary(self):
        rows = parse_week_csv(week_data_with_sunday(2021, 1), 2021, 1)
        self.assertEqual(rows[0]['time'], '2021-01-03T21:00:00Z')
        self.assertEqual(rows[3]['time'], '2021-01-04T00:00:00Z')

    def test_two_week_acquire_retains_sunday_bars_without_overlap(self):
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            week = int(req.full_url.rsplit('/', 1)[1].split('.')[0])
            return FakeResponse(req.full_url, gzip.compress(
                week_data_with_sunday(2020, week)))
        def mock_audit(quotes, provenance):
            expected = hashlib.sha256(quotes.read_bytes()).hexdigest()
            self.assertEqual(json.loads(provenance.read_text())['sha256'], expected)
            return None, None, {'quotes_sha256': expected}
        fake = types.SimpleNamespace(load_quotes=mock_audit)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
                sys.modules, {'execution_lab': fake}):
            output = Path(folder) / 'private-quotes'
            result = acquire(year=2020, week=11, weeks=2, out=output,
                             enable_download=True, personal_use_accepted=True,
                             opener=opener)
            self.assertEqual(len(calls), 2)
            self.assertEqual(result['h1_bars'], 246)
            with (output / 'bidask_h1.csv').open() as stream:
                records = list(csv.DictReader(stream))
            dates = [r['time'] for r in records]
            self.assertEqual(len(dates), len(set(dates)))
            self.assertEqual(dates, sorted(dates))
            self.assertIn('2020-03-08T21:00:00Z', dates)

    def test_fractional_timestamp_zero_and_nonzero(self):
        from fxcm_h1_personal_download import read_timestamp
        self.assertEqual(read_timestamp('10/19/2026 00:00:00.000').isoformat(),
                         '2026-10-19T00:00:00+00:00')
        with self.assertRaisesRegex(ValueError, 'UTC hour'):
            read_timestamp('10/19/2026 00:00:00.123')

    def test_url_is_fixed_fxcm_usdjpy_h1_only(self):
        self.assertEqual(url_for_week(2020,10),BASE+'/H1/USDJPY/2020/10.csv.gz')

    def test_access_requires_both_explicits(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'out'
            with self.assertRaisesRegex(ValueError,'Explicit'):
                acquire(year=2020,week=11,weeks=2,out=out,enable_download=False,
                        personal_use_accepted=True)
            self.assertFalse(out.exists())

    def test_csv_columns_must_have_both_bid_and_ask(self):
        with self.assertRaisesRegex(ValueError,'all 8 named'):
            parse_week_csv(b'DateTime,BidOpen,BidHigh,BidLow,BidClose\n',2020,10)

    def test_exact_h1_and_bidask_ohlc(self):
        rows=parse_week_csv(week_data(2020,10),2020,10)
        self.assertEqual(len(rows),120)
        self.assertEqual(list(rows[0]),list(COLUMNS))
        self.assertEqual(rows[0]['time'],'2020-03-02T00:00:00Z')
        self.assertGreater(rows[0]['ask_open'],rows[0]['bid_open'])

    def test_week_outside_and_duplicate_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            parse_week_csv(week_data(2020,10),2020,11)
        bad=week_data(2020,10,modify=lambda r,i: [r[0] if i!=1 else '03/02/2020 00:00:00',*r[1:]])
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            parse_week_csv(bad,2020,10)

    def test_bid_ask_sides_must_not_be_fabricated(self):
        bad=week_data(2020,10,modify=lambda r,i: [*r[:5],r[5]-.03,r[6]-.03,r[7]-.03,r[8]-.03,r[9]] if i==10 else r)
        with self.assertRaisesRegex(ValueError,'Bid/Ask'):
            parse_week_csv(bad,2020,10)

    def test_high_low_enforced(self):
        bad=week_data(2020,10,modify=lambda r,i:[r[0],r[1],r[1]-.1,*r[3:]] if i==9 else r)
        with self.assertRaisesRegex(ValueError,'OHLC'):
            parse_week_csv(bad,2020,10)

    def test_redirect_to_other_host_rejected(self):
        def opener(req,timeout):
            return FakeResponse('https://evil.invalid/tracker.csv.gz',gzip.compress(week_data(2020,10)))
        with self.assertRaisesRegex(ValueError,'redirect'):
            download_week(2020,10,opener=opener)

    def test_corrupted_gzip_rejected(self):
        def opener(req,timeout):return FakeResponse(req.full_url,b'not gzip')
        with self.assertRaisesRegex(ValueError,'GZIP'):
            download_week(2020,10,opener=opener)

    def test_two_week_private_conversion_and_provenance(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req.full_url)
            w=int(req.full_url.split('/')[-1].split('.')[0])
            return FakeResponse(req.full_url,gzip.compress(week_data(2020,w)))
        def mock_audit(q,p):
            meta=json.loads(p.read_text())
            assert meta['sha256']==hashlib.sha256(q.read_bytes()).hexdigest()
            with q.open() as stream:rows=list(csv.DictReader(stream))
            assert len(rows)==240
            return None,None,{'quotes_sha256':meta['sha256']}
        fake=types.SimpleNamespace(load_quotes=mock_audit)
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'execution_lab':fake}):
            out=Path(folder)/'private-snapshot'
            result=acquire(year=2020,week=11,weeks=2,out=out,
                   enable_download=True,personal_use_accepted=True,opener=opener)
            self.assertEqual(result['h1_bars'],240)
            self.assertEqual(len(calls),2)
            meta=json.loads((out/'provenance.json').read_text())
            self.assertEqual(meta['source_kind'],'BROKER_BID_ASK_H1')
            self.assertEqual(meta['source_files'],2)
            self.assertFalse(meta['approved_for_live_trading'])
            with self.assertRaisesRegex(ValueError,'already exists'):
                acquire(year=2020,week=11,weeks=2,out=out,
                   enable_download=True,personal_use_accepted=True,opener=opener)

    def test_safe_diagnostic_reports_http_status_without_response_text(self):
        private_reason = 'SECRET_PROVIDER_RESPONSE_NEVER_LOG'
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, private_reason, {}, None)
        with self.assertRaises(RuntimeError) as captured:
            download_week(2026, 46, opener=opener)
        self.assertEqual(downloader.safe_failure_code(captured.exception), 'SOURCE_HTTP_404')
        self.assertNotIn(private_reason, downloader.safe_failure_code(captured.exception))

    def test_main_failure_log_is_allowlisted_and_excludes_secret_values(self):
        fake_secret = 'SECRET_RAW_QUOTE_OR_CREDENTIAL'
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(downloader, 'acquire', side_effect=ValueError(fake_secret)), \
                patch.object(sys, 'argv', [
                    'fxcm_h1_personal_download.py', '--year', '2026', '--week', '46'
                ]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = downloader.main()
        self.assertEqual(code, 2)
        self.assertEqual(stderr.getvalue().strip(),
                         'FXCM_SAFE_DIAGNOSTIC=H1_DATA_VALIDATION_FAILED')
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['status'], 'FAILED_CLOSED')
        self.assertEqual(payload['error_code'], 'H1_DATA_VALIDATION_FAILED')
        self.assertNotIn(fake_secret, stderr.getvalue() + stdout.getvalue())

    def test_output_cannot_go_to_pages(self):
        with self.assertRaisesRegex(ValueError,'public'):
            private_destination(Path('/tmp/example/docs/quotes'))


if __name__=='__main__': unittest.main()

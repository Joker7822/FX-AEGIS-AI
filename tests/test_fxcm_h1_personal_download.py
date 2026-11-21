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


def week_data_with_last_sunday(y, w, hours=(21, 22, 23)):
    """Synthetic ISO-week H1 file with the *following* Sunday evening."""
    original = week_data(y, w).decode().splitlines()
    sunday = date.fromisocalendar(y, w, 1) + timedelta(days=6)
    added = []
    for hour in hours:
        stamp = datetime.combine(sunday, datetime.min.time()) + timedelta(hours=hour)
        bid = 109.0 + hour / 10000
        close = bid + .002
        high = close + .006
        low = bid - .006
        ask = lambda x: x + .012
        fields = [stamp.strftime('%m/%d/%Y %H:%M:%S'),
                  bid, high, low, close,
                  ask(bid), ask(high), ask(low), ask(close), 100 + hour]
        added.append(','.join(str(x) for x in fields))
    return ('\n'.join([*original, *added]) + '\n').encode()


def labeled_week_data(y, w, *, include_sunday=True):
    """Synthetic FXCM file label W: Sunday of W + Mon-Fri of W+1."""
    next_monday = date.fromisocalendar(y, w, 1) + timedelta(weeks=1)
    label = next_monday.isocalendar()
    if include_sunday:
        return week_data_with_sunday(label.year, label.week)
    return week_data(label.year, label.week)


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
                labeled_week_data(2020, week)))
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

    def test_iso_calendar_week_accepts_only_last_sunday_21_to_23_utc(self):
        rows = parse_week_csv(week_data_with_last_sunday(2026, 46), 2026, 46)
        self.assertEqual(len(rows), 123)
        self.assertEqual(rows[-3]['time'], '2026-11-15T21:00:00Z')
        self.assertEqual(rows[-1]['time'], '2026-11-15T23:00:00Z')
        self.assertAlmostEqual(rows[-1]['bid_open'], 109.0023)
        self.assertAlmostEqual(rows[-1]['ask_open'], 109.0143)
        with self.assertRaisesRegex(ValueError, 'outside') as excess:
            parse_week_csv(week_data_with_last_sunday(
                2026, 46, hours=(21, 22, 23, 24)), 2026, 46)
        self.assertEqual(downloader.safe_failure_code(excess.exception),
                         'SOURCE_CSV_WEEK_AFTER')

    def test_two_opposing_sunday_borders_fail_closed(self):
        leading = week_data_with_sunday(2026, 46).decode().splitlines()
        trailing = week_data_with_last_sunday(2026, 46).decode().splitlines()[-3:]
        with self.assertRaisesRegex(ValueError, 'mixes both') as mixed:
            parse_week_csv(('\n'.join([*leading, *trailing]) + '\n').encode(),
                           2026, 46)
        self.assertEqual(downloader.safe_failure_code(mixed.exception),
                         'SOURCE_CSV_WEEK_BOUNDARY_MIXED')

    def test_two_iso_week_archives_preserve_order_and_provenance(self):
        def opener(req, timeout):
            week = int(req.full_url.rsplit('/', 1)[1].split('.')[0])
            return FakeResponse(req.full_url, gzip.compress(
                labeled_week_data(2020, week)))
        def mock_audit(quotes, provenance):
            meta = json.loads(provenance.read_text())
            digest = hashlib.sha256(quotes.read_bytes()).hexdigest()
            self.assertEqual(meta['sha256'], digest)
            self.assertEqual(
                [x['week_partition_mode'] for x in meta['source_archives']],
                ['FXCM_SUNDAY_LABEL_NEXT_WEEK', 'FXCM_SUNDAY_LABEL_NEXT_WEEK'])
            return None, None, {'quotes_sha256': digest}
        fake = types.SimpleNamespace(load_quotes=mock_audit)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
                sys.modules, {'execution_lab': fake}):
            out = Path(folder) / 'private'
            result = acquire(year=2020, week=11, weeks=2, out=out,
                             enable_download=True, personal_use_accepted=True,
                             opener=opener)
            self.assertEqual(result['h1_bars'], 246)
            with (out / 'bidask_h1.csv').open() as stream:
                dates = [x['time'] for x in csv.DictReader(stream)]
            self.assertEqual(dates, sorted(set(dates)))
            self.assertEqual(dates[-1], '2020-03-20T23:00:00Z')

    def test_provider_label_mode_rejects_inconsistent_calendar_week_file(self):
        def opener(req, timeout):
            week = int(req.full_url.rsplit('/', 1)[1].split('.')[0])
            content = (labeled_week_data(2020, week) if week == 10
                       else week_data(2020, week))
            return FakeResponse(req.full_url, gzip.compress(content))
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'private'
            with self.assertRaisesRegex(ValueError, 'outside') as rejected:
                acquire(year=2020, week=11, weeks=2, out=out,
                        enable_download=True, personal_use_accepted=True,
                        opener=opener)
            self.assertEqual(downloader.safe_failure_code(rejected.exception),
                             'SOURCE_CSV_WEEK_BEFORE')
            self.assertFalse(out.exists())

    def test_boundary_offset_bucket_has_only_fixed_public_categories(self):
        from fxcm_h1_personal_download import (
            H1WeekWindowError, boundary_offset_bucket,
        )
        cases = [
            (timedelta(0), '0_2H'),
            (timedelta(hours=2), '0_2H'),
            (timedelta(hours=3), '3_23H'),
            (timedelta(hours=23), '3_23H'),
            (timedelta(days=1), '1_6D'),
            (timedelta(days=6), '1_6D'),
            (timedelta(days=7), '7_27D'),
            (timedelta(days=27), '7_27D'),
            (timedelta(days=28), '28D_PLUS'),
        ]
        for delta, expected in cases:
            self.assertEqual(boundary_offset_bucket(delta), expected)
        with self.assertRaises(ValueError):
            boundary_offset_bucket(timedelta(hours=-1))
        with self.assertRaises(ValueError):
            H1WeekWindowError('INVALID', timedelta(0))

    def test_week_window_error_preserves_fixed_diagnostic_classification(self):
        from fxcm_h1_personal_download import H1WeekWindowError
        before = H1WeekWindowError('before', timedelta(days=10))
        after = H1WeekWindowError('after', timedelta(hours=12))
        self.assertEqual(downloader.safe_failure_code(before),
                         'SOURCE_CSV_WEEK_BEFORE')
        self.assertEqual(downloader.safe_failure_code(after),
                         'SOURCE_CSV_WEEK_AFTER')
        self.assertEqual(before.offset_bucket, '7_27D')
        self.assertEqual(after.offset_bucket, '3_23H')

    def test_failed_week_index_and_offset_logged_without_sensitive_data(self):
        # The first week is valid. The second week's archive has a next-week
        # timestamp and must fail before writing any H1 file or provenance.
        def fake_download(year, week, opener=None):
            fake_week = 13 if week == 11 else 11
            return week_data(year, fake_week), {'source_url': 'PRIVATE_SOURCE_URL'}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
                tempfile.TemporaryDirectory() as folder,
                patch.object(downloader, 'download_week', side_effect=fake_download),
                patch.object(sys, 'argv', [
                    'fxcm_h1_personal_download.py', '--year', '2020',
                    '--week', '11', '--weeks', '2', '--out',
                    str(Path(folder) / 'private'),
                    '--enable-download', '--accept-fxcm-personal-use'
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
        ):
            code = downloader.main()
            self.assertFalse((Path(folder) / 'private').exists())
        self.assertEqual(code, 2)
        output = stderr.getvalue()
        self.assertEqual(output.splitlines(), [
            'FXCM_SAFE_DIAGNOSTIC=SOURCE_CSV_WEEK_AFTER',
            'FXCM_SAFE_WEEK_OFFSET=1_6D',
            'FXCM_SAFE_ARCHIVE_POSITION=2',
            'FXCM_SAFE_FILE_TAIL_OFFSET=1_6D',
            'FXCM_SAFE_FILE_AFTER_BARS=25_120',
            'FXCM_SAFE_FIRST_ISO_WEEK=NEXT_2',
            'FXCM_SAFE_LAST_ISO_WEEK=NEXT_2',
        ])
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'FAILED_CLOSED')
        self.assertNotIn('PRIVATE_SOURCE_URL', output + stdout.getvalue())
        self.assertNotIn('2020-03-16', output + stdout.getvalue())

    def test_full_csv_tail_extent_is_distinct_from_first_invalid_bar(self):
        from fxcm_h1_personal_download import (
            H1WeekWindowError, safe_week_tail_summary,
        )
        first_week = week_data(2020, 10).decode().splitlines()
        next_week = week_data(2020, 11).decode().splitlines()[1:15]
        content = ('\n'.join([*first_week, *next_week]) + '\n').encode()
        self.assertEqual(
            safe_week_tail_summary(content.decode(), 'DateTime', 2020, 10),
            ('3_23H', '4_24'),
        )
        with self.assertRaises(H1WeekWindowError) as context:
            parse_week_csv(content, 2020, 10)
        self.assertEqual(context.exception.offset_bucket, '0_2H')
        self.assertEqual(context.exception.file_tail_offset_bucket, '3_23H')
        self.assertEqual(context.exception.file_after_bars_bucket, '4_24')

    def test_full_csv_tail_scan_rejects_invalid_later_timestamps(self):
        from fxcm_h1_personal_download import safe_week_tail_summary
        first_week = week_data(2020, 10).decode().splitlines()
        next_week = week_data(2020, 11).decode().splitlines()[1:4]
        rows = [*first_week, *next_week, next_week[-1]]
        # Later duplicate timestamps invalidate a public diagnostic extent.
        self.assertEqual(
            safe_week_tail_summary('\n'.join(rows) + '\n', 'DateTime', 2020, 10),
            ('UNAVAILABLE', 'UNAVAILABLE'),
        )
        self.assertEqual(
            safe_week_tail_summary(week_data(2020, 10).decode(),
                                   'DateTime', 2020, 10),
            ('NONE', 'NONE'),
        )

    def test_full_csv_tail_diagnostic_logs_bins_only_and_refuses_output(self):
        first_week = week_data(2020, 10).decode().splitlines()
        next_week = week_data(2020, 11).decode().splitlines()[1:15]
        fake_content = ('\n'.join([*first_week, *next_week]) + '\n').encode()
        def fake_download(year, week, opener=None):
            return fake_content, {'source_url': 'PRIVATE_DO_NOT_LOG'}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
                tempfile.TemporaryDirectory() as folder,
                patch.object(downloader, 'download_week', side_effect=fake_download),
                patch.object(sys, 'argv', [
                    'fxcm_h1_personal_download.py', '--year', '2020',
                    '--week', '11', '--weeks', '2', '--out',
                    str(Path(folder) / 'private'),
                    '--enable-download', '--accept-fxcm-personal-use'
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
        ):
            result = downloader.main()
            self.assertFalse((Path(folder) / 'private').exists())
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue().splitlines(), [
            'FXCM_SAFE_DIAGNOSTIC=SOURCE_CSV_WEEK_AFTER',
            'FXCM_SAFE_WEEK_OFFSET=0_2H',
            'FXCM_SAFE_ARCHIVE_POSITION=1',
            'FXCM_SAFE_FILE_TAIL_OFFSET=3_23H',
            'FXCM_SAFE_FILE_AFTER_BARS=4_24',
            'FXCM_SAFE_FIRST_ISO_WEEK=REQUESTED',
            'FXCM_SAFE_LAST_ISO_WEEK=NEXT_1',
            'FXCM_SAFE_ADJACENT_RELATION=OVERLAP_SHARED',
            'FXCM_SAFE_ADJACENT_SHARED_H1=121_PLUS',
            'FXCM_SAFE_ARCHIVE_MAP_2=PREV_1/REQUESTED/OVERLAP_SHARED/121_PLUS/NONE',
            'FXCM_SAFE_ARCHIVE_STATUS_2=VALID',
        ])
        for private_marker in ('PRIVATE_DO_NOT_LOG', '2020-03-09',
                               '108.000', 'bid_open'):
            self.assertNotIn(private_marker,
                             stderr.getvalue() + stdout.getvalue())

    def test_relative_iso_week_categories_cross_calendar_year(self):
        from fxcm_h1_personal_download import safe_relative_iso_week
        # ISO 2020-W53 ends 2021-01-03; this is not 2021-W01.
        cases = [
            ('2020-12-27T23:00:00Z', 'PREV_1'),
            ('2020-12-28T00:00:00Z', 'REQUESTED'),
            ('2021-01-03T23:00:00Z', 'REQUESTED'),
            ('2021-01-04T00:00:00Z', 'NEXT_1'),
            ('2021-01-11T00:00:00Z', 'NEXT_2'),
            ('2021-01-18T00:00:00Z', 'FAR_AFTER'),
        ]
        for value, expected in cases:
            self.assertEqual(
                safe_relative_iso_week(read_timestamp(value), 2020, 53),
                expected)

    def test_safe_week_extent_distinguishes_overlap_from_shift(self):
        from fxcm_h1_personal_download import safe_week_iso_extent
        first = week_data(2020, 10).decode().splitlines()
        following = week_data(2020, 11).decode().splitlines()[1:16]
        mixed = '\n'.join([*first, *following]) + '\n'
        self.assertEqual(
            safe_week_iso_extent(mixed, 'DateTime', 2020, 10),
            ('REQUESTED', 'NEXT_1'))
        self.assertEqual(
            safe_week_iso_extent(week_data(2020, 11).decode(),
                                 'DateTime', 2020, 10),
            ('NEXT_1', 'NEXT_1'))
        self.assertEqual(
            safe_week_iso_extent(week_data_with_sunday(2020, 10).decode(),
                                 'DateTime', 2020, 10),
            ('PREV_1', 'REQUESTED'))

    def test_safe_week_extent_fails_closed_for_corrupt_timestamp_order(self):
        from fxcm_h1_personal_download import safe_week_iso_extent
        first = week_data(2020, 10).decode().splitlines()
        invalid = '\n'.join([*first, first[-1]]) + '\n'
        self.assertEqual(safe_week_iso_extent(
            invalid, 'DateTime', 2020, 10), ('UNAVAILABLE', 'UNAVAILABLE'))
        self.assertEqual(safe_week_iso_extent(
            'DateTime,BidOpen\nnot-a-timestamp,123\n', 'DateTime',
            2020, 10), ('UNAVAILABLE', 'UNAVAILABLE'))
        self.assertEqual(safe_week_iso_extent(
            'DateTime\n', 'DateTime', 2020, 10),
            ('UNAVAILABLE', 'UNAVAILABLE'))

    def test_adjacent_archive_timestamp_overlap_and_gap_categories(self):
        from fxcm_h1_personal_download import safe_adjacent_week_relation
        first = week_data(2020, 10).decode().splitlines()
        second = week_data(2020, 11).decode().splitlines()
        overlapping = ('\n'.join([*first, *second[1:31]]) + '\n').encode()
        normal = ('\n'.join(first) + '\n').encode()
        following = ('\n'.join(second) + '\n').encode()
        self.assertEqual(
            safe_adjacent_week_relation(overlapping, following),
            ('OVERLAP_SHARED', '25_120'))
        # The missing weekend creates a real temporal gap between valid
        # consecutive provider files. Diagnostics do not fill this gap.
        self.assertEqual(safe_adjacent_week_relation(normal, following),
                         ('GAP', 'NONE'))
        joined = week_data(2020, 10).decode().splitlines()
        left = ('\n'.join([joined[0], *joined[1:10]]) + '\n').encode()
        right = ('\n'.join([joined[0], *joined[10:]]) + '\n').encode()
        self.assertEqual(safe_adjacent_week_relation(left, right),
                         ('CONTIGUOUS', 'NONE'))
        sparse_a = ('\n'.join([joined[0], joined[1], joined[3]]) + '\n').encode()
        sparse_b = ('\n'.join([joined[0], joined[2], joined[4]]) + '\n').encode()
        self.assertEqual(safe_adjacent_week_relation(sparse_a, sparse_b),
                         ('OVERLAP_NO_SHARED', 'NONE'))

    def test_adjacent_archive_diagnostic_rejects_bad_csv_or_duplicate_hours(self):
        from fxcm_h1_personal_download import safe_adjacent_week_relation
        one = week_data(2020, 10)
        bad = one.decode() + one.decode().splitlines()[-1] + '\n'
        self.assertEqual(safe_adjacent_week_relation(one, bad.encode()),
                         ('UNAVAILABLE', 'UNAVAILABLE'))
        self.assertEqual(safe_adjacent_week_relation(one, b'not,csv\n'),
                         ('UNAVAILABLE', 'UNAVAILABLE'))

    def test_failed_first_archive_downloads_next_only_for_safe_diagnosis(self):
        first = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        bad_first = ('\n'.join([*first, *next_rows[1:31]]) + '\n').encode()
        second = ('\n'.join(next_rows) + '\n').encode()
        calls = []
        def fake_download(year, week, opener=None):
            calls.append((year, week))
            return (bad_first if week == 10 else second), {
                'source_url': 'PRIVATE_FXCM_PROVIDER_URL'}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
                tempfile.TemporaryDirectory() as folder,
                patch.object(downloader, 'download_week', side_effect=fake_download),
                patch.object(sys, 'argv', [
                    'fxcm_h1_personal_download.py', '--year', '2020',
                    '--week', '11', '--weeks', '2', '--out',
                    str(Path(folder) / 'private'),
                    '--enable-download', '--accept-fxcm-personal-use'
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(downloader.main(), 2)
            self.assertFalse((Path(folder) / 'private').exists())
        self.assertEqual(calls, [(2020, 10), (2020, 11)])
        self.assertIn('FXCM_SAFE_ADJACENT_RELATION=OVERLAP_SHARED',
                      stderr.getvalue().splitlines())
        self.assertIn('FXCM_SAFE_ADJACENT_SHARED_H1=25_120',
                      stderr.getvalue().splitlines())
        self.assertIn('FXCM_SAFE_DIAGNOSTIC=SOURCE_CSV_WEEK_AFTER',
                      stderr.getvalue().splitlines())
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'FAILED_CLOSED')
        for private in ('PRIVATE_FXCM_PROVIDER_URL', '2020-03-09',
                        '108.000', 'BidOpen'):
            self.assertNotIn(private, stderr.getvalue() + stdout.getvalue())

    def test_next_week_network_error_preserves_original_failure(self):
        from fxcm_h1_personal_download import H1WeekWindowError
        first = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        bad_first = ('\n'.join([*first, *next_rows[1:31]]) + '\n').encode()
        def fake_download(year, week, opener=None):
            if week == 11:
                raise RuntimeError('SECRET_PROVIDER_ERROR_DO_NOT_LOG')
            return bad_first, {}
        with tempfile.TemporaryDirectory() as folder, patch.object(
                downloader, 'download_week', side_effect=fake_download):
            with self.assertRaises(H1WeekWindowError) as found:
                acquire(year=2020, week=11, weeks=2,
                        out=Path(folder) / 'private', enable_download=True,
                        personal_use_accepted=True)
            self.assertEqual(
                downloader.safe_failure_code(found.exception),
                'SOURCE_CSV_WEEK_AFTER')
            self.assertEqual(found.exception.adjacent_relation, 'UNAVAILABLE')
            self.assertEqual(found.exception.adjacent_shared_h1_bucket,
                             'UNAVAILABLE')
            self.assertFalse((Path(folder) / 'private').exists())

    def test_four_week_archive_manifest_handles_overlap_then_weekend_gaps(self):
        from fxcm_h1_personal_download import (
            H1WeekWindowError, safe_remaining_archive_manifest,
        )
        base = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        rejected = ('\n'.join([*base, *next_rows[1:6]]) + '\n').encode()
        requested = weeks_ending(2020, 13, 4)
        calls = []
        def fake_download(y, w, opener=None):
            calls.append((y, w))
            return week_data(y, w), {'source_url': 'SECRET_NOT_FOR_LOGS'}
        with patch.object(downloader, 'download_week', side_effect=fake_download):
            manifest = safe_remaining_archive_manifest(requested, 1, rejected)
        self.assertEqual(calls, [(2020, 11), (2020, 12), (2020, 13)])
        self.assertEqual(manifest, [
            (2, 'REQUESTED', 'REQUESTED', 'OVERLAP_SHARED', '4_24', 'NONE', 'VALID'),
            (3, 'REQUESTED', 'REQUESTED', 'GAP', 'NONE', '1_6D', 'VALID'),
            (4, 'REQUESTED', 'REQUESTED', 'GAP', 'NONE', '1_6D', 'VALID'),
        ])

    def test_four_week_manifest_survives_one_failed_archive_download(self):
        from fxcm_h1_personal_download import safe_remaining_archive_manifest
        base = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        rejected = ('\n'.join([*base, *next_rows[1:6]]) + '\n').encode()
        def fake_download(y, w, opener=None):
            if w == 12:
                raise RuntimeError(
                    'FXCM H1 download unavailable for 2020-W12; no quotes accepted'
                ) from urllib.error.URLError('SECRET_NETWORK_RESPONSE_NEVER_LOG')
            return week_data(y, w), {}
        with patch.object(downloader, 'download_week', side_effect=fake_download):
            manifest = safe_remaining_archive_manifest(
                weeks_ending(2020, 13, 4), 1, rejected)
        self.assertEqual(manifest, [
            (2, 'REQUESTED', 'REQUESTED', 'OVERLAP_SHARED', '4_24', 'NONE', 'VALID'),
            (3, 'UNAVAILABLE', 'UNAVAILABLE',
             'UNAVAILABLE', 'UNAVAILABLE', 'UNAVAILABLE', 'SOURCE_CONNECTION_ERROR'),
            (4, 'REQUESTED', 'REQUESTED',
             'UNAVAILABLE', 'UNAVAILABLE', 'UNAVAILABLE', 'VALID'),
        ])

    def test_manifest_logging_is_allowlisted_and_keeps_output_absent(self):
        base = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        bad_first = ('\n'.join([*base, *next_rows[1:6]]) + '\n').encode()
        calls = []
        def fake_download(y, w, opener=None):
            calls.append((y, w))
            return (bad_first if w == 10 else week_data(y, w)), {
                'source_url': 'SECRET_SOURCE_URL_NEVER_LOG'}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
                tempfile.TemporaryDirectory() as folder,
                patch.object(downloader, 'download_week', side_effect=fake_download),
                patch.object(sys, 'argv', [
                    'fxcm_h1_personal_download.py', '--year', '2020',
                    '--week', '13', '--weeks', '4', '--out',
                    str(Path(folder) / 'private'),
                    '--enable-download', '--accept-fxcm-personal-use'
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(downloader.main(), 2)
            self.assertFalse((Path(folder) / 'private').exists())
        self.assertEqual(calls, [(2020, 10), (2020, 11),
                                 (2020, 12), (2020, 13)])
        logs = stderr.getvalue().splitlines()
        self.assertEqual([line for line in logs if
                          line.startswith('FXCM_SAFE_ARCHIVE_MAP_')], [
            'FXCM_SAFE_ARCHIVE_MAP_2=REQUESTED/REQUESTED/OVERLAP_SHARED/4_24/NONE',
            'FXCM_SAFE_ARCHIVE_MAP_3=REQUESTED/REQUESTED/GAP/NONE/1_6D',
            'FXCM_SAFE_ARCHIVE_MAP_4=REQUESTED/REQUESTED/GAP/NONE/1_6D',
        ])
        self.assertEqual([line for line in logs if
                          line.startswith('FXCM_SAFE_ARCHIVE_STATUS_')], [
            'FXCM_SAFE_ARCHIVE_STATUS_2=VALID',
            'FXCM_SAFE_ARCHIVE_STATUS_3=VALID',
            'FXCM_SAFE_ARCHIVE_STATUS_4=VALID',
        ])
        self.assertEqual(logs[0],
                         'FXCM_SAFE_DIAGNOSTIC=SOURCE_CSV_WEEK_AFTER')
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'FAILED_CLOSED')
        for private in ('SECRET_SOURCE_URL_NEVER_LOG', '2020-03-09',
                        '108.000', 'BidOpen', '2020-03-23'):
            self.assertNotIn(private, stderr.getvalue() + stdout.getvalue())

    def test_index_status_classifies_csv_integrity_without_quote_leakage(self):
        from fxcm_h1_personal_download import safe_h1_timestamp_index_with_status
        good = week_data(2020, 10)
        timestamps, status = safe_h1_timestamp_index_with_status(good)
        self.assertEqual(status, 'VALID')
        self.assertEqual(len(timestamps), 120)
        self.assertEqual(
            safe_h1_timestamp_index_with_status(b'\xff\xff')[1],
            'SOURCE_CSV_ENCODING')
        self.assertEqual(
            safe_h1_timestamp_index_with_status(b'foo,bar\nx,y\n')[1],
            'SOURCE_CSV_HEADER')
        self.assertEqual(
            safe_h1_timestamp_index_with_status(HEADER.encode()+b'\n')[1],
            'SOURCE_CSV_NO_BARS')
        bad_order = good + good.decode().splitlines()[-1].encode() + b'\n'
        self.assertEqual(
            safe_h1_timestamp_index_with_status(bad_order)[1],
            'SOURCE_CSV_ORDER')
        bad_stamp = week_data(2020, 10, modify=lambda row, idx:
            ['private_stamp', *row[1:]] if idx == 2 else row)
        self.assertEqual(
            safe_h1_timestamp_index_with_status(bad_stamp)[1],
            'SOURCE_CSV_TIMESTAMP')
        unaligned = week_data(2020, 10, modify=lambda row, idx:
            ['03/02/2020 02:30:00', *row[1:]] if idx == 2 else row)
        self.assertEqual(
            safe_h1_timestamp_index_with_status(unaligned)[1],
            'SOURCE_CSV_TIMESTAMP_ALIGNMENT')
        malformed = good + b'2020-03-10T12:00:00Z\n'
        self.assertEqual(
            safe_h1_timestamp_index_with_status(malformed)[1],
            'SOURCE_CSV_ROW')

    def test_manifest_http_404_gzip_failure_and_good_week_have_safe_codes(self):
        from fxcm_h1_personal_download import safe_remaining_archive_manifest
        import urllib.error
        base = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        rejected = ('\n'.join([*base, *next_rows[1:6]]) + '\n').encode()
        def fake_download(y, w, opener=None):
            if w == 11:
                cause = urllib.error.HTTPError(
                    'https://private.invalid/data', 404,
                    'SECRET_HTTP_404_BODY', {}, None)
                raise RuntimeError(
                    'FXCM H1 download unavailable for 2020-W11; no quotes accepted'
                ) from cause
            if w == 12:
                raise ValueError('Corrupt official GZIP archive')
            return week_data(y, w), {'secret': 'SECRET_PROVIDER_RESPONSE'}
        with patch.object(downloader, 'download_week', side_effect=fake_download):
            manifest = safe_remaining_archive_manifest(
                weeks_ending(2020, 13, 4), 1, rejected)
        self.assertEqual([item[-1] for item in manifest], [
            'SOURCE_HTTP_404', 'SOURCE_GZIP_CORRUPT', 'VALID'])
        self.assertEqual([item[0] for item in manifest], [2, 3, 4])

    def test_manifest_status_refuses_corrupt_last_file_without_disclosing_data(self):
        base = week_data(2020, 10).decode().splitlines()
        next_rows = week_data(2020, 11).decode().splitlines()
        bad_first = ('\n'.join([*base, *next_rows[1:6]]) + '\n').encode()
        def fake_download(y, w, opener=None):
            if w == 13:
                return week_data(y, w, modify=lambda row, idx:
                    ['SECRET_INVALID_TIMESTAMP', *row[1:]] if idx == 3 else row), {}
            return bad_first if w == 10 else week_data(y, w), {}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
                tempfile.TemporaryDirectory() as folder,
                patch.object(downloader, 'download_week', side_effect=fake_download),
                patch.object(sys, 'argv', [
                    'fxcm_h1_personal_download.py', '--year', '2020',
                    '--week', '13', '--weeks', '4', '--out',
                    str(Path(folder) / 'private'),
                    '--enable-download', '--accept-fxcm-personal-use'
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(downloader.main(), 2)
            self.assertFalse((Path(folder) / 'private').exists())
        self.assertIn(
            'FXCM_SAFE_ARCHIVE_STATUS_4=SOURCE_CSV_TIMESTAMP',
            stderr.getvalue().splitlines())
        for secret in ('SECRET_INVALID_TIMESTAMP', '2020-03-24', '108.0'):
            self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'FAILED_CLOSED')

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
            return FakeResponse(req.full_url,gzip.compress(labeled_week_data(2020,w,include_sunday=False)))
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

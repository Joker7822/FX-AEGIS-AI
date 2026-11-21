"""Explicit, one-shot, private FXCM USDJPY H1 Bid/Ask CSV/GZIP importer.

The user must acknowledge FXCM personal-use/EULA terms and opt in to network.
Scheduled execution requires separate GitHub owner opt-ins and encryption key.
No plaintext public data, broker token, or trading orders.
Provider: https://github.com/fxcm/MarketData/tree/master/CandleData
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
HOST = 'candledata.fxcorporate.com'
BASE = 'https://' + HOST
SCHEMA = 'fx-aegis-h1-bidask-v1'
REQUIRED = ('datetime', 'bidopen', 'bidhigh', 'bidlow', 'bidclose',
            'askopen', 'askhigh', 'asklow', 'askclose')
COLUMNS = ('time', 'bid_open', 'bid_high', 'bid_low', 'bid_close',
           'ask_open', 'ask_high', 'ask_low', 'ask_close')
MAX_COMPRESSED = 8 * 1024 * 1024
MAX_DECOMPRESSED = 32 * 1024 * 1024
MAX_ROWS = 4000


def private_destination(path: Path) -> Path:
    path = path.expanduser().resolve()
    if any(part in ('.git', '.github', 'docs') for part in path.parts):
        raise ValueError('Refusing public/CI destinations')
    if path == PROJECT or PROJECT in path.parents:
        relative = path.relative_to(PROJECT)
        if not relative.parts or relative.parts[0] != 'private_quotes':
            raise ValueError('Use gitignored private_quotes/ inside the project')
    return path


def weeks_ending(year: int, week: int, count: int) -> list[tuple[int, int]]:
    if not (2012 <= year <= 2100 and 1 <= week <= 53 and 1 <= count <= 8):
        raise ValueError('Year/week/count out of permitted bounds')
    try:
        ending = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError('Invalid ISO calendar week') from exc
    return [(d.isocalendar().year, d.isocalendar().week)
            for d in (ending - timedelta(weeks=i) for i in range(count-1, -1, -1))]


def url_for_week(year: int, week: int) -> str:
    date.fromisocalendar(year, week, 1)
    return f'{BASE}/H1/USDJPY/{year}/{week}.csv.gz'


def download_week(year: int, week: int, *, opener=None) -> tuple[bytes, dict]:
    """Download only from a fixed known provider host; no user-provided URL."""
    url = url_for_week(year, week)
    request = urllib.request.Request(url, headers={
        'User-Agent': 'FX-AEGIS-AI-personal-research/1.0',
        'Accept': 'application/gzip,application/octet-stream',
    })
    open_impl = opener or urllib.request.urlopen
    try:
        with open_impl(request, timeout=25) as response:
            actual = response.geturl()
            parsed = urllib.parse.urlsplit(actual)
            expected = urllib.parse.urlsplit(url)
            if (parsed.scheme != 'https' or parsed.hostname != HOST
                    or parsed.path != expected.path or parsed.query):
                raise ValueError('Unexpected redirect; download refused')
            compressed = response.read(MAX_COMPRESSED + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f'FXCM H1 download unavailable for {year}-W{week:02d}; no quotes accepted') from exc
    if len(compressed) > MAX_COMPRESSED or compressed[:2] != b'\x1f\x8b':
        raise ValueError('Invalid or oversized official GZIP archive')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode='rb') as stream:
            content = stream.read(MAX_DECOMPRESSED + 1)
    except (OSError, EOFError) as exc:
        raise ValueError('Corrupt official GZIP archive') from exc
    if len(content) > MAX_DECOMPRESSED:
        raise ValueError('GZIP expands beyond the permitted bound')
    return content, {'source_url': url, 'compressed_sha256': hashlib.sha256(compressed).hexdigest(),
                     'uncompressed_sha256': hashlib.sha256(content).hexdigest()}


def read_timestamp(value: str) -> datetime:
    value = value.strip()
    formats = ('%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S',
               '%m/%d/%Y %H:%M:%S.%f',
               '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ')
    timestamp = None
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        for pattern in formats:
            try:
                timestamp = datetime.strptime(value, pattern)
                break
            except ValueError:
                pass
    if timestamp is None:
        raise ValueError('Invalid FXCM candle timestamp')
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)  # Official feed documents UTC
    if timestamp.utcoffset() != timedelta(0) or any((timestamp.minute, timestamp.second,
                                                      timestamp.microsecond)):
        raise ValueError('FXCM H1 timestamp is not aligned to a UTC hour')
    return timestamp.astimezone(timezone.utc)


def fxcm_trading_week_bounds(year: int, week: int) -> tuple[datetime, datetime]:
    """Return the exclusive-end, UTC H1 archive window for an ISO week.

    The forex session begins Sunday approximately 17:00 ET (21:00/22:00 UTC).
    Allow only the immediately preceding Sunday's 21/22/23 UTC opens. Keep
    all original bar timestamps; never shift, discard or rewrite prices.
    """
    monday_date = date.fromisocalendar(year, week, 1)
    monday = datetime(monday_date.year, monday_date.month, monday_date.day,
                      tzinfo=timezone.utc)
    return monday - timedelta(hours=3), monday + timedelta(days=6, hours=21)


def belongs_to_fxcm_trading_week(when: datetime, year: int, week: int) -> bool:
    start, end = fxcm_trading_week_bounds(year, week)
    return start <= when < end


# Fixed public-only classifications for diagnosing archive-week mismatches.
# Do not include raw timestamps, price data, URLs, or exception text in logs.
def boundary_offset_bucket(delta: timedelta) -> str:
    if delta < timedelta(0):
        raise ValueError('Negative week offset is invalid')
    if delta < timedelta(hours=3):
        return '0_2H'
    if delta < timedelta(days=1):
        return '3_23H'
    if delta < timedelta(days=7):
        return '1_6D'
    if delta < timedelta(days=28):
        return '7_27D'
    return '28D_PLUS'


class H1WeekWindowError(ValueError):
    """Out-of-range bar; retain only bounded diagnostics, not source rows."""

    def __init__(self, side: str, delta: timedelta):
        if side not in ('before', 'after') or delta < timedelta(0):
            raise ValueError('Invalid week-window diagnostic')
        super().__init__(f'Provider file contains H1 data outside the specified ISO week ({side})')
        self.offset_bucket = boundary_offset_bucket(delta)
        self.archive_position: int | None = None
        self.file_tail_offset_bucket: str | None = None
        self.file_after_bars_bucket: str | None = None
        self.file_first_iso_week: str | None = None
        self.file_last_iso_week: str | None = None
        self.side = side
        self.adjacent_relation: str | None = None
        self.adjacent_shared_h1_bucket: str | None = None
        self.remaining_archive_map: list[tuple[int, str, str, str, str, str, str]] = []


def safe_after_bar_count_bucket(count: int) -> str:
    """Allowlisted counts, not the number of private market observations."""
    if count < 0:
        raise ValueError('Negative bar count')
    if count == 0:
        return 'NONE'
    if count <= 3:
        return '1_3'
    if count <= 24:
        return '4_24'
    if count <= 120:
        return '25_120'
    return '121_PLUS'


def safe_week_tail_summary(source: str, timestamp_field: str,
                           year: int, week: int) -> tuple[str, str]:
    """Inspect every source timestamp after a rejection; never return a date.

    This is diagnosis ONLY: it does not normalize, accept, skip, change or
    rewrite a candle. On invalid CSV structure, chronology, or row count the
    summary is unavailable. The normal importer still fails closed.
    """
    _, end = fxcm_trading_week_bounds(year, week)
    calendar_end = end + timedelta(hours=3)
    reader = csv.DictReader(io.StringIO(source, newline=''))
    previous = None
    last = None
    after_count = 0
    try:
        for count, row in enumerate(reader, start=1):
            if count > MAX_ROWS or not row or any(v is None for v in row.values()):
                return 'UNAVAILABLE', 'UNAVAILABLE'
            when = read_timestamp(row[timestamp_field])
            if previous is not None and when <= previous:
                return 'UNAVAILABLE', 'UNAVAILABLE'
            if when >= calendar_end:
                after_count += 1
            previous = last = when
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'UNAVAILABLE', 'UNAVAILABLE'
    if last is None or after_count == 0:
        return 'NONE', safe_after_bar_count_bucket(after_count)
    return (boundary_offset_bucket(last - calendar_end),
            safe_after_bar_count_bucket(after_count))


def safe_relative_iso_week(when: datetime, year: int, week: int) -> str:
    """Fixed week identity relative to the requested ISO year/week.

    Never return an observed datetime, year or week number.
    """
    monday = date.fromisocalendar(year, week, 1)
    index = (when.date() - monday).days // 7
    if index < -1:
        return 'FAR_BEFORE'
    if index == -1:
        return 'PREV_1'
    if index == 0:
        return 'REQUESTED'
    if index == 1:
        return 'NEXT_1'
    if index == 2:
        return 'NEXT_2'
    return 'FAR_AFTER'


def safe_week_iso_extent(source: str, timestamp_field: str,
                         year: int, week: int) -> tuple[str, str]:
    """Diagnose whole-file ISO-week coverage using only ordered timestamps.

    This supplementary scan cannot make a rejected file acceptable. Its
    externally visible outputs come exclusively from a closed vocabulary.
    """
    reader = csv.DictReader(io.StringIO(source, newline=''))
    first = None
    previous = None
    try:
        for count, row in enumerate(reader, start=1):
            if count > MAX_ROWS or not row or any(v is None for v in row.values()):
                return 'UNAVAILABLE', 'UNAVAILABLE'
            when = read_timestamp(row[timestamp_field])
            if previous is not None and when <= previous:
                return 'UNAVAILABLE', 'UNAVAILABLE'
            if first is None:
                first = when
            previous = when
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'UNAVAILABLE', 'UNAVAILABLE'
    if first is None:
        return 'UNAVAILABLE', 'UNAVAILABLE'
    return (safe_relative_iso_week(first, year, week),
            safe_relative_iso_week(previous, year, week))


def safe_h1_timestamp_index_with_status(
        content: bytes) -> tuple[list[datetime] | None, str]:
    """Return only private UTC timestamps plus a fixed, public-safe status.

    The status is from a closed set of constant identifiers. Never expose
    source row values, local paths, provider URLs, or exception messages.
    """
    try:
        reader = csv.DictReader(
            io.StringIO(content.decode('utf-8-sig'), newline=''))
        names = reader.fieldnames
        if not names:
            return None, 'SOURCE_CSV_EMPTY'
        if any(not isinstance(name, str) for name in names):
            return None, 'SOURCE_CSV_HEADER'
        normalized = {name.strip().lower(): name for name in names}
        if len(normalized) != len(names) or any(
                field not in normalized for field in REQUIRED):
            return None, 'SOURCE_CSV_HEADER'
        timestamp_column = normalized['datetime']
        result = []
        previous = None
        for raw in reader:
            if len(result) >= MAX_ROWS:
                return None, 'SOURCE_CSV_TOO_MANY_BARS'
            if not raw or None in raw or any(value is None for value in raw.values()):
                return None, 'SOURCE_CSV_ROW'
            try:
                when = read_timestamp(raw[timestamp_column])
            except ValueError as exc:
                code = safe_failure_code(exc)
                if code in ('SOURCE_CSV_TIMESTAMP',
                            'SOURCE_CSV_TIMESTAMP_ALIGNMENT'):
                    return None, code
                return None, 'SOURCE_CSV_TIMESTAMP'
            if previous is not None and when <= previous:
                return None, 'SOURCE_CSV_ORDER'
            result.append(when)
            previous = when
    except UnicodeError:
        return None, 'SOURCE_CSV_ENCODING'
    except (KeyError, TypeError, ValueError, OverflowError, csv.Error):
        return None, 'SOURCE_CSV_ROW'
    if not result:
        return None, 'SOURCE_CSV_NO_BARS'
    return result, 'VALID'


def safe_h1_timestamp_index(content: bytes) -> list[datetime] | None:
    """Compatibility helper: diagnosis only, never accepts quote data."""
    return safe_h1_timestamp_index_with_status(content)[0]

def safe_adjacent_week_relation(
        first_content: bytes, second_content: bytes) -> tuple[str, str]:
    """Classify only timestamp overlap/continuity between adjacent archives.

    Does not approve imported data or deduplicate private H1 bars. Both
    archives must independently have strictly increasing UTC-hour timestamps.
    """
    first = safe_h1_timestamp_index(first_content)
    second = safe_h1_timestamp_index(second_content)
    if first is None or second is None:
        return 'UNAVAILABLE', 'UNAVAILABLE'
    shared_count = len(set(first).intersection(second))
    if second[0] <= first[-1]:
        relation = ('OVERLAP_SHARED' if shared_count
                    else 'OVERLAP_NO_SHARED')
    elif second[0] == first[-1] + timedelta(hours=1):
        relation = 'CONTIGUOUS'
    else:
        relation = 'GAP'
    return relation, safe_after_bar_count_bucket(shared_count)


def safe_remaining_archive_manifest(
        spans: list[tuple[int, int]], failed_position: int,
        rejected_content: bytes, *, opener=None
        ) -> list[tuple[int, str, str, str, str, str, str]]:
    """Classify the remaining already-authorized FXCM archives after failure.

    Returned tuples are strictly: bounded ordinal, first/last *relative*
    ISO week, adjacent relationship, shared timestamp count bucket and
    gap-duration bucket and a fixed file-status code. Prices, dates and URLs
    are never exposed. All archives remain rejected and are never saved.
    """
    if not 1 <= failed_position <= len(spans) <= 8:
        return []
    previous_index = safe_h1_timestamp_index(rejected_content)
    manifest = []
    for position in range(failed_position + 1, len(spans) + 1):
        y, w = spans[position - 1]
        try:
            next_content, _ = download_week(y, w, opener=opener)
            current_index, file_status = safe_h1_timestamp_index_with_status(
                next_content)
        except (OSError, ValueError, RuntimeError) as exc:
            current_index = None
            # Existing allowlist maps HTTP failures and GZIP validation to
            # fixed enums; the raw exception is never printed or preserved.
            file_status = safe_failure_code(exc)

        if current_index is None:
            first_iso = last_iso = 'UNAVAILABLE'
        else:
            first_iso = safe_relative_iso_week(current_index[0], y, w)
            last_iso = safe_relative_iso_week(current_index[-1], y, w)

        if previous_index is None or current_index is None:
            relation = shared = gap = 'UNAVAILABLE'
        else:
            overlap = len(set(previous_index).intersection(current_index))
            shared = safe_after_bar_count_bucket(overlap)
            if current_index[0] <= previous_index[-1]:
                relation = 'OVERLAP_SHARED' if overlap else 'OVERLAP_NO_SHARED'
                gap = 'NONE'
            elif current_index[0] == previous_index[-1] + timedelta(hours=1):
                relation, gap = 'CONTIGUOUS', 'NONE'
            else:
                relation = 'GAP'
                gap = boundary_offset_bucket(
                    current_index[0] - previous_index[-1] - timedelta(hours=1))
        manifest.append((position, first_iso, last_iso, relation, shared, gap, file_status))
        previous_index = current_index
    return manifest


def parse_week_csv(content: bytes, year: int, week: int) -> list[dict]:
    """Map by *named* Bid/Ask fields only. Never fabricate Ask from mid/bid."""
    try:
        source = content.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('Unreadable provider CSV encoding') from exc
    reader = csv.DictReader(io.StringIO(source, newline=''))
    if not reader.fieldnames:
        raise ValueError('Empty provider CSV')
    normalized = {name.strip().lower(): name for name in reader.fieldnames}
    if any(name not in normalized for name in REQUIRED) or len(normalized) != len(reader.fieldnames):
        raise ValueError('FXCM CSV must include DateTime and all 8 named Bid/Ask OHLC fields')
    week_start, week_end = fxcm_trading_week_bounds(year, week)
    # Two strictly bounded candidates: Sunday-session week or ISO calendar week.
    # Allow a file to follow either one, but never both (would risk overlaps).
    calendar_start = week_start + timedelta(hours=3)
    calendar_end = week_end + timedelta(hours=3)
    has_prior_sunday = False
    has_last_sunday = False
    records = []
    previous = None
    for raw in reader:
        if any(v is None for v in raw.values()):
            raise ValueError('Incomplete FXCM CSV row')
        when = read_timestamp(raw[normalized['datetime']])
        if when < week_start:
            raise H1WeekWindowError('before', week_start - when)
        if when >= calendar_end:
            exc = H1WeekWindowError('after', when - calendar_end)
            (exc.file_tail_offset_bucket,
             exc.file_after_bars_bucket) = safe_week_tail_summary(
                 source, normalized['datetime'], year, week)
            (exc.file_first_iso_week,
             exc.file_last_iso_week) = safe_week_iso_extent(
                 source, normalized['datetime'], year, week)
            raise exc
        if when < calendar_start:
            has_prior_sunday = True
        if when >= week_end:
            has_last_sunday = True
        if has_prior_sunday and has_last_sunday:
            raise ValueError('Provider H1 archive mixes both Sunday week boundaries')
        if previous is not None and when <= previous:
            raise ValueError('Duplicate or nonmonotonic FXCM H1 timestamps')
        previous = when
        row = {'time': when.strftime('%Y-%m-%dT%H:%M:%SZ')}
        for pfx in ('bid', 'ask'):
            for axis in ('open', 'high', 'low', 'close'):
                try:
                    value = float(raw[normalized[pfx + axis]])
                except (TypeError, ValueError) as exc:
                    raise ValueError('Invalid FXCM Bid/Ask price field') from exc
                if not math.isfinite(value) or value <= 0:
                    raise ValueError('Nonpositive or nonfinite FXCM price')
                row[pfx + '_' + axis] = value
            if (row[pfx + '_high'] < max(row[pfx + '_open'],row[pfx + '_close'],row[pfx + '_low'])
                    or row[pfx + '_low'] > min(row[pfx + '_open'],row[pfx + '_close'],row[pfx + '_high'])):
                raise ValueError('FXCM OHLC high/low is inconsistent')
        if (row['ask_open'] < row['bid_open'] or row['ask_close'] < row['bid_close']
                or row['ask_open'] - row['bid_open'] > .5
                or row['ask_close'] - row['bid_close'] > .5):
            raise ValueError('FXCM Bid/Ask order or spread is invalid')
        records.append(row)
        if len(records) > MAX_ROWS:
            raise ValueError('FXCM week contains too many H1 bars')
    if not records:
        raise ValueError('Official H1 week contains no bars')
    return records


def acquire(*, year: int, week: int, weeks: int, out: Path,
            enable_download: bool, personal_use_accepted: bool, opener=None) -> dict:
    if not enable_download or not personal_use_accepted:
        raise ValueError('Explicit --enable-download and --accept-fxcm-personal-use are both mandatory')
    spans = weeks_ending(year,week,weeks)
    output = private_destination(out)
    if output.exists():
        raise ValueError('Target snapshot already exists; no overwrite or implicit replay')
    rows=[];sources=[];prior=None;observed_modes=set()
    for archive_position, (y,w) in enumerate(spans, start=1):
        content,meta=download_week(y,w,opener=opener)
        try:
            items=parse_week_csv(content,y,w)
        except H1WeekWindowError as exc:
            exc.archive_position = archive_position
            if exc.side == 'after' and archive_position < len(spans):
                # Diagnose only the remaining *already-authorized* weeks.
                # Never bypass the original validation failure or save files.
                exc.remaining_archive_map = safe_remaining_archive_manifest(
                    spans, archive_position, content, opener=opener)
                if exc.remaining_archive_map:
                    _, _, _, relation, shared, _, _ = exc.remaining_archive_map[0]
                    exc.adjacent_relation = relation
                    exc.adjacent_shared_h1_bucket = shared
            raise
        first = read_timestamp(items[0]['time'])
        last = read_timestamp(items[-1]['time'])
        session_start, session_end = fxcm_trading_week_bounds(y,w)
        if first < session_start + timedelta(hours=3):
            mode = 'SUNDAY_SESSION_START'
        elif last >= session_end:
            mode = 'ISO_CALENDAR_WEEK'
        else:
            mode = 'BOUNDARY_NOT_OBSERVED'
        if mode != 'BOUNDARY_NOT_OBSERVED':
            observed_modes.add(mode)
        if len(observed_modes) > 1:
            raise ValueError('FXCM weekly archives disagree on week boundary convention')
        if prior is not None and first <= prior:
            raise ValueError('Cross-week H1 chronology revised or duplicated')
        rows.extend(items)
        prior=last
        sources.append({**meta, 'week_partition_mode': mode})
    if len(rows)<200:
        raise ValueError('Need at least 200 completed H1 bars; expand the authorized week range')
    if (read_timestamp(rows[-1]['time'])+timedelta(hours=1))>datetime.now(timezone.utc):
        raise ValueError('Incomplete or future H1 candle refused')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.fxcm-h1-stage-',dir=output.parent))
    try:
        q=stage/'bidask_h1.csv';p=stage/'provenance.json'
        with q.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=COLUMNS,lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow({key:row[key] if key=='time' else f'{row[key]:.6f}' for key in COLUMNS})
        digest=hashlib.sha256(q.read_bytes()).hexdigest()
        provenance={
           'schema':SCHEMA,'source_kind':'BROKER_BID_ASK_H1',
           'source_name':'FXCM official published indicative H1 Bid/Ask historical sample',
           'symbol':'USDJPY','timestamp_semantics':'BAR_OPEN','account_currency':'JPY',
           'quote_timezone':'UTC','sha256':digest,
           'license_scope':'FXCM personal use only; user acknowledged EULA; no redistribution',
           'source_url_pattern':BASE+'/H1/USDJPY/{year}/{week}.csv.gz',
           'source_archives':sources,'source_files':len(sources),'source_rows':len(rows),
           'start_utc':rows[0]['time'],'end_utc':rows[-1]['time'],
           'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),
           'broker_origin_independently_confirmed':False,'user_supplied_access_rights':True,
           'approved_for_live_trading':False,'order_placement':'DISABLED',
           'note':'FXCM indicative quotes, not a record of executed orders. Provider rights are personal use only.',
        }
        p.write_text(json.dumps(provenance,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        # Enforce the exact schema used by the AI/Canvas before atomic visibility.
        # This import occurs locally; it does not contact any provider.
        from execution_lab import load_quotes
        _,_,audit=load_quotes(q,p)
        if audit['quotes_sha256'] != digest:
            raise ValueError('Final H1 audit SHA mismatch')
        os.replace(stage,output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {'status':'USER_FETCHED_FXCM_H1_INDICATIVE_QUOTES',
            'h1_bars':len(rows),'start_utc':rows[0]['time'],'end_utc':rows[-1]['time'],
            'quotes_sha256':digest,'out':str(output),'approved_for_live_trading':False}


def safe_failure_code(exc: Exception) -> str:
    """Return only a fixed public diagnostic enum; never log remote/quote content.

    HTTP errors are extracted from the original wrapped exception, but the
    provider URL, HTTP reason text, response body and local paths are omitted.
    """
    if isinstance(exc, RuntimeError) and str(exc).startswith(
            'FXCM H1 download unavailable for '):
        cause = exc.__cause__
        if isinstance(cause, urllib.error.HTTPError):
            if cause.code == 404:
                return 'SOURCE_HTTP_404'
            if cause.code == 403:
                return 'SOURCE_HTTP_403'
            if cause.code == 429:
                return 'SOURCE_HTTP_429'
            if 500 <= cause.code <= 599:
                return 'SOURCE_HTTP_5XX'
            return 'SOURCE_HTTP_OTHER'
        if isinstance(cause, TimeoutError):
            return 'SOURCE_TIMEOUT'
        return 'SOURCE_CONNECTION_ERROR'
    if isinstance(exc, ValueError):
        known = {
            'Explicit --enable-download and --accept-fxcm-personal-use are both mandatory': 'OWNER_OPT_IN_REQUIRED',
            'Refusing public/CI destinations': 'DESTINATION_REJECTED',
            'Use gitignored private_quotes/ inside the project': 'DESTINATION_REJECTED',
            'Target snapshot already exists; no overwrite or implicit replay': 'DESTINATION_EXISTS',
            'Unexpected redirect; download refused': 'SOURCE_REDIRECT_REJECTED',
            'Invalid or oversized official GZIP archive': 'SOURCE_GZIP_INVALID',
            'Corrupt official GZIP archive': 'SOURCE_GZIP_CORRUPT',
            'GZIP expands beyond the permitted bound': 'SOURCE_GZIP_OVERSIZED',
            'Unreadable provider CSV encoding': 'SOURCE_CSV_ENCODING',
            'Empty provider CSV': 'SOURCE_CSV_EMPTY',
            'FXCM CSV must include DateTime and all 8 named Bid/Ask OHLC fields': 'SOURCE_CSV_HEADER',
            'Incomplete FXCM CSV row': 'SOURCE_CSV_ROW',
            'Invalid FXCM candle timestamp': 'SOURCE_CSV_TIMESTAMP',
            'FXCM H1 timestamp is not aligned to a UTC hour': 'SOURCE_CSV_TIMESTAMP_ALIGNMENT',
            'Provider file contains H1 data outside the specified ISO week': 'SOURCE_CSV_WEEK_MISMATCH',
            'Provider file contains H1 data outside the specified ISO week (before)': 'SOURCE_CSV_WEEK_BEFORE',
            'Provider file contains H1 data outside the specified ISO week (after)': 'SOURCE_CSV_WEEK_AFTER',
            'Provider H1 archive mixes both Sunday week boundaries': 'SOURCE_CSV_WEEK_BOUNDARY_MIXED',
            'FXCM weekly archives disagree on week boundary convention': 'SOURCE_WEEK_CONVENTION_DRIFT',
            'Duplicate or nonmonotonic FXCM H1 timestamps': 'SOURCE_CSV_ORDER',
            'Invalid FXCM Bid/Ask price field': 'SOURCE_CSV_PRICE_FIELD',
            'Nonpositive or nonfinite FXCM price': 'SOURCE_CSV_PRICE_RANGE',
            'FXCM OHLC high/low is inconsistent': 'SOURCE_CSV_OHLC_INVALID',
            'FXCM Bid/Ask order or spread is invalid': 'SOURCE_CSV_SPREAD_INVALID',
            'FXCM week contains too many H1 bars': 'SOURCE_CSV_TOO_MANY_BARS',
            'Official H1 week contains no bars': 'SOURCE_CSV_NO_BARS',
            'Cross-week H1 chronology revised or duplicated': 'SOURCE_CROSS_WEEK_ORDER',
            'Need at least 200 completed H1 bars; expand the authorized week range': 'SOURCE_INSUFFICIENT_BARS',
            'Incomplete or future H1 candle refused': 'SOURCE_INCOMPLETE_BAR',
            'Final H1 audit SHA mismatch': 'VALIDATION_DIGEST_MISMATCH',
            'Year/week/count out of permitted bounds': 'ARGUMENTS_INVALID',
            'Invalid ISO calendar week': 'ARGUMENTS_INVALID',
        }
        return known.get(str(exc), 'H1_DATA_VALIDATION_FAILED')
    if isinstance(exc, OSError):
        return 'LOCAL_IO_FAILED'
    return 'H1_PROCESSING_FAILED'


def main() -> int:
    parser=argparse.ArgumentParser(description='FXCM H1 personal-use-only private downloader; never orders')
    parser.add_argument('--year',type=int,required=True,help='ISO week-year, e.g. 2026')
    parser.add_argument('--week',type=int,required=True,help='Last ISO week number, e.g. 46')
    parser.add_argument('--weeks',type=int,default=4,help='Number of complete ISO weeks ending on --week; 1-8')
    parser.add_argument('--out',type=Path,default=Path('private_quotes/fxcm_usdjpy_h1'))
    parser.add_argument('--enable-download',action='store_true')
    parser.add_argument('--accept-fxcm-personal-use',action='store_true')
    args=parser.parse_args()
    try:
        result=acquire(year=args.year,week=args.week,weeks=args.weeks,out=args.out,
                       enable_download=args.enable_download,
                       personal_use_accepted=args.accept_fxcm_personal_use)
    except (OSError,ValueError,RuntimeError) as exc:
        # Avoid printing local data/credentials or remote responses.
        code = safe_failure_code(exc)
        print(json.dumps({'status':'FAILED_CLOSED','error_type':type(exc).__name__,
                          'error_code':code,'approved_for_live_trading':False}))
        # The workflow sends stdout to a private temporary file. Only this
        # fixed enum goes to stderr/Actions logs; never print the exception.
        print(f'FXCM_SAFE_DIAGNOSTIC={code}', file=sys.stderr)
        if isinstance(exc, H1WeekWindowError):
            # Fixed offset bins and a bounded ordinal only. No source dates,
            # bar contents, local filenames, endpoint responses or secrets.
            print(f'FXCM_SAFE_WEEK_OFFSET={exc.offset_bucket}', file=sys.stderr)
            if exc.archive_position is not None and 1 <= exc.archive_position <= 8:
                print(f'FXCM_SAFE_ARCHIVE_POSITION={exc.archive_position}', file=sys.stderr)
            if exc.file_tail_offset_bucket is not None:
                print(f'FXCM_SAFE_FILE_TAIL_OFFSET={exc.file_tail_offset_bucket}', file=sys.stderr)
            if exc.file_after_bars_bucket is not None:
                print(f'FXCM_SAFE_FILE_AFTER_BARS={exc.file_after_bars_bucket}', file=sys.stderr)
            if exc.file_first_iso_week is not None:
                print(f'FXCM_SAFE_FIRST_ISO_WEEK={exc.file_first_iso_week}', file=sys.stderr)
            if exc.file_last_iso_week is not None:
                print(f'FXCM_SAFE_LAST_ISO_WEEK={exc.file_last_iso_week}', file=sys.stderr)
            if exc.adjacent_relation is not None:
                print(f'FXCM_SAFE_ADJACENT_RELATION={exc.adjacent_relation}', file=sys.stderr)
            if exc.adjacent_shared_h1_bucket is not None:
                print(f'FXCM_SAFE_ADJACENT_SHARED_H1={exc.adjacent_shared_h1_bucket}', file=sys.stderr)
            for ordinal, first_iso, last_iso, relation, shared, gap, file_status in exc.remaining_archive_map:
                # Ordinal is limited to 2..8; all other fields are fixed enums.
                print(
                    f'FXCM_SAFE_ARCHIVE_MAP_{ordinal}='
                    f'{first_iso}/{last_iso}/{relation}/{shared}/{gap}',
                    file=sys.stderr)
                print(f'FXCM_SAFE_ARCHIVE_STATUS_{ordinal}={file_status}',
                      file=sys.stderr)
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())

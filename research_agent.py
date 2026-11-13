"""Internet research agent: verified-source search and BLS release calendar.

Intentional separation: online information is untrusted for direct orders.
The calendar CSV records the time we retrieved it, preventing a 2026
calendar snapshot from being replayed as 2023 foreknowledge.
No secrets are written to the results or logs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd

from trading_conditions import trusted_url

BRAVE_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
BLS_CALENDAR = 'https://www.bls.gov/schedule/news_release/bls.ics'
DEFAULT_QUERY = 'USD JPY monetary policy FOMC CPI employment report Bank of Japan'
INTERESTING_RELEASES = (
    'consumer price index', 'employment situation', 'producer price index',
    'import and export price', 'employment cost index',
)


def fetch(url: str, headers=None, timeout=15) -> bytes:
    req = Request(url, headers=headers or {'User-Agent': 'FXResearchAgent/1.1 (research-only)'})
    with urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP status {response.status}')
        data = response.read(3_000_001)
        if len(data) > 3_000_000:
            raise ValueError('Response too large; refusing')
        return data


def search(query: str, output: Path, api_key: str) -> dict:
    if not api_key:
        raise ValueError('BRAVE_API_KEY required. Do not put credentials in source code.')
    search_q = query.strip()[:300]
    if not search_q:
        raise ValueError('Query cannot be empty')
    url = BRAVE_ENDPOINT + '?' + urlencode({'q': search_q, 'count': 20})
    payload = json.loads(fetch(url, headers={
        'X-Subscription-Token': api_key, 'Accept': 'application/json',
        'User-Agent': 'FXResearchAgent/1.1 (research-only)'}))
    found = []
    for entry in payload.get('web', {}).get('results', []):
        link = entry.get('url', '')
        if trusted_url(link):
            found.append({'title': entry.get('title', '')[:300],
                          'source_url': link, 'snippet': entry.get('description', '')[:1200]})
    result = {'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
              'query': search_q, 'status': 'information_only_no_trade_override',
              'trusted_official_hits': found,
              'disclaimer': 'Search snippets are not verified event time or market data; no automatic trade decision.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def _unfold_ics(content: str) -> list[str]:
    result = []
    for line in content.splitlines():
        if line.startswith((' ', '\t')) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    return result


def _parse_ics_dt(prop: str, value: str) -> datetime | None:
    """Only timed, timezone-defined events; unknown timezone fails closed."""
    value = value.strip()
    if 'VALUE=DATE' in prop or re.fullmatch(r'\d{8}', value):
        return None
    if value.endswith('Z'):
        dt = datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    else:
        tzmatch = re.search(r'TZID=([^;:]+)', prop)
        if not tzmatch:
            return None
        dt = datetime.strptime(value, '%Y%m%dT%H%M%S').replace(tzinfo=ZoneInfo(tzmatch.group(1)))
    return dt.astimezone(timezone.utc)


def parse_bls_calendar(ics: str, retrieved_at: datetime) -> pd.DataFrame:
    events = []
    block = None
    for line in _unfold_ics(ics):
        if line == 'BEGIN:VEVENT':
            block = []
        elif line == 'END:VEVENT':
            if block is not None:
                fields = [(x.split(':', 1)[0], x.split(':', 1)[1]) for x in block if ':' in x]
                summaries = [v for k, v in fields if k == 'SUMMARY']
                times = [(k, v) for k, v in fields if k.startswith('DTSTART')]
                if summaries and times and any(k in summaries[0].lower() for k in INTERESTING_RELEASES):
                    when = _parse_ics_dt(*times[0])
                    if when is not None and when >= retrieved_at:
                        events.append({
                            'event_time_utc': when.isoformat(),
                            'known_at_utc': retrieved_at.isoformat(),
                            'event': summaries[0].replace('\\,', ',')[:250],
                            'source_url': BLS_CALENDAR,
                        })
            block = None
        elif block is not None:
            block.append(line)
    if not events:
        raise ValueError('No future, timed, relevant BLS releases parsed; do not use this empty calendar')
    return pd.DataFrame(events).drop_duplicates().sort_values('event_time_utc')


def main() -> None:
    p = argparse.ArgumentParser(description='Internet research ONLY, no trade connectivity')
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('search', help='Search official domains (Brave API key required)')
    s.add_argument('--query', default=DEFAULT_QUERY)
    s.add_argument('--out', default='data/official_web_search.json')
    c = sub.add_parser('bls-calendar', help='Retrieve future BLS events for blackout filter')
    c.add_argument('--out', default='data/bls_events.csv')
    args = p.parse_args()
    if args.command == 'search':
        snapshot = search(args.query, Path(args.out), os.getenv('BRAVE_API_KEY', ''))
        print(f"Saved {len(snapshot['trusted_official_hits'])} official search results to {args.out}")
    else:
        now = datetime.now(timezone.utc)
        csv = parse_bls_calendar(fetch(BLS_CALENDAR).decode('utf-8-sig', errors='replace'), now)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        csv.to_csv(args.out, index=False)
        print(f'Saved {len(csv)} scheduled future releases to {args.out}; timestamped as known only on retrieval')


if __name__ == '__main__':
    main()

"""Private OANDA v20 H1 USD/JPY Bid/Ask downloader; NEVER places orders.

The downloaded candles are OANDA broker quotes, NOT actual user trade fills.
Token only goes in Authorization header. Only explicit approved hosts are supported.
Data files must stay private; no uploads to public GitHub, Pages or artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from execution_lab import COLUMNS, SCHEMA, load_quotes, sha256_file, write_json

API_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
SOURCE_ENDPOINT = "/v3/instruments/USD_JPY/candles"
PROJECT = Path(__file__).resolve().parent
MAX_CHUNK_DAYS = 21  # <5000 H1 candles per OANDA range request


def utc_instant(value: str) -> datetime:
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        value += "T00:00:00Z"
    if not (value.endswith("Z") or (len(value) > 6 and value[-6] in ("+", "-") and value[-3] == ":")):
        raise ValueError("Dates require UTC or an explicit time-zone offset")
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.utcoffset() is None:
        raise ValueError("Naive date is not allowed")
    when = when.astimezone(timezone.utc)
    if any((when.minute, when.second, when.microsecond)):
        raise ValueError("H1 range endpoints must align to UTC hours")
    return when


def _private_destination(out: Path) -> Path:
    out = out.expanduser().resolve()
    if out == PROJECT or PROJECT in out.parents:
        relative = out.relative_to(PROJECT)
        if not relative.parts or relative.parts[0] not in ("private_quotes", "datasets", "data"):
            raise ValueError("Broker files inside project must be stored under ignored private_quotes/, data/, or datasets/broker/")
        if relative.parts[0] == "datasets" and (len(relative.parts) < 2 or relative.parts[1] != "broker"):
            raise ValueError("Broker data must be stored under datasets/broker/ (gitignored)")
    # Do not create a publicly served asset folder even if placed outside project.
    if any(part in ("docs", ".github") for part in out.parts):
        raise ValueError("Refusing to place broker quote data in a public/CI directory")
    return out


def _get_json(url: str, token: str, *, opener=urlopen) -> dict:
    req = Request(url, headers={"Authorization": "Bearer " + token,
                                "Accept": "application/json", "User-Agent": "FX-AEGIS-AI-research/2.3"})
    for attempt in range(4):
        try:
            with opener(req, timeout=25) as response:
                body = response.read(3_000_001)
                if len(body) > 3_000_000:
                    raise ValueError("Unexpectedly large OANDA API response")
                return json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(min(2 ** attempt, 5))
                continue
            # No URL, token, request/response body, or headers are included.
            raise RuntimeError(f"OANDA API HTTP status {exc.code}; check token, account and rate limits") from None
        except URLError:
            if attempt < 3:
                time.sleep(min(2 ** attempt, 5))
                continue
            raise RuntimeError("OANDA API unavailable; connection failed") from None
    raise RuntimeError("Request exhausted retry limit")


def normalize_response(payload: dict, start: datetime, end: datetime) -> list[dict]:
    """Validate the actual OANDA candle response and normalize complete H1 quotes."""
    if not isinstance(payload, dict) or payload.get("instrument") != "USD_JPY" or payload.get("granularity") != "H1":
        raise ValueError("Unexpected OANDA instrument or granularity")
    candles = payload.get("candles")
    if not isinstance(candles, list) or len(candles) > 5000:
        raise ValueError("Missing/oversized candles collection")
    out = []
    for candle in candles:
        if not isinstance(candle, dict) or candle.get("complete") is not True:
            continue  # never use an unfinished candle
        stamp = pd.Timestamp(candle["time"])
        if stamp.tzinfo is None:
            raise ValueError("Candle has no UTC offset")
        stamp = stamp.tz_convert("UTC")
        if stamp.minute or stamp.second or stamp.microsecond or stamp.nanosecond:
            raise ValueError("Candle time is not H1 aligned")
        if not (pd.Timestamp(start) <= stamp < pd.Timestamp(end)):
            raise ValueError("Candle outside requested UTC range")
        if (stamp + pd.Timedelta(hours=1)) > pd.Timestamp.now(tz="UTC"):
            raise ValueError("Provider returned a future/incomplete bar")
        row = {"time": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")}
        for pfx in ("bid", "ask"):
            book = candle.get(pfx)
            if not isinstance(book, dict):
                raise ValueError("Both Bid and Ask H1 OHLC components are required")
            for key, suffix in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
                row[pfx + "_" + suffix] = float(book[key])
        out.append(row)
    return out


def fetch_oanda(start: datetime, end: datetime, *, environment: str, token: str,
                getter=None) -> tuple[list[dict], dict]:
    """Fetch bounded ranges, preserving a hash of provider candle payloads."""
    if environment not in API_HOSTS:
        raise ValueError("Unsupported OANDA environment")
    if not token or any(c.isspace() for c in token):
        raise ValueError("OANDA_API_TOKEN is missing/invalid")
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Invalid chronological UTC range")
    if (end - start) > timedelta(days=366 * 21):
        raise ValueError("Fetch span exceeds 21 years; split into separately auditable runs")
    if end > datetime.now(timezone.utc) + timedelta(hours=1):
        raise ValueError("Cannot request dates in the future")
    getter = getter or _get_json
    entries: dict[str,dict] = {}
    digest = hashlib.sha256()
    requests = 0
    cursor = start
    while cursor < end:
        edge = min(end, cursor + timedelta(days=MAX_CHUNK_DAYS))
        q = urlencode({"price": "BA", "granularity": "H1", "smooth": "false",
                       "from": cursor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                       "to": edge.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")})
        url = API_HOSTS[environment] + SOURCE_ENDPOINT + "?" + q
        response = getter(url, token)
        digest.update(json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        for row in normalize_response(response, cursor, edge):
            t = row["time"]
            if t in entries and row != entries[t]:
                raise ValueError("Conflicting H1 values at the same timestamp")
            entries[t] = row
        requests += 1
        cursor = edge
    rows = [entries[t] for t in sorted(entries)]
    if len(rows) < 200:
        raise ValueError("Insufficient completed Bid/Ask H1 candles (<200). Extend the request range")
    return rows, {"api_response_sha256": digest.hexdigest(), "requests": requests,
                  "requested_from_utc": start.isoformat(), "requested_to_utc": end.isoformat(),
                  "environment": environment, "source_url": API_HOSTS[environment] + SOURCE_ENDPOINT}


def save_private_quotes(out: Path, rows: list[dict], collection: dict,
                        *, license_scope: str) -> tuple[Path, Path, dict]:
    out = _private_destination(out)
    if not license_scope.strip() or license_scope.strip().lower() == "public":
        raise ValueError("Specify a private, verified licensed-use scope")
    # Avoid leaving half a public result if validation fails; output itself is private.
    out.mkdir(parents=True, exist_ok=True)
    quotes_path, meta_path = out / "bidask_h1.csv", out / "provenance.json"
    if quotes_path.exists() or meta_path.exists():
        raise ValueError("Destination already exists; do not overwrite a prior immutable snapshot")
    frame = pd.DataFrame(rows, columns=COLUMNS)
    tmp = out / ".candidate_bidask_h1.csv"
    try:
        frame.to_csv(tmp, index=False, float_format="%.6f")
        meta = {"schema": SCHEMA, "source_kind": "BROKER_BID_ASK_H1",
                "source_name": "OANDA v20 H1 Bid/Ask " + collection["environment"],
                "symbol": "USDJPY", "timestamp_semantics": "BAR_OPEN", "account_currency": "JPY",
                "quote_timezone": "UTC", "sha256": sha256_file(tmp),
                "license_scope": license_scope, "provider_endpoint": collection["source_url"],
                "provider_response_sha256": collection["api_response_sha256"],
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                "request_from_utc": collection["requested_from_utc"],
                "request_to_utc": collection["requested_to_utc"],
                "request_count": collection["requests"],
                "note": "Authenticated broker candle feed, not a historical trade fill or proof of transferable data rights."}
        temp_meta = out / ".candidate_provenance.json"
        write_json(temp_meta, meta)
        # Check integrity and spread/chronology before making the final snapshot visible.
        quotes, _, audit = load_quotes(tmp, temp_meta)
        tmp.replace(quotes_path)
        temp_meta.replace(meta_path)
        return quotes_path, meta_path, audit
    finally:
        tmp.unlink(missing_ok=True)
        (out / ".candidate_provenance.json").unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Private OANDA USD/JPY H1 Bid/Ask ingestion; research only")
    parser.add_argument("--from-utc", required=True, dest="start")
    parser.add_argument("--to-utc", required=True, dest="end")
    parser.add_argument("--out", type=Path, default=Path("private_quotes/oanda_snapshot"))
    parser.add_argument("--environment", choices=sorted(API_HOSTS), default="practice")
    parser.add_argument("--license-scope", required=True)
    parser.add_argument("--license-acknowledged", action="store_true")
    parser.add_argument("--enable-api", action="store_true", help="Explicit opt-in; OANDA is disabled by default")
    args = parser.parse_args()
    if not args.enable_api:
        parser.error("OANDA API is ON HOLD: no network call unless --enable-api is explicitly supplied")
    if not args.license_acknowledged:
        parser.error("You must check your OANDA data license and explicitly pass --license-acknowledged")
    token = os.environ.get("OANDA_API_TOKEN", "")
    rows, provenance = fetch_oanda(utc_instant(args.start), utc_instant(args.end),
                                   environment=args.environment, token=token)
    quotes, meta, audit = save_private_quotes(args.out, rows, provenance, license_scope=args.license_scope)
    print(json.dumps({"quotes_path": str(quotes), "provenance_path": str(meta),
                      "rows": audit["rows"], "start_utc": audit["start_utc"],
                      "end_utc": audit["end_utc"], "quote_sha256": audit["quotes_sha256"],
                      "status": "BROKER_RESEARCH_NOT_EXECUTED", "approved_for_live_trading": False},ensure_ascii=False))


if __name__ == "__main__":
    main()

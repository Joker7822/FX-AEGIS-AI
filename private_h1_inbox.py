"""Offline, manually supplied, atomic private H1 snapshot inbox for AEGIS forward research.

No broker/network access, account access, order placement, background scheduler,
model re-freezing, or public data publication. Does NOT prove provider authenticity
or timestamp integrity beyond the local host. Ingests only validated complete
H1 Bid/Ask snapshot pairs; records the real first-seen time via forward_supervisor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from execution_lab import load_quotes
from forward_monitor import PROJECT, _private_out, _utc_now
from forward_supervisor import BLOCKED, run_once
from forward_dashboard_bridge import write_export

SCHEMA = "fx-aegis-private-h1-drop-v1"
READY = "ready.json"
QUOTES = "bidask_h1.csv"
PROVENANCE = "provenance.json"
MAX_QUOTES_BYTES = 75 * 1024 * 1024
MAX_PROVENANCE_BYTES = 128 * 1024
MAX_READY_BYTES = 16 * 1024
MAX_PENDING = 128
HEX_ID = re.compile(r"^[a-f0-9]{32}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")


def _private_inbox(path: Path) -> Path:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError("Symlink inbox is forbidden")
    target = raw.resolve()
    if any(part in ("docs", ".git", ".github") for part in target.parts):
        raise ValueError("Private inbox must not be placed in public site or Git directory")
    if target == PROJECT:
        raise ValueError("Private inbox cannot be the repository root")
    if PROJECT in target.parents:
        root = target.relative_to(PROJECT).parts[0]
        if root not in ("private_quotes", "private_reports", "results", "data"):
            raise ValueError("In-repository inbox must be under an ignored private folder")
    return target


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError("Private directory must not be a symlink or group/world accessible")


def _safe_file(path: Path, cap: int) -> int:
    if path.is_symlink():
        raise ValueError("Symlink input is forbidden")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > cap:
        raise ValueError("Input is not a nonempty bounded regular file")
    return info.st_size


def _copy_checked(src: Path, dst: Path, cap: int) -> tuple[str, int]:
    before_bytes = _safe_file(src, cap)
    before = src.stat()
    digest = hashlib.sha256()
    written = 0
    # No-follow open prevents a late symlink substitution between the checks.
    fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "rb") as stream, dst.open("xb") as target:
            os.chmod(dst, 0o600)
            while chunk := stream.read(1024 * 1024):
                written += len(chunk)
                if written > cap:
                    raise ValueError("Input exceeded bounded file length during copy")
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    after = src.stat()
    if (written != before_bytes or before.st_dev != after.st_dev or
            before.st_ino != after.st_ino or before.st_mtime_ns != after.st_mtime_ns or
            before.st_size != after.st_size):
        raise ValueError("Snapshot changed during copy")
    return digest.hexdigest(), written


def _save_json(path: Path, record: dict) -> None:
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_READY_BYTES:
        raise ValueError("Ready marker exceeds safety cap")
    with path.open("xb") as f:
        os.chmod(path, 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _clock(_test_asof: datetime | None, *, allow_synthetic: bool) -> datetime:
    # Test override is never allowed in a real-data invocation.
    return _utc_now(_test_asof, synthetic=allow_synthetic)


def _validate_pair(quote_path: Path, meta_path: Path, *, allow_synthetic: bool,
                   observed_at: datetime) -> tuple[dict, dict, dict]:
    quotes, meta, audit = load_quotes(quote_path, meta_path, allow_synthetic=allow_synthetic)
    if meta["source_kind"] == "SYNTHETIC_TEST_FIXTURE" and not allow_synthetic:
        raise ValueError("Test fixture must be explicitly marked synthetic")
    last_time = quotes["time"].iloc[-1].to_pydatetime()
    if last_time + timedelta(hours=1) > observed_at:
        raise ValueError("Latest H1 bar is not yet completed")
    return {"rows": int(len(quotes)), "first_bar_time_utc": quotes["time"].iloc[0].isoformat(),
            "last_bar_time_utc": quotes["time"].iloc[-1].isoformat()}, meta, audit


def _id(quote_hash: str, meta_hash: str) -> str:
    return hashlib.sha256((quote_hash + ":" + meta_hash).encode("ascii")).hexdigest()[:32]


def _verify_marker(marker: dict, quote_hash: str, meta_hash: str,
                   quote_bytes: int, meta_bytes: int, info: dict, meta: dict) -> None:
    if (not isinstance(marker, dict) or marker.get("schema") != SCHEMA
            or marker.get("snapshot_id") != _id(quote_hash, meta_hash)
            or marker.get("quotes_sha256") != quote_hash
            or marker.get("provenance_sha256") != meta_hash
            or marker.get("quotes_bytes") != quote_bytes
            or marker.get("provenance_bytes") != meta_bytes
            or marker.get("rows") != info["rows"]
            or marker.get("last_bar_time_utc") != info["last_bar_time_utc"]
            or marker.get("first_bar_time_utc") != info["first_bar_time_utc"]
            or marker.get("source_kind") != meta["source_kind"]
            or marker.get("approved_for_live_trading") is not False
            or marker.get("external_timestamp_attested") is not False):
        raise ValueError("Private snapshot ready marker does not match data")
    try:
        marker_at = datetime.fromisoformat(marker["published_at_utc"])
        if marker_at.tzinfo is None:
            raise ValueError("Missing UTC offset")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Invalid snapshot publication time") from exc


def _snapshot_directory(folder: Path) -> None:
    if folder.is_symlink() or not folder.is_dir() or folder.stat().st_mode & 0o077:
        raise ValueError("Unsafe or nonprivate snapshot folder")
    allowed = {QUOTES, PROVENANCE, READY}
    if {f.name for f in folder.iterdir()} != allowed:
        raise ValueError("Snapshot must contain exactly H1 CSV, provenance and ready marker")
    for name in allowed:
        if (folder / name).is_symlink():
            raise ValueError("Symlink snapshot component is forbidden")


def _hash_checked(path: Path, cap: int) -> str:
    _safe_file(path, cap)
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_validate(folder: Path, working: Path, *, allow_synthetic: bool,
                    now: datetime) -> dict:
    """Copy into a private directory before reading; never parse a mutable source."""
    _snapshot_directory(folder)
    _safe_file(folder / READY, MAX_READY_BYTES)
    marker = json.loads((folder / READY).read_text(encoding="utf-8"))
    quote_hash, qsize = _copy_checked(folder / QUOTES, working / QUOTES, MAX_QUOTES_BYTES)
    meta_hash, msize = _copy_checked(folder / PROVENANCE, working / PROVENANCE, MAX_PROVENANCE_BYTES)
    info, meta, audit = _validate_pair(working / QUOTES, working / PROVENANCE,
                                      allow_synthetic=allow_synthetic, observed_at=now)
    _verify_marker(marker, quote_hash, meta_hash, qsize, msize, info, meta)
    if folder.name != marker["snapshot_id"]:
        raise ValueError("Snapshot ID and directory name disagree")
    if audit["quotes_sha256"] != quote_hash:
        raise ValueError("Price digest differs from source validation")
    return marker


def publish(*, quotes_path: Path, provenance_path: Path, inbox: Path,
            allow_synthetic: bool = False, _test_asof: datetime | None = None) -> dict:
    """Complete, validate and atomically reveal a private H1 snapshot pair.

    Source data must already have been downloaded with authorized external tools.
    This function never contacts the price provider.
    """
    directory = _private_inbox(inbox)
    _mkdir_private(directory)
    lock = directory / ".publish_lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Another publisher holds the private inbox lock") from exc
    try:
        with tempfile.TemporaryDirectory(dir=directory, prefix=".pending-") as tmp:
            stage = Path(tmp)
            quote_hash, qsize = _copy_checked(Path(quotes_path), stage / QUOTES, MAX_QUOTES_BYTES)
            meta_hash, msize = _copy_checked(Path(provenance_path), stage / PROVENANCE, MAX_PROVENANCE_BYTES)
            _, source_meta, _ = load_quotes(stage / QUOTES, stage / PROVENANCE,
                                            allow_synthetic=allow_synthetic)
            now = _clock(_test_asof,
                         allow_synthetic=source_meta["source_kind"] == "SYNTHETIC_TEST_FIXTURE")
            info, meta, audit = _validate_pair(stage / QUOTES, stage / PROVENANCE,
                                              allow_synthetic=allow_synthetic, observed_at=now)
            if audit["quotes_sha256"] != quote_hash:
                raise ValueError("CSV and provenance hashes disagree")
            snapshot_id = _id(quote_hash, meta_hash)
            marker = {"schema": SCHEMA, "snapshot_id": snapshot_id,
                      "quotes_sha256": quote_hash, "provenance_sha256": meta_hash,
                      "quotes_bytes": qsize, "provenance_bytes": msize,
                      **info, "source_kind": meta["source_kind"],
                      "published_at_utc": now.isoformat(),
                      "approved_for_live_trading": False,
                      "external_timestamp_attested": False,
                      "broker_origin_independently_verified": False}
            _save_json(stage / READY, marker)
            destination = directory / snapshot_id
            processed = directory / "processed" / snapshot_id
            if destination.exists() or processed.exists():
                reference = destination if destination.exists() else processed
                with tempfile.TemporaryDirectory(dir=directory, prefix=".verify-") as check:
                    _stage_validate(reference, Path(check), allow_synthetic=allow_synthetic, now=now)
                return {"status": "ALREADY_PUBLISHED", "snapshot_id": snapshot_id,
                        "approved_for_live_trading": False, "broker_access": "DISABLED"}
            # Stage ready marker already fsynced. An atomic rename exposes all 3 files.
            os.replace(stage, destination)
            _fsync_directory(directory)
            return {"status": "READY_PRIVATE_SNAPSHOT", "snapshot_id": snapshot_id,
                    "rows": info["rows"], "last_bar_time_utc": info["last_bar_time_utc"],
                    "approved_for_live_trading": False, "broker_access": "DISABLED"}
    finally:
        lock.rmdir()


def _queue(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    available = []
    for entry in directory.iterdir():
        if entry.name in (".publish_lock", ".consume_lock", "processed") or entry.name.startswith(".pending-") or entry.name.startswith(".verify-"):
            continue
        if not HEX_ID.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            raise ValueError("Unexpected or unsafe file in the private inbox")
        available.append(entry)
    if len(available) > MAX_PENDING:
        raise ValueError("Inbox backlog exceeds safe bounded size")
    return available


def poll(*, inbox: Path, out: Path, allow_synthetic: bool = False,
         _test_asof: datetime | None = None) -> dict:
    """Consume oldest completed local snapshot, run existing research, export locally.

    At most one snapshot is processed per invocation. No automatic model init or
    retry after a supervisor STOP; the caller must arrange any scheduling.
    """
    directory = _private_inbox(inbox)
    monitor = _private_out(Path(out))
    if not directory.is_dir() or not (monitor / "monitor_state.json").is_file():
        raise ValueError("Private inbox and a previously frozen monitor are required")
    lock = directory / ".consume_lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Another consumer is active or left a stale lock") from exc
    try:
        snapshots = _queue(directory)
        if not snapshots:
            return {"status": "NO_READY_SNAPSHOT", "queue_depth": 0,
                    "market_data_freshness_confirmed": False,
                    "approved_for_live_trading": False, "broker_access": "DISABLED"}
        if _test_asof is not None and not allow_synthetic:
            raise ValueError("Clock override is permitted for synthetic tests only")
        now = _clock(_test_asof, allow_synthetic=allow_synthetic)
        # First inspect the signed-by-content ready marker (NOT a provider signature).
        # Choose oldest by last bar; this makes delayed snapshots ineligible as timely signals.
        candidates = []
        by_time = {}
        for folder in snapshots:
            _snapshot_directory(folder)
            _safe_file(folder / READY, MAX_READY_BYTES)
            r = json.loads((folder / READY).read_text(encoding="utf-8"))
            if (_test_asof is not None and r.get("source_kind") != "SYNTHETIC_TEST_FIXTURE"):
                raise ValueError("Clock override is permitted for synthetic tests only")
            if (r.get("snapshot_id") != folder.name or
                    not isinstance(r.get("last_bar_time_utc"), str) or
                    type(r.get("rows")) is not int or r["rows"] < 200 or
                    not HEX64.fullmatch(r.get("quotes_sha256", ""))):
                raise ValueError("Corrupted or incomplete ready marker")
            order = (r["last_bar_time_utc"], r["rows"])
            if order in by_time and by_time[order] != r["quotes_sha256"]:
                raise ValueError("Two incompatible snapshots claim the same H1 endpoint")
            by_time[order] = r["quotes_sha256"]
            candidates.append((order, folder))
        candidates.sort(key=lambda t: (t[0][0], t[0][1], t[1].name))
        chosen = candidates[0][1]
        with tempfile.TemporaryDirectory(dir=monitor, prefix=".inbox-verified-") as temp:
            stable = Path(temp)
            marker = _stage_validate(chosen, stable, allow_synthetic=allow_synthetic, now=now)
            result = run_once(quotes_path=stable / QUOTES, provenance_path=stable / PROVENANCE,
                              out=monitor, allow_synthetic=allow_synthetic,
                              _test_asof=_test_asof)
            if result["status"] in BLOCKED:
                return {"status": result["status"], "queue_depth": len(candidates),
                        "approved_for_live_trading": False, "broker_access": "DISABLED"}
            exported = write_export(monitor, monitor / "forward_status.json")
            # Revalidate original files before acknowledging/archiving this exact drop.
            # A failure after successful supervisor run forces manual review, not silent ack.
            if (_hash_checked(chosen / QUOTES, MAX_QUOTES_BYTES) != marker["quotes_sha256"]
                    or _hash_checked(chosen / PROVENANCE, MAX_PROVENANCE_BYTES) != marker["provenance_sha256"]):
                raise ValueError("Inbox snapshot changed during monitor processing")
            processed = directory / "processed"
            _mkdir_private(processed)
            target = processed / chosen.name
            if target.exists():
                raise ValueError("Already archived snapshot unexpectedly reappeared")
            os.replace(chosen, target)
            _fsync_directory(processed)
            _fsync_directory(directory)
            return {"status": "PRIVATE_SNAPSHOT_PROCESSED", "snapshot_id": marker["snapshot_id"],
                    "supervisor_status": result["status"], "monitor_status": exported["monitor_status"],
                    "queue_depth_remaining": len(candidates) - 1,
                    "market_data_freshness_confirmed": False,
                    "approved_for_live_trading": False, "broker_access": "DISABLED"}
    finally:
        lock.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline private H1 drop staging; NO API, orders or scheduler")
    commands = parser.add_subparsers(dest="cmd", required=True)
    send = commands.add_parser("publish", help="Stage local H1 snapshot pair atomically")
    send.add_argument("--quotes", type=Path, required=True)
    send.add_argument("--provenance", type=Path, required=True)
    for command in (send, commands.add_parser("poll", help="Consume one ready snapshot")):
        command.add_argument("--inbox", type=Path, default=Path("private_quotes/inbox"))
        command.add_argument("--allow-synthetic", action="store_true")
    commands.choices["poll"].add_argument("--out", type=Path, default=Path("private_reports/forward_monitor"))
    args = parser.parse_args()
    try:
        result = (publish(quotes_path=args.quotes, provenance_path=args.provenance,
                          inbox=args.inbox, allow_synthetic=args.allow_synthetic)
                  if args.cmd == "publish" else poll(inbox=args.inbox, out=args.out,
                                                     allow_synthetic=args.allow_synthetic))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2 if result["status"] in BLOCKED else 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # No secret, licensed quote contents, local paths or stack traces in stdout.
        print(json.dumps({"status": "BLOCKED_PRIVATE_INBOX_REVIEW_REQUIRED",
                          "error_type": type(exc).__name__,
                          "broker_access": "DISABLED", "approved_for_live_trading": False}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

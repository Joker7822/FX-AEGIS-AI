"""Offline, fail-closed security policy audit for the manually triggered OANDA workflow.

Only reads local workflow text. Does not import broker modules, load secrets,
probe network connectivity, or initiate any GitHub Actions dispatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github/workflows/private-broker-h1-research.yml"
EXPECTED_GUARD = (
    "if: ${{ inputs.operation == 'fetch-and-evaluate' && "
    "inputs.confirm_private_download == 'yes' && "
    "vars.OANDA_RESEARCH_ENABLED == 'true' && "
    "vars.OANDA_RESEARCH_LICENSE_OK == 'true' }}"
)
EXPECTED_PRECHECKS = {
    'if [[ "$OPT_IN" == \'true\' && "$LICENSE_OK" == \'true\' ]]; then',
    'if [[ "$OPERATION" == \'fetch-and-evaluate\' && "$CONFIRM_DOWNLOAD" == \'yes\' ]]; then',
}


def audit_text(source: str) -> dict:
    """Conservative textual check; not a full YAML interpreter or security proof."""
    checks: dict[str, bool] = {}

    def check(name: str, ok: bool) -> None:
        checks[name] = bool(ok)

    events_match = re.search(r"(?ms)^on:\s*\n(.*?)(?=^[A-Za-z][\w-]*:|\Z)", source)
    events = re.findall(r"(?m)^  ([a-z_][a-z0-9_-]*):", events_match.group(1)) if events_match else []
    check("manual_trigger_only", events == ["workflow_dispatch"])
    check("safe_defaults", bool(re.search(r"(?m)^\s+default: 'preflight-only'\s*$", source))
          and bool(re.search(r"(?m)^\s+default: 'no'\s*$", source)))
    check("read_only_permissions", bool(re.search(r"(?m)^permissions:\s*\n  contents: read\s*\n", source))
          and "contents: write" not in source and "id-token: write" not in source)

    jobs = source.split("\njobs:\n", 1)
    body = jobs[1] if len(jobs) == 2 else ""
    job_names = re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\s*$", body)
    check("exact_jobs", job_names == ["readiness", "private-reference-research"])

    part = body.split("  private-reference-research:\n", 1)
    readiness = part[0] if len(part) == 2 else ""
    private = part[1] if len(part) == 2 else ""
    check("preflight_does_not_read_secrets", bool(readiness)
          and "secrets." not in readiness and "OANDA_API_TOKEN" not in readiness
          and "- uses:" not in readiness)

    preflight_body = readiness.split("        run: |\n", 1)
    run_lines = [line.strip() for line in preflight_body[1].splitlines()] if len(preflight_body) == 2 else []
    expected_commands = all(
        not line or re.fullmatch(r"echo '[^']*'", line)
        or line in EXPECTED_PRECHECKS or line in ("else", "fi")
        for line in run_lines
    )
    check("preflight_echo_only", bool(run_lines) and expected_commands
          and EXPECTED_PRECHECKS.issubset(set(run_lines)))

    guards = re.findall(r"(?m)^    if: ([^\n]+)$", private)
    check("strict_fetch_guard", len(guards) == 1 and "if: " + guards[0].strip() == EXPECTED_GUARD)
    check("requires_readiness", "    needs: readiness\n" in private)
    check("token_and_license_gated", "secrets.OANDA_API_TOKEN" in private
          and "vars.OANDA_RESEARCH_LICENSE_OK" in private
          and 'if [[ -z "${OANDA_API_TOKEN:-}"' in private
          and "exit 1\n" in private)
    check("cli_explicit_enable", "python broker_h1_oanda.py --enable-api" in private)
    check("private_cleanup_no_public_artifacts", "rm -rf" in private
          and "actions/upload-artifact" not in source
          and "git push" not in source
          and "actions/deploy-pages" not in source)

    failed = [name for name, ok in checks.items() if not ok]
    return {
        "schema": "fx-aegis-oanda-offline-policy-1",
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "failed": failed,
        "workflow_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "actual_oanda_api_access": False,
        "api_token_inspected": False,
        "note": "Local static audit only; GitHub variables, secrets and API connectivity not verified.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed offline audit; no broker network access")
    parser.add_argument("--workflow", type=Path, default=WORKFLOW)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    try:
        content = args.workflow.read_text(encoding="utf-8")
        report = audit_text(content)
    except (OSError, UnicodeError) as exc:
        report = {"schema": "fx-aegis-oanda-offline-policy-1", "status": "FAIL",
                  "failed": ["workflow_unreadable"], "error": type(exc).__name__,
                  "actual_oanda_api_access": False, "api_token_inspected": False}
    report_json = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report_json, encoding="utf-8")
    print(report_json, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

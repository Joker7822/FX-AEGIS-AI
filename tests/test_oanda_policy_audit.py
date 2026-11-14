"""Regression tests for fail-closed OANDA workflow static policy checks."""
import unittest
from oanda_policy_audit import WORKFLOW, audit_text


class OandaOfflinePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_checked_in_workflow_passes(self):
        self.assertEqual(audit_text(self.source)["status"], "PASS")

    def test_alter_guard_fails(self):
        altered = self.source.replace("inputs.confirm_private_download == 'yes'", "true")
        self.assertIn("strict_fetch_guard", audit_text(altered)["failed"])

    def test_schedule_fails(self):
        altered = self.source.replace("  workflow_dispatch:\n",
                                      "  workflow_dispatch:\n  schedule:\n    - cron: '0 * * * *'\n")
        self.assertIn("manual_trigger_only", audit_text(altered)["failed"])

    def test_secret_in_readiness_fails(self):
        altered = self.source.replace("  readiness:\n",
                                      "  readiness:\n    env:\n      TOKEN: ${{{ secrets.OANDA_API_TOKEN }}\n")
        self.assertIn("preflight_does_not_read_secrets", audit_text(altered)["failed"])

    def test_preflight_unsafe_shell_fails(self):
        altered = self.source.replace("          echo 'The API token is intentionally not checked in preflight-only mode.'",
                                      "          curl https://api-fxpractice.oanda.com/")
        self.assertIn("preflight_echo_only", audit_text(altered)["failed"])

    def test_default_fetch_fails(self):
        altered = self.source.replace("default: 'preflight-only'", "default: 'fetch-and-evaluate'")
        self.assertIn("safe_defaults", audit_text(altered)["failed"])

    def test_upload_artifact_fails(self):
        altered = self.source + "\n# actions/upload-artifact\n"
        self.assertIn("private_cleanup_no_public_artifacts", audit_text(altered)["failed"])

    def test_report_never_claims_api_check(self):
        r = audit_text(self.source)
        self.assertFalse(r["actual_oanda_api_access"])
        self.assertFalse(r["api_token_inspected"])
        self.assertEqual(len(r["workflow_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

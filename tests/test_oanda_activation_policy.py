"""OANDA activation is a deliberate manual-only action with a no-network default."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/private-broker-h1-research.yml"


class ActivationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_only_manual_trigger(self):
        s = self.text
        self.assertIn("workflow_dispatch:", s)
        self.assertNotIn("  schedule:", s)
        self.assertNotIn("  push:", s)
        self.assertNotIn("  pull_request:", s)

    def test_preflight_default_cannot_download(self):
        s = self.text
        self.assertIn("default: 'preflight-only'", s)
        self.assertIn("default: 'no'", s)
        self.assertIn("  readiness:\n", s)
        self.assertIn("no API access; no broker quotes", s)

    def test_research_requires_explicit_input_and_both_vars(self):
        s = self.text
        guard = next(x.strip() for x in s.splitlines() if x.lstrip().startswith("if: ${{ inputs.operation"))
        for required in ("inputs.operation == 'fetch-and-evaluate'", "inputs.confirm_private_download == 'yes'",
                         "vars.OANDA_RESEARCH_ENABLED == 'true'", "vars.OANDA_RESEARCH_LICENSE_OK == 'true'"):
            self.assertIn(required, guard)
        self.assertNotIn("false &&", guard)

    def test_missing_token_fails_before_download(self):
        s = self.text
        self.assertIn('if [[ -z "${OANDA_API_TOKEN:-}"', s)
        self.assertIn("exit 1", s)
        self.assertIn("--enable-api", s)

    def test_no_public_broker_artifact(self):
        s = self.text
        self.assertNotIn("actions/upload-artifact", s)
        self.assertNotIn("git push", s)
        self.assertIn("rm -rf", s)


if __name__ == '__main__':
    unittest.main()

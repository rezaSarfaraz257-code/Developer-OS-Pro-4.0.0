from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ProductionOperationalContractTests(unittest.TestCase):
    """Regression guards for the production/operational boundary only."""

    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8", errors="replace")

    def test_production_black_box_is_fail_closed(self):
        workflow = self.read(".github/workflows/production-e2e.yml")
        for marker in (
            "environment:",
            "name: production",
            "PRODUCTION_BASE_URL",
            "PRODUCTION_FRONTEND_URL",
            "PRODUCTION_BILLING_WEBHOOK_SECRET",
            "test -n \"$PRODUCTION_BASE_URL\"",
            "test -n \"$PRODUCTION_FRONTEND_URL\"",
            "test -n \"$PRODUCTION_BILLING_WEBHOOK_SECRET\"",
            "node scripts/production_validate.mjs",
        ):
            self.assertIn(marker, workflow, marker)

    def test_production_validator_covers_critical_customer_path(self):
        validator = self.read("scripts/production_validate.mjs")
        for marker in (
            "PRODUCTION_BASE_URL must use HTTPS",
            "Health failed",
            "Readiness failed",
            "securityHeaders",
            'request(\"/pricing/\")',
            'request(\"/ide/capabilities/\"',
            'request(`/ide/workspaces/${workspace.id}/execute/`',
            'customer.subscription.updated',
            "Invalid billing signature was not rejected",
            "Billing webhook idempotency failed",
            'usage.plan === \"pro\"',
        ):
            self.assertIn(marker, validator, marker)

    def test_ci_keeps_operational_and_release_gates_blocking(self):
        ci = self.read(".github/workflows/ci.yml")
        self.assertIn("python scripts/operational_policy_gate.py", ci)
        self.assertIn("python scripts/release_gate.py", ci)
        self.assertIn("sha256sum -c", ci)

    def test_reliability_contract_and_operations_runbook_exist(self):
        slo = self.read("docs/RELIABILITY_SLO.md")
        operations = self.read("docs/OPERATIONS.md")
        for marker in (
            "Service-level objectives",
            "Error-budget policy",
            "Recovery point objective",
            "Recovery time objective",
        ):
            self.assertIn(marker, slo, marker)
        for marker in ("release", "backup", "rollback", "runner"):
            self.assertIn(marker.lower(), operations.lower(), marker)


if __name__ == "__main__":
    unittest.main()

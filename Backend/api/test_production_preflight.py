"""Regression tests for the fail-fast production configuration gate."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "scripts" / "production_preflight.py"


class ProductionPreflightEmailTests(unittest.TestCase):
    def _environment(self):
        env = os.environ.copy()
        env.update({
            "DJANGO_SECRET_KEY": "preflight-test-secret",
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "ALLOWED_HOSTS": "example.test",
            "CORS_ALLOWED_ORIGINS": "https://example.test",
            "CSRF_TRUSTED_ORIGINS": "https://example.test",
            "IDE_RUNNER_TOKEN": "preflight-test-runner-token",
            "FRONTEND_URL": "https://example.test",
            "DEFAULT_FROM_EMAIL": "no-reply@example.test",
        })
        for key in (
            "EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_BACKEND",
            "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_PRO",
            "STRIPE_PRICE_TEAM", "STRIPE_PRICE_ENTERPRISE",
        ):
            env.pop(key, None)
        env.pop("REQUIRE_STRIPE_BILLING", None)
        return env

    def _run(self, env):
        return subprocess.run(
            [sys.executable, str(PREFLIGHT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_required_email_verification_fails_without_smtp(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "true"
        result = self._run(env)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("MISSING REQUIRED EMAIL DELIVERY", result.stdout)

    def test_disabled_email_verification_can_use_no_smtp(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "false"
        result = self._run(env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PRODUCTION PREFLIGHT: PASS", result.stdout)

    def test_required_stripe_billing_fails_when_paid_prices_are_missing(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "false"
        env["REQUIRE_STRIPE_BILLING"] = "true"
        result = self._run(env)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("STRIPE_PRICE_PRO", result.stdout)
        self.assertIn("STRIPE_PRICE_TEAM", result.stdout)
        self.assertIn("STRIPE_PRICE_ENTERPRISE", result.stdout)

    def test_partial_stripe_configuration_fails_closed(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "false"
        env["STRIPE_SECRET_KEY"] = "sk_test_example"
        env["STRIPE_WEBHOOK_SECRET"] = "whsec_example"
        env["STRIPE_PRICE_PRO"] = "price_pro_example"
        env["STRIPE_PRICE_TEAM"] = "price_team_example"
        result = self._run(env)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("STRIPE_PRICE_ENTERPRISE", result.stdout)

    def test_complete_stripe_configuration_requires_realistic_identifier_prefixes(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "false"
        env.update({
            "STRIPE_SECRET_KEY": "sk_test_example",
            "STRIPE_WEBHOOK_SECRET": "whsec_example",
            "STRIPE_PRICE_PRO": "price_pro_example",
            "STRIPE_PRICE_TEAM": "price_team_example",
            "STRIPE_PRICE_ENTERPRISE": "price_enterprise_example",
        })
        result = self._run(env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_stripe_price_identifier_fails_preflight(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "false"
        env.update({
            "STRIPE_SECRET_KEY": "sk_test_example",
            "STRIPE_WEBHOOK_SECRET": "whsec_example",
            "STRIPE_PRICE_PRO": "pro",
            "STRIPE_PRICE_TEAM": "price_team_example",
            "STRIPE_PRICE_ENTERPRISE": "price_enterprise_example",
        })
        result = self._run(env)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("INVALID STRIPE CONFIG", result.stdout)


if __name__ == "__main__":
    unittest.main()

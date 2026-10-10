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
        for key in ("EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_BACKEND"):
            env.pop(key, None)
        return env

    def test_required_email_verification_fails_without_smtp(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "true"
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("MISSING REQUIRED EMAIL DELIVERY", result.stdout)

    def test_disabled_email_verification_can_use_no_smtp(self):
        env = self._environment()
        env["EMAIL_VERIFICATION_REQUIRED"] = "false"
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PRODUCTION PREFLIGHT: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()

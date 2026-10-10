"""Regression tests for fail-closed runner capability reporting."""
import unittest
from unittest.mock import patch

import main


class RunnerCapabilityTests(unittest.TestCase):
    def test_strict_shared_container_does_not_advertise_execution(self):
        with patch.object(main, "SANDBOX_MODE", "container"), patch.object(
            main, "RUNNER_SECURITY_LEVEL", "strict"
        ):
            capabilities = main._capability_manifest()

        self.assertFalse(capabilities["operations"]["execute"])
        self.assertIn("per-execution isolation", capabilities["sandbox"]["unavailable_reason"])

    def test_bubblewrap_namespace_failure_has_actionable_reason(self):
        with patch.object(main, "SANDBOX_MODE", "bwrap"), patch.object(
            main, "RUNNER_SECURITY_LEVEL", "strict"
        ), patch.object(main, "_bwrap_runtime_available", return_value=False), patch.object(
            main.shutil, "which", return_value="/usr/bin/bwrap"
        ):
            capabilities = main._capability_manifest()

        self.assertFalse(capabilities["operations"]["execute"])
        self.assertIn("Linux namespaces", capabilities["sandbox"]["unavailable_reason"])

    def test_compatibility_mode_is_explicitly_marked_as_shared(self):
        with patch.object(main, "SANDBOX_MODE", "container"), patch.object(
            main, "RUNNER_SECURITY_LEVEL", "compat"
        ):
            capabilities = main._capability_manifest()

        self.assertTrue(capabilities["operations"]["execute"])
        self.assertTrue(capabilities["sandbox"]["trusted_shared_container"])
        self.assertIsNone(capabilities["sandbox"]["unavailable_reason"])


if ( __name__ == "__main__"):
    unittest.main()

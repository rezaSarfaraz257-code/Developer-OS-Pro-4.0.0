from django.test import SimpleTestCase

from .agent_execution import _safe_diagnostic_signals


class SafeAgentDiagnosticBoundaryTests(SimpleTestCase):
    def test_malformed_output_is_reduced_to_generic_signal(self):
        for stdout, stderr in ((None, ""), ("", {"secret": "value"}), ([], [])):
            with self.subTest(stdout_type=type(stdout).__name__, stderr_type=type(stderr).__name__):
                self.assertEqual(
                    _safe_diagnostic_signals("python_pytest", stdout, stderr, 1),
                    [{"code": "command_failed", "source": "exit_status"}],
                )

    def test_unknown_profile_does_not_expose_output(self):
        signals = _safe_diagnostic_signals(
            "unrecognized_profile",
            "PRIVATE_SOURCE_CODE",
            "TOKEN=do-not-store",
            1,
        )
        self.assertEqual(signals, [{"code": "command_failed", "source": "exit_status"}])
        self.assertNotIn("PRIVATE_SOURCE_CODE", str(signals))
        self.assertNotIn("do-not-store", str(signals))

    def test_non_failure_and_invalid_exit_code_remain_empty(self):
        self.assertEqual(_safe_diagnostic_signals("python_pytest", "", "", 0), [])
        self.assertEqual(_safe_diagnostic_signals("python_pytest", "", "", True), [])

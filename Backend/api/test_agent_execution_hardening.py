from types import SimpleNamespace

from django.utils import timezone

from django.test import SimpleTestCase

from .agent_execution import _job_lease_is_current, _safe_diagnostic_signals


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



class AgentWorkerLeaseFencingTests(SimpleTestCase):
    def test_only_exact_current_worker_lease_is_accepted(self):
        claimed = SimpleNamespace(locked_by="worker-a:token-123")
        current = SimpleNamespace(status="running", locked_by="worker-a:token-123", locked_at=timezone.now())
        self.assertTrue(_job_lease_is_current(current, claimed))

    def test_reclaimed_job_rejects_previous_worker_token(self):
        claimed = SimpleNamespace(locked_by="worker-a:old-token")
        current = SimpleNamespace(status="running", locked_by="worker-a:new-token")
        self.assertFalse(_job_lease_is_current(current, claimed))

    def test_non_running_or_empty_lease_is_never_current(self):
        claimed = SimpleNamespace(locked_by="worker-a:token")
        for status, token in (("queued", "worker-a:token"), ("failed", "worker-a:token"), ("running", "")):
            with self.subTest(status=status, token=token):
                current = SimpleNamespace(status=status, locked_by=token)
                self.assertFalse(_job_lease_is_current(current, claimed))

    def test_missing_job_or_claim_is_never_current(self):
        claimed = SimpleNamespace(locked_by="worker-a:token")
        current = SimpleNamespace(status="running", locked_by="worker-a:token")
        self.assertFalse(_job_lease_is_current(None, claimed))
        self.assertFalse(_job_lease_is_current(current, None))

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .agent_execution import (
    VERIFICATION_PROFILES, execute_agent_task_verification,
    fail_agent_task_verification,
)
from .management.commands.worker import recover_stale_jobs
from .models import AgentRun, AgentTask, AuditLog, BackgroundJob, CodeWorkspace, Project


class AgentTaskVerificationAPITests(TestCase):
    def setUp(self):
        # This API test targets task authorization and execution, not the
        # shared request-rate-limit cache. Keep middleware behavior deterministic.
        rate_limit_patcher = patch("api.security.consume_rate_limit", return_value=(True, None))
        rate_limit_patcher.start()
        self.addCleanup(rate_limit_patcher.stop)
        self.user = User.objects.create_user(username="agent-execution-owner", password="StrongPassword123!")
        self.other = User.objects.create_user(username="agent-execution-other", password="StrongPassword123!")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user, name="Execution workspace", files={"main.py": "print('ok')"},
        )
        self.run = AgentRun.objects.create(
            owner=self.user, workspace=self.workspace, goal="Implement and verify a feature",
            status="approved", approved_at=timezone.now(),
            plan={"acceptance_criteria": ["Feature behavior is correct", "Existing behavior remains compatible"]},
            idempotency_key="agent-execution-run-001", workspace_revision=self.workspace.revision,
        )
        self.task = AgentTask.objects.create(
            run=self.run, key="backend", title="Backend implementation",
            status="ready", agent_role="backend",
            acceptance_criteria=["Feature behavior is correct", "Existing behavior remains compatible"],
        )
        self.workspace.files = {"main.py": "print('updated')"}
        self.workspace.save(update_fields={"files"})
        self.sync_job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync", status="succeeded",
            payload={"workspace_id": self.workspace.id, "owner_id": self.user.id,
                     "requested_revision": self.workspace.revision},
            result={"workspace_id": self.workspace.id, "revision": self.workspace.revision, "status": "synced"},
            idempotency_key=f"sync:{self.workspace.id}:{self.workspace.revision}",
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
            target_id=str(self.workspace.id), metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
            },
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(self.workspace.id),
            metadata={"job_id": self.sync_job.id, "revision": self.workspace.revision},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/verify/"

    def test_only_allowlisted_profiles_are_accepted(self):
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest; touch /tmp/not-allowed",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BackgroundJob.objects.filter(kind="ai_agent_task_verify").exists())

    def test_dag_dependencies_are_enforced_even_if_task_status_is_stale(self):
        dependency = AgentTask.objects.create(
            run=self.run, key="prerequisite", title="Prerequisite", status="ready",
        )
        self.task.depends_on.add(dependency)

        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(queued.status_code, 409)
        self.assertFalse(BackgroundJob.objects.filter(kind="ai_agent_task_verify").exists())

        verification_job = BackgroundJob.objects.create(
            kind="ai_agent_task_verify", status="succeeded",
            payload={"run_id": self.run.id, "task_id": self.task.id, "revision": self.workspace.revision},
            result={"status": "verification_passed_pending_acceptance"},
            idempotency_key="agent-task-dag-guard-001",
        )
        self.task.status = "awaiting_approval"
        self.task.execution_job = verification_job
        self.task.execution_revision = self.workspace.revision
        self.task.execution_evidence = {"state": "passed", "revision": self.workspace.revision}
        self.task.save(update_fields=[
            "status", "execution_job", "execution_revision", "execution_evidence", "updated_at",
        ])
        accepted = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )
        self.assertEqual(accepted.status_code, 409)

    @patch("api.views._runner_request")
    def test_worker_rechecks_signed_patch_approval_after_job_was_queued(self, runner_request):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(queued.status_code, 202)
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        # Simulate the approval being revoked while the durable job is queued.
        AuditLog.objects.filter(
            user=self.user, action="ai_agent.patch_applied",
            target_type="CodeWorkspace", target_id=str(self.workspace.id),
            metadata__revision=self.workspace.revision,
        ).delete()
        # A replacement audit with identical metadata must not inherit the
        # queued job's authorization; it was not the record bound at queue time.
        AuditLog.objects.create(
            user=self.user, action="ai_agent.patch_applied",
            target_type="CodeWorkspace", target_id=str(self.workspace.id),
            metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
            },
        )

        with self.assertRaisesRegex(RuntimeError, "signed approval.*no longer valid"):
            execute_agent_task_verification(job)
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    def test_worker_rechecks_dependencies_after_job_was_queued(self, runner_request):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(queued.status_code, 202)
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        # Simulate a prerequisite becoming incomplete while the verification
        # job is waiting in the durable worker queue.
        dependency = AgentTask.objects.create(
            run=self.run, key="late-prerequisite", title="Late prerequisite",
            status="failed",
        )
        self.task.depends_on.add(dependency)

        with self.assertRaisesRegex(RuntimeError, "dependencies are no longer satisfied"):
            execute_agent_task_verification(job)

        runner_request.assert_not_called()

    def test_requires_human_patch_approval_audit(self):
        AuditLog.objects.filter(action="ai_agent.patch_applied").delete()
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_rejects_patch_approval_bound_to_a_different_task(self):
        AuditLog.objects.filter(action="ai_agent.patch_applied").update(
            metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id + 999,
            },
        )
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(BackgroundJob.objects.filter(kind="ai_agent_task_verify").exists())



    def test_rejects_patch_audit_without_signed_revision_chain(self):
        AuditLog.objects.filter(action="ai_agent.patch_applied").update(
            metadata={"revision": self.workspace.revision},
        )
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(BackgroundJob.objects.filter(kind="ai_agent_task_verify").exists())

    def test_verification_cannot_start_without_task_specific_criteria(self):
        self.task.acceptance_criteria = []
        self.task.save(update_fields=["acceptance_criteria"])
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(BackgroundJob.objects.filter(kind="ai_agent_task_verify").exists())

    def test_requires_exact_successfully_synced_revision(self):
        self.sync_job.result = {"revision": self.workspace.revision - 1}
        self.sync_job.save(update_fields=["result"])
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 409)

    def test_other_user_cannot_queue_or_read_verification(self):
        self.client.force_authenticate(self.other)
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 404)
        status = self.client.get(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/verification/",
        )
        self.assertEqual(status.status_code, 404)

    def test_queue_is_idempotent_and_never_replays_runner_post(self):
        payload = {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.data["status"], "running")
        job = BackgroundJob.objects.get(kind="ai_agent_task_verify")
        self.assertEqual(job.max_attempts, 1)
        self.assertEqual(job.payload["profile"], "python_pytest")
        self.assertIsNotNone(job.payload["patch_audit_id"])
        self.assertEqual(
            AuditLog.objects.get(pk=job.payload["patch_audit_id"]).action,
            "ai_agent.patch_applied",
        )
        second = self.client.post(self.url, payload, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["job_id"], job.id)
        self.assertEqual(BackgroundJob.objects.filter(kind="ai_agent_task_verify").count(), 1)

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-test-001", "status": "completed", "exit_code": 0, "duration_ms": 31, "stdout": "private output", "stderr": ""}, None,
    ))
    def test_runner_success_waits_for_human_acceptance_before_unblocking(self, runner_request):
        dependent = AgentTask.objects.create(
            run=self.run, key="review", title="Review", status="blocked",
        )
        dependent.depends_on.add(self.task)
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=response.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])
        result = execute_agent_task_verification(job)
        self.assertEqual(result["status"], "verification_passed_pending_acceptance")
        runner_request.assert_called_once()
        runner_payload = runner_request.call_args.args[2]
        self.assertIs(runner_payload["include_files"], False)
        self.assertEqual(runner_payload["execution_id"], f"agent-{job.id}")
        job.refresh_from_db()
        self.assertEqual(job.payload["runner_execution_id"], f"agent-{job.id}")
        self.task.refresh_from_db()
        dependent.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "awaiting_approval")
        self.assertEqual(dependent.status, "blocked")
        self.assertEqual(self.run.status, "running")
        job.status = "succeeded"
        job.result = result
        job.save(update_fields=["status", "result"])

        # Acceptance must bind the task evidence to the exact durable worker
        # result and must revalidate both the sync and signed patch audit.
        self.task.refresh_from_db()
        valid_evidence = dict(self.task.execution_evidence)
        tampered_evidence = dict(valid_evidence)
        tampered_evidence["patch_audit_id"] = valid_evidence["patch_audit_id"] + 1
        self.task.execution_evidence = tampered_evidence
        self.task.save(update_fields=["execution_evidence", "updated_at"])
        rejected = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )
        self.assertEqual(rejected.status_code, 409)
        self.task.execution_evidence = valid_evidence
        self.task.save(update_fields=["execution_evidence", "updated_at"])

        patch_audit = AuditLog.objects.get(pk=job.payload["patch_audit_id"])
        original_patch_metadata = dict(patch_audit.metadata)
        patch_audit.metadata = {**original_patch_metadata, "agent_task_id": self.task.id + 999}
        patch_audit.save(update_fields=["metadata"])
        rejected = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )
        self.assertEqual(rejected.status_code, 409)
        patch_audit.metadata = original_patch_metadata
        patch_audit.save(update_fields=["metadata"])

        sync_audit = AuditLog.objects.get(
            user=self.user,
            action="ai_agent.runner_workspace_synced",
            metadata__job_id=self.sync_job.id,
        )
        original_sync_metadata = dict(sync_audit.metadata)
        sync_audit.metadata = {**original_sync_metadata, "revision": self.workspace.revision - 1}
        sync_audit.save(update_fields=["metadata"])
        rejected = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )
        self.assertEqual(rejected.status_code, 409)
        sync_audit.metadata = original_sync_metadata
        sync_audit.save(update_fields=["metadata"])

        accepted = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200)
        self.task.refresh_from_db()
        dependent.refresh_from_db()
        self.assertEqual(self.task.status, "succeeded")
        self.assertEqual(dependent.status, "ready")
        self.assertEqual(self.task.execution_evidence["human_acceptance"]["criteria_scope"], "agent_task")
        self.assertEqual(self.task.execution_evidence["exit_code"], 0)
        self.assertEqual(self.task.execution_evidence["runner_job_id"], "runner-test-001")
        self.assertEqual(self.task.execution_evidence["verification_job_id"], job.id)
        self.assertEqual(self.task.execution_evidence["sync_job_id"], self.sync_job.id)
        self.assertEqual(
            self.task.execution_evidence["patch_audit_id"],
            job.payload["patch_audit_id"],
        )
        self.assertEqual(
            self.task.execution_evidence["stdout_sha256"],
            "13adcc097b3297fc79ab2dbc73cd64852c39c5e3c209ae67e72b01af8d2d6288",
        )
        self.assertEqual(
            self.task.execution_evidence["stderr_sha256"],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(self.task.execution_evidence["stdout_bytes"], len("private output".encode("utf-8")) )
        self.assertEqual(self.task.execution_evidence["stderr_bytes"], 0)
        self.assertFalse(self.task.execution_evidence["output_persisted"])
        self.assertNotIn("stdout", self.task.execution_evidence)
        runner_request.assert_called_once()
        self.assertEqual(
            runner_request.call_args.args[2]["command"],
            VERIFICATION_PROFILES["python_pytest"],
        )
        self.assertEqual(VERIFICATION_PROFILES["node_test"], "npm test")

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-oversized-001", "status": "completed", "exit_code": 0,
         "duration_ms": 1, "stdout": "x" * 30_000, "stderr": "y" * 30_000}, None,
    ))
    def test_oversized_runner_output_fails_closed_and_reconciles(self, runner_request):
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=response.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        with self.assertRaisesRegex(RuntimeError, "output exceeded the control-plane limit"):
            execute_agent_task_verification(job)

        fail_agent_task_verification(job)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "infrastructure_failed")
        runner_request.assert_called_once()

    def test_expired_verification_lease_is_failed_and_workspace_resynced(self):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(queued.status_code, 202)
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.locked_at = timezone.now() - timedelta(minutes=11)
        job.locked_by = "worker-that-crashed"
        job.save(update_fields=["status", "attempts", "locked_at", "locked_by"])

        recover_stale_jobs()

        job.refresh_from_db()
        self.task.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "infrastructure_failed")
        self.assertIsNotNone(self.task.execution_evidence["resync_job_id"])
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(
            BackgroundJob.objects.get(pk=self.task.execution_evidence["resync_job_id"]).kind,
            "ai_agent_runner_sync",
        )

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-\u00e9", "status": "completed", "exit_code": 0,
         "duration_ms": 1, "stdout": "", "stderr": ""}, None,
    ))
    def test_runner_rejects_non_ascii_job_identifiers(self, runner_request):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        with self.assertRaisesRegex(RuntimeError, "invalid execution job reference"):
            execute_agent_task_verification(job)

        runner_request.assert_called_once()

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-invalid-status", "status": {"unexpected": "object"},
         "exit_code": 0, "duration_ms": 1, "stdout": "", "stderr": ""}, None,
    ))
    def test_runner_rejects_non_string_execution_status(self, runner_request):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        with self.assertRaisesRegex(RuntimeError, "invalid execution status"):
            execute_agent_task_verification(job)

        runner_request.assert_called_once()

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-malformed-001", "status": "completed", "exit_code": 0,
         "duration_ms": 1, "stdout": None, "stderr": ""}, None,
    ))
    def test_malformed_runner_output_fails_closed_and_reconciles(self, runner_request):
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=response.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        with self.assertRaisesRegex(RuntimeError, "malformed execution output"):
            execute_agent_task_verification(job)

        fail_agent_task_verification(job)
        self.task.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "infrastructure_failed")
        self.assertIsNotNone(self.task.execution_evidence["resync_job_id"])
        self.assertEqual(self.run.status, "failed")
        runner_request.assert_called_once()

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-test-failed-001", "status": "failed", "exit_code": 1, "duration_ms": 10, "stdout": "", "stderr": "failure"}, None,
    ))
    def test_failed_runner_verification_is_terminal_evidence_not_success(self, runner_request):
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=response.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])
        result = execute_agent_task_verification(job)
        self.assertEqual(result["status"], "verification_failed")
        self.task.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(self.task.execution_evidence["exit_code"], 1)
        self.assertEqual(self.task.execution_evidence["state"], "failed")
        self.assertEqual(self.task.execution_evidence["iteration"], 1)


class AgentTaskAcceptanceTests(AgentTaskVerificationAPITests):
    def _mark_task_passed_with_durable_job(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_task_verify", status="succeeded",
            payload={
                "run_id": self.run.id, "task_id": self.task.id,
                "revision": self.workspace.revision,
            },
            result={"status": "verification_passed_pending_acceptance"},
            idempotency_key=f"accepted-test:{self.run.id}:{self.task.id}",
        )
        self.task.status = "awaiting_approval"
        self.task.execution_job = job
        self.task.execution_revision = self.workspace.revision
        self.task.execution_evidence = {"state": "passed", "revision": self.workspace.revision}
        self.task.save(update_fields=["status", "execution_job", "execution_revision", "execution_evidence"])

    def test_acceptance_cannot_resurrect_canceled_run(self):
        self._mark_task_passed_with_durable_job()
        self.run.status = "canceled"
        self.run.save(update_fields=["status"])

        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("canceled", response.data["error"])
        self.run.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.run.status, "canceled")
        self.assertEqual(self.task.status, "awaiting_approval")
        self.assertNotIn("human_acceptance", self.task.execution_evidence)

    def test_acceptance_cannot_resurrect_canceled_task(self):
        self._mark_task_passed_with_durable_job()
        self.task.status = "canceled"
        self.task.save(update_fields=["status"])

        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.run.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.run.status, "approved")
        self.assertEqual(self.task.status, "canceled")
        self.assertNotIn("human_acceptance", self.task.execution_evidence)

    def test_acceptance_requires_all_exact_task_criteria(self):
        self._mark_task_passed_with_durable_job()
        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": ["Feature behavior is correct"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "awaiting_approval")

    def test_acceptance_rejected_if_workspace_changes_after_verification(self):
        self._mark_task_passed_with_durable_job()
        self.workspace.files = {"main.py": "print('later change')"}
        self.workspace.save(update_fields={"files"})
        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.task.acceptance_criteria},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "awaiting_approval")


class AgentTaskRetryTests(AgentTaskVerificationAPITests):
    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-retry-failed-001", "status": "failed", "exit_code": 1, "duration_ms": 12, "stdout": "", "stderr": "failure"}, None,
    ))
    def test_failed_task_can_retry_only_after_new_approved_synced_revision(self, runner_request):
        first = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        first_job = BackgroundJob.objects.get(pk=first.data["job_id"])
        first_job.status = "running"
        first_job.attempts = 1
        first_job.save(update_fields=["status", "attempts"])
        execute_agent_task_verification(first_job)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "failed")

        same_revision = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(same_revision.status_code, 409)

        self.workspace.files = {"main.py": "print('retry')"}
        self.workspace.save(update_fields={"files"})
        new_sync = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync", status="succeeded",
            payload={"workspace_id": self.workspace.id, "owner_id": self.user.id,
                     "requested_revision": self.workspace.revision},
            result={"workspace_id": self.workspace.id, "revision": self.workspace.revision, "status": "synced"},
            idempotency_key=f"sync:{self.workspace.id}:{self.workspace.revision}",
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(self.workspace.id),
            metadata={"job_id": new_sync.id, "revision": self.workspace.revision},
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
            target_id=str(self.workspace.id), metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
            },
        )
        retry = self.client.post(self.url, {
            "sync_job_id": new_sync.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(retry.data["evidence"]["iteration"], 2)
        self.assertEqual(len(retry.data["evidence"]["history"]), 1)


class AgentTaskRetryLimitTests(AgentTaskVerificationAPITests):
    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-limit-failed-001", "status": "failed", "exit_code": 1, "duration_ms": 8, "stdout": "", "stderr": "failure"}, None,
    ))
    def test_retry_loop_stops_after_four_total_iterations(self, runner_request):
        sync_job = self.sync_job
        for iteration in range(4):
            response = self.client.post(self.url, {
                "sync_job_id": sync_job.id, "revision": self.workspace.revision,
                "profile": "python_pytest",
            }, format="json")
            self.assertEqual(response.status_code, 202)
            job = BackgroundJob.objects.get(pk=response.data["job_id"])
            job.status = "running"
            job.attempts = 1
            job.save(update_fields=["status", "attempts"])
            execute_agent_task_verification(job)
            self.task.refresh_from_db()
            self.assertEqual(self.task.status, "failed")
            self.assertEqual(self.task.execution_evidence["iteration"], iteration + 1)
            if iteration < 3:
                self.workspace.files = {"main.py": f"print('retry {iteration + 1}')"}
                self.workspace.save(update_fields={"files"})
                sync_job = BackgroundJob.objects.create(
                    kind="ai_agent_runner_sync", status="succeeded",
                    payload={"workspace_id": self.workspace.id, "owner_id": self.user.id,
                             "requested_revision": self.workspace.revision},
                    result={"workspace_id": self.workspace.id, "revision": self.workspace.revision, "status": "synced"},
                    idempotency_key=f"sync:{self.workspace.id}:{self.workspace.revision}",
                )
                AuditLog.objects.create(
                    user=self.user, action="ai_agent.runner_workspace_synced",
                    target_type="CodeWorkspace", target_id=str(self.workspace.id),
                    metadata={"job_id": sync_job.id, "revision": self.workspace.revision},
                )
                AuditLog.objects.create(
                    user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
                    target_id=str(self.workspace.id), metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
            },
                )

        self.workspace.files = {"main.py": "print('manual review required')"}
        self.workspace.save(update_fields={"files"})
        final_sync = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync", status="succeeded",
            payload={"workspace_id": self.workspace.id, "owner_id": self.user.id,
                     "requested_revision": self.workspace.revision},
            result={"workspace_id": self.workspace.id, "revision": self.workspace.revision, "status": "synced"},
            idempotency_key=f"sync:{self.workspace.id}:{self.workspace.revision}",
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(self.workspace.id),
            metadata={"job_id": final_sync.id, "revision": self.workspace.revision},
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
            target_id=str(self.workspace.id), metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
            },
        )
        exhausted = self.client.post(self.url, {
            "sync_job_id": final_sync.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(exhausted.status_code, 409)
        self.assertIn("four-iteration", exhausted.data["error"])
        self.assertEqual(runner_request.call_count, 4)


class AgentTaskExecutionHardeningTests(AgentTaskVerificationAPITests):
    @patch("api.views._runner_request")
    def test_runner_call_occurs_outside_database_transaction(self, runner_request):
        baseline_atomic_depth = len(connection.atomic_blocks)

        def runner_side_effect(*args, **kwargs):
            self.assertEqual(len(connection.atomic_blocks), baseline_atomic_depth)
            return {
                "job_id": "runner-outside-tx-001", "status": "completed",
                "exit_code": 0, "duration_ms": 20, "stdout": "not persisted",
                "stderr": "", "files": {"main.py": "runner snapshot"},
            }, None

        runner_request.side_effect = runner_side_effect
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])
        result = execute_agent_task_verification(job)
        self.assertEqual(result["status"], "verification_passed_pending_acceptance")
        self.task.refresh_from_db()
        self.assertEqual(self.task.execution_evidence["state"], "passed")
        self.assertNotIn("stdout", self.task.execution_evidence)
        self.assertNotIn("files", self.task.execution_evidence)

    @patch("api.views._runner_request")
    def test_workspace_change_during_runner_execution_invalidates_evidence(self, runner_request):
        def mutate_then_return(*args, **kwargs):
            workspace = CodeWorkspace.objects.get(pk=self.workspace.id)
            workspace.files = {"main.py": "print('new revision')"}
            workspace.save(update_fields={"files"})
            return {
                "job_id": "runner-stale-001", "status": "completed", "exit_code": 0,
                "duration_ms": 30, "stdout": "", "stderr": "",
            }, None

        runner_request.side_effect = mutate_then_return
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])
        result = execute_agent_task_verification(job)
        self.task.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertEqual(result["status"], "verification_stale")
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "stale_workspace")
        self.assertNotIn("human_acceptance", self.task.execution_evidence)
        resync = BackgroundJob.objects.get(pk=self.task.execution_evidence["resync_job_id"])
        self.assertEqual(resync.kind, "ai_agent_runner_sync")
        self.assertEqual(resync.payload["requested_revision"], self.workspace.revision)





    def test_failed_job_finalization_invalidates_uncommitted_pass_evidence(self):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "failed"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])
        self.task.status = "awaiting_approval"
        self.task.execution_evidence = {
            "state": "passed", "revision": self.workspace.revision,
            "runner_job_id": "runner-finished-before-worker-crash",
            "iteration": 1, "history": [],
        }
        self.task.save(update_fields=["status", "execution_evidence"])

        fail_agent_task_verification(job)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "finalization_failed")
        self.assertNotIn("human_acceptance", self.task.execution_evidence)
        self.assertTrue(self.task.execution_evidence.get("resync_job_id"))

    @patch("api.views._runner_request")
    def test_cancellation_during_runner_execution_preserves_cancel_and_resyncs(self, runner_request):
        def cancel_then_return(*args, **kwargs):
            self.run.status = "canceled"
            self.run.save(update_fields=["status"])
            self.task.status = "canceled"
            self.task.save(update_fields=["status"])
            return {
                "job_id": "runner-canceled-001", "status": "completed",
                "exit_code": 0, "duration_ms": 18, "stdout": "", "stderr": "",
            }, None

        runner_request.side_effect = cancel_then_return
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        result = execute_agent_task_verification(job)

        self.run.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(result["status"], "verification_canceled")
        self.assertEqual(self.run.status, "canceled")
        self.assertEqual(self.task.status, "canceled")
        resync = BackgroundJob.objects.get(pk=result["resync_job_id"])
        self.assertEqual(resync.kind, "ai_agent_runner_sync")

    def test_ambiguous_runner_failure_queues_authoritative_resync(self):
        queued = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])

        fail_agent_task_verification(job)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "infrastructure_failed")
        resync = BackgroundJob.objects.get(pk=self.task.execution_evidence["resync_job_id"])
        self.assertEqual(resync.kind, "ai_agent_runner_sync")
        self.assertEqual(resync.payload["requested_revision"], self.workspace.revision)
        self.assertIn("agent-runner-resync:", resync.idempotency_key)

    @patch("api.views._runner_request", return_value=(
        {"job_id": "runner-collaborator-001", "status": "completed", "exit_code": 0,
         "duration_ms": 22, "stdout": "", "stderr": ""}, None,
    ))
    def test_project_collaborator_can_verify_authorized_workspace(self, runner_request):
        workspace_owner = User.objects.create_user(username="workspace-owner", password="StrongPassword123!")
        project = Project.objects.create(owner=workspace_owner, title="Shared workspace project")
        project.collaborators.add(self.user)
        workspace = CodeWorkspace.objects.create(
            owner=workspace_owner, project=project, name="Shared execution workspace",
            files={"main.py": "print('initial')"},
        )
        run = AgentRun.objects.create(
            owner=self.user, workspace=workspace, goal="Verify collaborator work",
            status="approved", approved_at=timezone.now(),
            plan={"acceptance_criteria": ["Collaborator change is correct"]},
            idempotency_key="agent-execution-collaborator-001",
            workspace_revision=workspace.revision,
        )
        task = AgentTask.objects.create(
            run=run, key="collaborator", title="Collaborator implementation",
            status="ready", agent_role="backend",
            acceptance_criteria=["Collaborator change is correct"],
        )
        workspace.files = {"main.py": "print('collaborator change')"}
        workspace.save(update_fields={"files"})
        sync = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync", status="succeeded",
            payload={"workspace_id": workspace.id, "owner_id": self.user.id,
                     "requested_revision": workspace.revision},
            result={"workspace_id": workspace.id, "revision": workspace.revision, "status": "synced"},
            idempotency_key=f"sync:{workspace.id}:{workspace.revision}",
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata={"job_id": sync.id, "revision": workspace.revision},
        )
        AuditLog.objects.create(
            user=self.user, action="ai_agent.patch_applied",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata={
                "previous_revision": workspace.revision - 1,
                "revision": workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": run.id,
                "agent_task_id": task.id,
            },
        )
        url = f"/api/ai/agent/runs/{run.id}/tasks/{task.id}/verify/"
        queued = self.client.post(url, {
            "sync_job_id": sync.id, "revision": workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(queued.status_code, 202)
        job = BackgroundJob.objects.get(pk=queued.data["job_id"])
        job.status = "running"
        job.attempts = 1
        job.save(update_fields=["status", "attempts"])
        result = execute_agent_task_verification(job)
        task.refresh_from_db()
        self.assertEqual(result["status"], "verification_passed_pending_acceptance")
        self.assertEqual(task.status, "awaiting_approval")
        runner_request.assert_called_once()


    @patch("api.views._runner_request", return_value=({"status": "cancelled"}, None))
    def test_run_cancel_propagates_to_the_exact_active_runner_job(self, runner_request):
        verification_job = BackgroundJob.objects.create(
            kind="ai_agent_task_verify",
            status="running",
            payload={
                "run_id": self.run.id,
                "task_id": self.task.id,
                "owner_id": self.user.id,
                "workspace_id": self.workspace.id,
            },
            idempotency_key="agent-cancel-propagation-001",
            max_attempts=1,
        )
        execution_id = f"agent-{verification_job.id}"
        verification_job.payload = {
            **verification_job.payload,
            "runner_execution_id": execution_id,
        }
        verification_job.save(update_fields=["payload"])
        self.task.status = "running"
        self.task.execution_job = verification_job
        self.task.save(update_fields=["status", "execution_job", "updated_at"])
        self.run.status = "running"
        self.run.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/cancel/", {}, format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.run.__class__.objects.get(pk=self.run.id).status, "canceled")
        self.assertEqual(response.data["runner_cancellation"], {
            "requested": 1, "confirmed": 1, "unconfirmed": 0,
        })
        runner_request.assert_called_once_with(
            "POST",
            f"/jobs/{execution_id}/cancel?workspace_id={self.workspace.id}",
            {},
            timeout=5,
        )


    @patch("api.views._runner_request", return_value=({"status": "synced", "files": {}}, None))
    def test_runner_sync_worker_honors_project_collaborator_access(self, runner_request):
        from .management.commands.worker import execute as execute_background_job

        workspace_owner = User.objects.create_user(
            username="sync-workspace-owner", password="StrongPassword123!",
        )
        project = Project.objects.create(owner=workspace_owner, title="Sync collaborator project")
        project.collaborators.add(self.user)
        workspace = CodeWorkspace.objects.create(
            owner=workspace_owner, project=project, name="Sync collaborator workspace",
            files={"main.py": "print('shared')"},
        )
        sync_job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync", status="running",
            payload={
                "workspace_id": workspace.id, "owner_id": self.user.id,
                "requested_revision": workspace.revision,
            },
            idempotency_key=f"sync:{workspace.id}:{workspace.revision}",
        )

        result = execute_background_job(sync_job)

        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["revision"], workspace.revision)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata__job_id=sync_job.id, metadata__revision=workspace.revision,
        ).exists())
        runner_request.assert_called_once()

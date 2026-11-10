from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .agent_execution import VERIFICATION_PROFILES, execute_agent_task_verification
from .models import AgentRun, AgentTask, AuditLog, BackgroundJob, CodeWorkspace


class AgentTaskVerificationAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent-execution-owner", password="StrongPassword123!")
        self.other = User.objects.create_user(username="agent-execution-other", password="StrongPassword123!")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user, name="Execution workspace", files={"main.py": "print('ok')"},
        )
        self.run = AgentRun.objects.create(
            owner=self.user, workspace=self.workspace, goal="Implement and verify a feature",
            status="approved", approved_at=timezone.now(),
            idempotency_key="agent-execution-run-001", workspace_revision=self.workspace.revision,
        )
        self.task = AgentTask.objects.create(
            run=self.run, key="backend", title="Backend implementation",
            status="ready", agent_role="backend",
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
            target_id=str(self.workspace.id), metadata={"revision": self.workspace.revision},
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

    def test_requires_human_patch_approval_audit(self):
        AuditLog.objects.filter(action="ai_agent.patch_applied").delete()
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(response.status_code, 403)

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
        second = self.client.post(self.url, payload, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["job_id"], job.id)
        self.assertEqual(BackgroundJob.objects.filter(kind="ai_agent_task_verify").count(), 1)

    @patch("api.views._runner_request", return_value=(
        {"status": "completed", "exit_code": 0, "duration_ms": 31, "stdout": "private output", "stderr": ""}, None,
    ))
    def test_runner_success_persists_evidence_and_unblocks_dependents(self, runner_request):
        dependent = AgentTask.objects.create(
            run=self.run, key="review", title="Review", status="blocked",
        )
        dependent.depends_on.add(self.task)
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=response.data["job_id"])
        job.attempts = 1
        result = execute_agent_task_verification(job)
        self.assertEqual(result["status"], "verified")
        self.task.refresh_from_db()
        dependent.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "succeeded")
        self.assertEqual(dependent.status, "ready")
        self.assertEqual(self.run.status, "running")
        self.assertEqual(self.task.execution_evidence["exit_code"], 0)
        self.assertNotIn("stdout", self.task.execution_evidence)
        runner_request.assert_called_once()
        self.assertEqual(
            runner_request.call_args.args[2]["command"],
            VERIFICATION_PROFILES["python_pytest"],
        )

    @patch("api.views._runner_request", return_value=(
        {"status": "failed", "exit_code": 1, "duration_ms": 10, "stdout": "", "stderr": "failure"}, None,
    ))
    def test_failed_runner_verification_is_terminal_evidence_not_success(self, runner_request):
        response = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        job = BackgroundJob.objects.get(pk=response.data["job_id"])
        job.attempts = 1
        result = execute_agent_task_verification(job)
        self.assertEqual(result["status"], "verification_failed")
        self.task.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(self.task.execution_evidence["exit_code"], 1)

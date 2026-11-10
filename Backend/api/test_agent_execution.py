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
            plan={"acceptance_criteria": ["Feature behavior is correct", "Existing behavior remains compatible"]},
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
        job.attempts = 1
        result = execute_agent_task_verification(job)
        self.assertEqual(result["status"], "verification_passed_pending_acceptance")
        self.task.refresh_from_db()
        dependent.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "awaiting_approval")
        self.assertEqual(dependent.status, "blocked")
        self.assertEqual(self.run.status, "running")
        accepted = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.run.plan["acceptance_criteria"]},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200)
        self.task.refresh_from_db()
        dependent.refresh_from_db()
        self.assertEqual(self.task.status, "succeeded")
        self.assertEqual(dependent.status, "ready")
        self.assertEqual(self.task.execution_evidence["human_acceptance"]["criteria_scope"], "agent_run")
        self.assertEqual(self.task.execution_evidence["exit_code"], 0)
        self.assertNotIn("stdout", self.task.execution_evidence)
        runner_request.assert_called_once()
        self.assertEqual(
            runner_request.call_args.args[2]["command"],
            VERIFICATION_PROFILES["python_pytest"],
        )
        self.assertEqual(VERIFICATION_PROFILES["node_test"], "npm test")

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
        self.assertEqual(self.task.execution_evidence["state"], "failed")
        self.assertEqual(self.task.execution_evidence["iteration"], 1)


class AgentTaskAcceptanceTests(AgentTaskVerificationAPITests):
    def test_acceptance_requires_all_exact_plan_criteria(self):
        self.task.status = "awaiting_approval"
        self.task.execution_revision = self.workspace.revision
        self.task.execution_evidence = {"state": "passed", "revision": self.workspace.revision}
        self.task.save(update_fields=["status", "execution_revision", "execution_evidence"])
        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": ["Feature behavior is correct"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "awaiting_approval")

    def test_acceptance_rejected_if_workspace_changes_after_verification(self):
        self.task.status = "awaiting_approval"
        self.task.execution_revision = self.workspace.revision
        self.task.execution_evidence = {"state": "passed", "revision": self.workspace.revision}
        self.task.save(update_fields=["status", "execution_revision", "execution_evidence"])
        self.workspace.files = {"main.py": "print('later change')"}
        self.workspace.save(update_fields={"files"})
        response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.run.plan["acceptance_criteria"]},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "awaiting_approval")


class AgentTaskRetryTests(AgentTaskVerificationAPITests):
    @patch("api.views._runner_request", return_value=(
        {"status": "failed", "exit_code": 1, "duration_ms": 12, "stdout": "", "stderr": "failure"}, None,
    ))
    def test_failed_task_can_retry_only_after_new_approved_synced_revision(self, runner_request):
        first = self.client.post(self.url, {
            "sync_job_id": self.sync_job.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        first_job = BackgroundJob.objects.get(pk=first.data["job_id"])
        first_job.attempts = 1
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
            user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
            target_id=str(self.workspace.id), metadata={"revision": self.workspace.revision},
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
        {"status": "failed", "exit_code": 1, "duration_ms": 8, "stdout": "", "stderr": "failure"}, None,
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
            job.attempts = 1
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
                    user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
                    target_id=str(self.workspace.id), metadata={"revision": self.workspace.revision},
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
            user=self.user, action="ai_agent.patch_applied", target_type="CodeWorkspace",
            target_id=str(self.workspace.id), metadata={"revision": self.workspace.revision},
        )
        exhausted = self.client.post(self.url, {
            "sync_job_id": final_sync.id, "revision": self.workspace.revision,
            "profile": "python_pytest",
        }, format="json")
        self.assertEqual(exhausted.status_code, 409)
        self.assertIn("four-iteration", exhausted.data["error"])
        self.assertEqual(runner_request.call_count, 4)

from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .agent_execution import VERIFICATION_PROFILES, execute_agent_task_verification
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
        self.assertEqual(self.task.execution_evidence["runner_job_id"], "runner-test-001")
        self.assertEqual(self.task.execution_evidence["verification_job_id"], job.id)
        self.assertEqual(self.task.execution_evidence["sync_job_id"], self.sync_job.id)
        self.assertNotIn("stdout", self.task.execution_evidence)
        runner_request.assert_called_once()
        self.assertEqual(
            runner_request.call_args.args[2]["command"],
            VERIFICATION_PROFILES["python_pytest"],
        )
        self.assertEqual(VERIFICATION_PROFILES["node_test"], "npm test")

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
            user=self.user, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(self.workspace.id),
            metadata={"job_id": final_sync.id, "revision": self.workspace.revision},
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
        self.assertEqual(result["status"], "verification_stale")
        self.assertEqual(self.task.status, "failed")
        self.assertEqual(self.task.execution_evidence["state"], "stale_workspace")
        self.assertNotIn("human_acceptance", self.task.execution_evidence)

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
            metadata={"revision": workspace.revision},
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

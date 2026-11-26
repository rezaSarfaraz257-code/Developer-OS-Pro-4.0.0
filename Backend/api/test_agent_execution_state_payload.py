from django.contrib.auth.models import User
from django.test import TestCase

from .agent_runtime import _run_payload
from .models import AgentRun, AgentTask, AuditLog, CodeWorkspace


class AgentExecutionStatePayloadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent-state-user", password="test-password")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Agent execution workspace",
            files={"main.py": "print('ready')"},
        )
        self.run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Implement and verify a task",
            idempotency_key="agent-state-payload-0001",
            workspace_revision=self.workspace.revision,
        )
        self.task = AgentTask.objects.create(
            run=self.run,
            key="implement",
            title="Implement feature",
            acceptance_criteria=["Build succeeds", "Tests pass"],
            status="ready",
        )

    def execution_state(self):
        return _run_payload(self.run)["execution_state"]

    def test_plan_and_approval_states_are_distinguished(self):
        self.assertEqual(self.execution_state(), "plan_only_no_workspace_mutations")
        self.run.status = "approved"
        self.run.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.execution_state(), "approved_awaiting_task_execution")

    def test_patch_application_is_reported_until_runner_verification_starts(self):
        self.workspace.files = {"main.py": "print('approved patch')"}
        self.workspace.save()
        AuditLog.objects.create(
            user=self.user,
            action="ai_agent.patch_applied",
            target_type="CodeWorkspace",
            target_id=str(self.workspace.id),
            metadata={
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
                "previous_revision": self.run.workspace_revision,
                "revision": self.workspace.revision,
            },
        )
        self.assertEqual(self.execution_state(), "patch_applied_sync_or_verification_pending")

        self.workspace.files = {"main.py": "print('newer manual edit')"}
        self.workspace.save()
        self.assertEqual(self.execution_state(), "workspace_changed_after_patch_requires_reanalysis")

    def test_runner_and_human_acceptance_states_are_evidence_based(self):
        self.task.status = "running"
        self.task.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.execution_state(), "runner_verification_in_progress")

        self.task.status = "awaiting_approval"
        self.task.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.execution_state(), "verification_passed_pending_human_acceptance")

        self.task.status = "failed"
        self.task.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.execution_state(), "execution_failed_requires_review")

        self.task.status = "succeeded"
        self.task.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.execution_state(), "verified_human_accepted")

    def test_partial_completion_and_cancellation_are_not_reported_as_success(self):
        self.task.status = "succeeded"
        self.task.save(update_fields=["status", "updated_at"])
        AgentTask.objects.create(
            run=self.run,
            key="second",
            title="Second task",
            acceptance_criteria=["Second task passes"],
            status="ready",
            sequence=1,
        )
        self.assertEqual(self.execution_state(), "partially_verified_awaiting_next_task")

        self.run.status = "canceled"
        self.run.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.execution_state(), "canceled")

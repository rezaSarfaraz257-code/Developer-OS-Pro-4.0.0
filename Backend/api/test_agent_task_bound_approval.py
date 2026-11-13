import json
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core import signing
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .ai_engine import _issue_patch_approval_token
from .models import AgentRun, AgentTask, AuditLog, CodeWorkspace


class AgentTaskBoundPatchApprovalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="task-bound-approval-owner",
            password="StrongPassword123!",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Task-bound approval workspace",
            files={"main.py": "print('before')\n"},
        )
        self.run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Implement one bounded task",
            status="approved",
            approved_at=timezone.now(),
            workspace_revision=self.workspace.revision,
            idempotency_key="task-bound-approval-run-0001",
        )
        self.task = AgentTask.objects.create(
            run=self.run,
            key="implement",
            title="Implement the approved change",
            status="ready",
            acceptance_criteria=["The requested behavior is implemented and verified."],
        )
        self.proposal = {
            "type": "patch_proposal",
            "changes": [{
                "path": "main.py",
                "operation": "modify",
                "reason": "task-bound approval regression test",
                "content": "print('after')\n",
            }],
        }

    def _token(self, **extra):
        data = {
            "workspace": self.workspace.id,
            "agent_run_id": self.run.id,
            "agent_task_id": self.task.id,
        }
        data.update(extra)
        request = SimpleNamespace(data=data, user=self.user)
        return _issue_patch_approval_token(request, json.dumps(self.proposal))

    def test_signed_token_contains_exact_run_and_task_identity(self):
        token = self._token()
        self.assertTrue(token)
        payload = signing.loads(token)
        self.assertEqual(payload["agent_run_id"], self.run.id)
        self.assertEqual(payload["agent_task_id"], self.task.id)
        self.assertEqual(payload["workspace_id"], self.workspace.id)

    def test_partial_task_binding_never_degrades_to_workspace_only_token(self):
        token = self._token(agent_task_id=None)
        self.assertIsNone(token)

    def test_apply_rechecks_task_state_before_mutating_workspace(self):
        token = self._token()
        self.assertTrue(token)
        self.task.status = "canceled"
        self.task.save(update_fields=["status"])
        previous_revision = self.workspace.revision

        response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": token},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.revision, previous_revision)
        self.assertFalse(AuditLog.objects.filter(
            action="ai_agent.patch_applied",
            target_id=str(self.workspace.id),
        ).exists())

    def test_task_bound_approval_rechecks_dependency_state(self):
        prerequisite = AgentTask.objects.create(
            run=self.run,
            key="prerequisite",
            title="Complete prerequisite",
            status="ready",
            acceptance_criteria=["The prerequisite work is complete."],
        )
        self.task.depends_on.add(prerequisite)
        self.assertIsNone(self._token())

        prerequisite.status = "succeeded"
        prerequisite.save(update_fields=["status"])
        token = self._token()
        self.assertTrue(token)
        prerequisite.status = "failed"
        prerequisite.save(update_fields=["status"])
        previous_revision = self.workspace.revision

        response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": token},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.revision, previous_revision)

    def test_apply_audit_is_bound_to_exact_run_and_task(self):
        token = self._token()
        response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": token},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        event = AuditLog.objects.get(
            action="ai_agent.patch_applied",
            target_id=str(self.workspace.id),
        )
        self.assertEqual(event.metadata["agent_run_id"], self.run.id)
        self.assertEqual(event.metadata["agent_task_id"], self.task.id)

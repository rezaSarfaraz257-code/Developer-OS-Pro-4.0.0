import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AgentRun, AgentTask, AuditLog, CodeWorkspace


class AgentTaskImplementationProposalTests(TestCase):
    def setUp(self):
        self.rate_limit = patch("api.security.consume_rate_limit", return_value=(True, None))
        self.rate_limit.start()
        self.addCleanup(self.rate_limit.stop)
        self.usage = patch(
            "api.views._consume_usage",
            return_value=(True, 1, 100, "pro"),
        )
        self.usage.start()
        self.addCleanup(self.usage.stop)

        self.user = User.objects.create_user(
            username="agent-proposal-owner",
            password="StrongPassword123!",
        )
        self.other = User.objects.create_user(
            username="agent-proposal-other",
            password="StrongPassword123!",
        )
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Implementation proposal workspace",
            files={"main.py": "def answer():\n    return 1\n"},
        )
        self.run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Implement the requested feature safely",
            status="approved",
            approved_at=timezone.now(),
            workspace_revision=self.workspace.revision,
            idempotency_key="agent-proposal-run-0001",
        )
        self.task = AgentTask.objects.create(
            run=self.run,
            key="implement_answer",
            title="Implement answer behavior",
            description="Change answer() to return 42.",
            agent_role="backend",
            status="ready",
            acceptance_criteria=["answer() returns 42", "Existing module imports remain valid"],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/proposal/"
        self.proposal = {
            "type": "patch_proposal",
            "summary": "Implement the requested answer behavior.",
            "root_cause": "The current function returns the old value.",
            "confidence": 0.9,
            "affected_files": ["main.py"],
            "changes": [{
                "path": "main.py",
                "operation": "modify",
                "reason": "Meet the task acceptance criterion.",
                "content": "def answer():\n    return 42\n",
            }],
            "test_plan": ["Run the project's Python test suite."],
            "risks": [],
            "requires_approval": True,
        }

    @patch("api.ai_engine._call_provider")
    def test_proposal_is_task_bound_and_does_not_mutate_workspace(self, provider):
        provider.return_value = (json.dumps(self.proposal), None)
        before_files = dict(self.workspace.files)
        before_revision = self.workspace.revision

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "proposal_ready")
        self.assertTrue(response.data["workspace_mutated"] is False)
        self.assertEqual(response.data["workspace_revision"], before_revision)
        self.assertEqual(response.data["proposal"], self.proposal)
        token = signing.loads(response.data["approval_token"])
        self.assertEqual(token["user_id"], self.user.id)
        self.assertEqual(token["workspace_id"], self.workspace.id)
        self.assertEqual(token["workspace_revision"], before_revision)
        self.assertEqual(token["agent_run_id"], self.run.id)
        self.assertEqual(token["agent_task_id"], self.task.id)

        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files, before_files)
        self.assertEqual(self.workspace.revision, before_revision)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="agent_task.patch_proposal_generated",
            target_type="AgentTask",
            target_id=str(self.task.id),
            metadata__workspace_mutated=False,
        ).exists())

    @patch("api.ai_engine._call_provider")
    def test_existing_apply_endpoint_applies_only_after_human_submits_token(self, provider):
        provider.return_value = (json.dumps(self.proposal), None)
        proposal_response = self.client.post(self.url, {}, format="json")
        self.assertEqual(proposal_response.status_code, 200, proposal_response.data)

        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files["main.py"], "def answer():\n    return 1\n")

        apply_response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": proposal_response.data["approval_token"]},
            format="json",
        )
        self.assertEqual(apply_response.status_code, 200, apply_response.data)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files["main.py"], "def answer():\n    return 42\n")
        self.assertEqual(self.workspace.revision, proposal_response.data["workspace_revision"] + 1)

    @patch("api.ai_engine._call_provider")
    def test_unsafe_path_is_rejected_without_workspace_mutation(self, provider):
        unsafe = {**self.proposal, "changes": [{
            **self.proposal["changes"][0], "path": "../outside.py",
        }], "affected_files": ["../outside.py"]}
        provider.return_value = (json.dumps(unsafe), None)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 422)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files["main.py"], "def answer():\n    return 1\n")
        self.assertFalse(AuditLog.objects.filter(action="agent_task.patch_proposal_generated").exists())

    @patch("api.ai_engine._call_provider")
    def test_provider_cannot_propose_modification_of_unsampled_or_secret_file(self, provider):
        unsafe = {**self.proposal, "changes": [{
            **self.proposal["changes"][0], "path": ".env",
        }], "affected_files": [".env"]}
        provider.return_value = (json.dumps(unsafe), None)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 422)

    @patch("api.ai_engine._call_provider")
    def test_missing_acceptance_criteria_blocks_provider_call(self, provider):
        self.task.acceptance_criteria = []
        self.task.save(update_fields=["acceptance_criteria"])
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)
        provider.assert_not_called()

    @patch("api.ai_engine._call_provider")
    def test_unfinished_dependency_blocks_proposal(self, provider):
        prerequisite = AgentTask.objects.create(
            run=self.run, key="prerequisite", title="Prerequisite",
            status="ready", acceptance_criteria=["Prerequisite is done"],
        )
        self.task.depends_on.add(prerequisite)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)
        provider.assert_not_called()

    @patch("api.ai_engine._call_provider")
    def test_other_user_cannot_access_task_or_generate_proposal(self, provider):
        self.client.force_authenticate(self.other)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 404)
        provider.assert_not_called()

    @patch("api.ai_engine._call_provider")
    def test_stale_workspace_after_provider_response_discards_proposal(self, provider):
        def generate(*args, **kwargs):
            self.workspace.files = {"main.py": "def answer():\n    return 7\n"}
            self.workspace.save(update_fields=["files"])
            return json.dumps(self.proposal), None

        provider.side_effect = generate
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "stale_agent_proposal_context")
        self.assertFalse(AuditLog.objects.filter(action="agent_task.patch_proposal_generated").exists())

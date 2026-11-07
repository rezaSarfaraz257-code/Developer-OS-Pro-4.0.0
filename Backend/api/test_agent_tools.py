import json

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import AgentRun, AgentTask, AuditLog, CodeWorkspace


class ReadOnlyAgentToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="readonly-agent-owner", password="StrongPassword123!")
        self.other = User.objects.create_user(username="readonly-agent-other", password="StrongPassword123!")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Tool registry workspace",
            language="python",
            files={
                "main.py": "DEBUG = True\\nsubprocess.run(command, shell=True)\\n",
                "package.json": json.dumps({"dependencies": {"react": "^19.0.0"}, "devDependencies": {"vite": "^7.0.0"}}),
                ".env": "DATABASE_URL=postgres://user:private-password@db.example.test/app",
                "keys/private.pem": "PRIVATE KEY MATERIAL",
            },
        )
        self.run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Inspect the project",
            status="approved",
            idempotency_key="readonly-tool-run-001",
            workspace_revision=self.workspace.revision,
        )
        self.task = AgentTask.objects.create(
            run=self.run,
            key="architecture",
            title="Inspect dependencies",
            agent_role="architect",
            status="ready",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def tool_url(self, tool, task=None, run=None):
        return f"/api/ai/agent/runs/{(run or self.run).id}/tasks/{(task or self.task).id}/tools/{tool}/"

    def test_approved_architect_can_analyze_dependencies_without_mutating_workspace(self):
        before = dict(self.workspace.files)
        response = self.client.post(self.tool_url("analyze_dependencies"), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["execution_state"], "read_only_analysis_no_workspace_mutations")
        self.assertEqual(response.data["result"]["dependency_count"], 2)
        self.assertNotIn(".env", [item["path"] for item in response.data["result"]["manifests"]])
        self.assertTrue(response.data["result"]["read_only"])
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files, before)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="agent_task.readonly_tool_invoked",
            target_id=str(self.task.id),
            metadata__tool="analyze_dependencies",
        ).exists())

    def test_unapproved_run_cannot_invoke_tool(self):
        self.run.status = "awaiting_approval"
        self.run.save(update_fields=["status"])
        response = self.client.post(self.tool_url("inspect_workspace"), {}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_role_cannot_invoke_tool_outside_its_capability_contract(self):
        response = self.client.post(self.tool_url("review_security"), {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_other_user_cannot_invoke_private_run_tool(self):
        self.client.force_authenticate(self.other)
        response = self.client.post(self.tool_url("inspect_workspace"), {}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_stale_workspace_revision_blocks_tool_invocation(self):
        self.workspace.files = {**self.workspace.files, "new.py": "value = 1"}
        self.workspace.save(update_fields=["files"])
        response = self.client.post(self.tool_url("inspect_workspace"), {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "stale_workspace")

    def test_security_review_returns_findings_without_source_content(self):
        task = AgentTask.objects.create(
            run=self.run,
            key="security",
            title="Review security",
            agent_role="security",
            status="ready",
        )
        response = self.client.post(self.tool_url("review_security", task=task), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("debug_enabled", [item["rule"] for item in response.data["result"]["findings"]])
        self.assertIn("shell_true", [item["rule"] for item in response.data["result"]["findings"]])
        self.assertNotIn("subprocess.run(command", json.dumps(response.data))
        self.assertNotIn("private-password", json.dumps(response.data))

    def test_blocked_task_cannot_invoke_tool(self):
        self.task.status = "blocked"
        self.task.save(update_fields=["status"])
        response = self.client.post(self.tool_url("inspect_workspace"), {}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_unknown_tool_is_not_executable(self):
        response = self.client.post(self.tool_url("shell"), {"command": "whoami"}, format="json")
        self.assertEqual(response.status_code, 404)

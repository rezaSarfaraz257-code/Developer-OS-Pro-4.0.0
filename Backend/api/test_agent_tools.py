import json

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .agent_tools import MAX_WORKSPACE_TOTAL_BYTES, _files, _safe_path, analyze_dependencies
from .models import AIConversation, AIMessage, AgentRun, AgentTask, AuditLog, CodeWorkspace


class ReadOnlyAgentToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="readonly-agent-owner", password="StrongPassword123!")
        self.other = User.objects.create_user(username="readonly-agent-other", password="StrongPassword123!")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Tool registry workspace",
            language="python",
            files={
                "main.py": "DEBUG = True\nsubprocess.run(command, shell=True)\n",
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

    def test_safe_path_rejects_traversal_absolute_and_malformed_paths(self):
        for path in ("../secret.txt", "src/../../secret.txt", "/etc/passwd", "C:/Windows/win.ini", "src//main.py", "src/./main.py", "bad\\x00name.py", "", "x" * 501):
            with self.subTest(path=path):
                self.assertFalse(_safe_path(path))
        self.assertTrue(_safe_path("src/components/App.jsx"))

    def test_workspace_snapshot_excludes_traversal_paths(self):
        workspace = type("Workspace", (), {
            "files": {
                "../outside.py": "must-not-enter-agent-context",
                "/absolute.py": "must-not-enter-agent-context",
                "safe/main.py": "value = 1",
            }
        })()
        self.assertEqual(_files(workspace), {"safe/main.py": "value = 1"})

        workspace = type("Workspace", (), {
            "files": {f"module_{index}.py": "x" * 150_000 for index in range(20)}
        })()
        selected = _files(workspace)
        total_bytes = sum(len(value.encode("utf-8")) for value in selected.values())
        self.assertLessEqual(total_bytes, MAX_WORKSPACE_TOTAL_BYTES)
        self.assertLess(len(selected), 20)

    def test_workspace_snapshot_skips_oversized_files_and_continues(self):
        workspace = type("Workspace", (), {
            "files": {
                "oversized.py": "x" * 300_000,
                "safe.py": "value = 1",
                ".env": "SECRET=must-not-be-read",
            }
        })()
        selected = _files(workspace)
        self.assertEqual(selected, {"safe.py": "value = 1"})

    def test_approved_architect_can_analyze_dependencies_without_mutating_workspace(self):
        before = dict(self.workspace.files)
        response = self.client.post(self.tool_url("analyze_dependencies"), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["execution_state"], "read_only_analysis_no_workspace_mutations")
        self.assertEqual(response.data["result"]["dependency_count"], 2)
        self.assertEqual(response.data["task_status"], "succeeded")
        self.assertEqual(response.data["run_status"], "succeeded")
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

    def test_read_only_task_completion_persists_evidence_and_unlocks_dag(self):
        conversation = AIConversation.objects.create(owner=self.user, title="Agent evidence")
        self.run.conversation = conversation
        self.run.save(update_fields=["conversation"])
        dependent = AgentTask.objects.create(
            run=self.run,
            key="follow_up",
            title="Inspect after dependency analysis",
            agent_role="architect",
            status="blocked",
            sequence=1,
        )
        dependent.depends_on.add(self.task)

        first = self.client.post(self.tool_url("analyze_dependencies"), {}, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["run_status"], "running")
        self.assertEqual(first.data["newly_ready_tasks"], ["follow_up"])
        dependent.refresh_from_db()
        self.assertEqual(dependent.status, "ready")
        self.assertTrue(AIMessage.objects.filter(
            conversation=conversation,
            role="tool",
            context__agent_task_id=self.task.id,
        ).exists())

        second = self.client.post(
            self.tool_url("analyze_dependencies", task=dependent), {}, format="json"
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["run_status"], "succeeded")
        self.assertEqual(AIMessage.objects.filter(conversation=conversation, role="tool").count(), 2)

        replay = self.client.post(self.tool_url("analyze_dependencies"), {}, format="json")
        self.assertEqual(replay.status_code, 409)

    def test_unapproved_run_cannot_invoke_tool(self):
        self.run.status = "awaiting_approval"
        self.run.save(update_fields=["status"])
        response = self.client.post(self.tool_url("inspect_workspace"), {}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_role_cannot_invoke_tool_outside_its_capability_contract(self):
        self.task.agent_role = "frontend"
        self.task.save(update_fields=["agent_role"])
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

    def test_dependency_analyzer_rejects_valid_json_with_wrong_manifest_shape(self):
        self.workspace.files = {**self.workspace.files, "package.json": "[]"}
        self.workspace.save(update_fields=["files"])
        result = analyze_dependencies(self.workspace)
        manifest = next(item for item in result["manifests"] if item["path"] == "package.json")
        self.assertEqual(manifest["status"], "invalid_manifest_shape")
        self.assertEqual(result["dependency_count"], 0)

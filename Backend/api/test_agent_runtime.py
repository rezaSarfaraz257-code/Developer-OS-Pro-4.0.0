import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .agent_runtime import (
    AGENT_CONTEXT_MAX_SAMPLE_CHARS,
    AGENT_SPECIALIST_CAPABILITIES,
    _agent_context_file_samples,
    _redact_sensitive_content,
    _validate_plan,
    build_agent_plan,
)
from .agent_tools import TOOL_REGISTRY
from .models import AgentRun, AgentTask, AIConversation, AIMessage, AuditLog, BackgroundJob, CodeWorkspace


class AgentContextRedactionTests(TestCase):
    def test_workspace_samples_reject_malformed_and_traversal_paths(self):
        samples = _agent_context_file_samples({
            "../outside.py": "must-not-leak",
            "/absolute.py": "must-not-leak",
            "C:/private.py": "must-not-leak",
            "src//broken.py": "must-not-leak",
            "src/./dot.py": "must-not-leak",
            "safe/main.py": "value = 1",
        })
        self.assertEqual(samples, {"safe/main.py": "value = 1"})

    def test_workspace_samples_enforce_file_and_aggregate_budgets(self):
        files = {
            f"module_{index}.py": "x" * 1800
            for index in range(120)
        }
        files["late_secret.py"] = "API_KEY=must-not-leak"
        samples = _agent_context_file_samples(files)
        self.assertLessEqual(sum(len(value) for value in samples.values()), AGENT_CONTEXT_MAX_SAMPLE_CHARS)
        self.assertLessEqual(len(samples), 120)
        self.assertNotIn("late_secret.py", samples)

    def test_workspace_samples_skip_oversized_and_sensitive_files(self):
        samples = _agent_context_file_samples({
            "oversized.py": "API_KEY=oversized-secret-value" + ("x" * 256000),
            ".env.production": "SECRET=must-not-leak",
            "secrets/service.json": "password=must-not-leak",
            "safe.py": "API_KEY=inline-secret-value-123",
        })
        self.assertEqual(set(samples), {"safe.py"})
        self.assertNotIn("inline-secret-value-123", samples["safe.py"])
        self.assertIn("[REDACTED]", samples["safe.py"])

    def test_redacts_named_credentials_and_common_provider_tokens(self):
        source = (
            'API_KEY="inline-secret-value-123"\n'
            'password=super-secret-password-value\n'
            'aws_access_key=AKIAABCDEFGHIJKLMNOP\n'
            'github=ghp_abcdefghijklmnopqrstuvwxyz123456\n'
            'openai=sk-proj-abcdefghijklmnopqrstuvwxyz123456\n'
            'stripe=sk_test_abcdefghijklmnopqrstuvwxyz123456\n'
            'google=AIza0123456789abcdefghijklmnopqrstuvwx\n'
            'gitlab=glpat-abcdefghijklmnopqrstuvwxyz123456\n'
            'npm=npm_abcdefghijklmnopqrstuvwxyz123456789012345\n'
            'pypi=pypi-abcdefghijklmnopqrstuvwxyz12345678901234567890123456789012345678901234567890\n'
            'jwt=eyJabcdefghijk.eyJabcdefghijk.signaturevalue12345\n'
            'slack=xoxb-1234567890-abcdefghijk\n'
            'authorization=Bearer abcdefghijklmnopqrstuvwxyz123456\n'
            'DATABASE_URL=postgres://agent:database-password-value@db.example.test:5432/app\n'
        )
        redacted = _redact_sensitive_content(source)
        self.assertNotIn("inline-secret-value-123", redacted)
        self.assertNotIn("super-secret-password-value", redacted)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", redacted)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("sk-proj-abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("sk_test_abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("AIza0123456789abcdefghijklmnopqrstuvwx", redacted)
        self.assertNotIn("glpat-abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("npm_abcdefghijklmnopqrstuvwxyz123456789012345", redacted)
        self.assertNotIn("pypi-abcdefghijklmnopqrstuvwxyz12345678901234567890123456789012345678901234567890", redacted)
        self.assertNotIn("eyJabcdefghijk.eyJabcdefghijk.signaturevalue12345", redacted)
        self.assertNotIn("xoxb-1234567890-abcdefghijk", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("database-password-value", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redacts_complete_quoted_credentials_containing_spaces(self):
        source = (
            'password = "correct horse battery staple"\n' +
            "API_KEY='multi word provider credential value'\n" +
            'authorization: "Bearer opaque token with spaces"\n'
        )
        redacted = _redact_sensitive_content(source)
        self.assertNotIn("correct horse battery staple", redacted)
        self.assertNotIn("multi word provider credential value", redacted)
        self.assertNotIn("opaque token with spaces", redacted)
        self.assertEqual(redacted.count("[REDACTED]"), 3)

    def test_redacts_pem_private_key_blocks(self):
        source = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIE-private-material-that-must-not-leak\n"
            "-----END PRIVATE KEY-----"
        )
        redacted = _redact_sensitive_content(source)
        self.assertNotIn("MIIE-private-material-that-must-not-leak", redacted)
        self.assertIn("[REDACTED_PRIVATE_KEY]", redacted)


class AgentPlanValidationTests(TestCase):
    def test_advertised_specialist_capabilities_are_registered_tools(self):
        self.assertEqual(set(AGENT_SPECIALIST_CAPABILITIES), {
            "product_manager", "architect", "frontend", "backend", "database",
            "ui_ux", "testing", "security", "devops", "debugger",
            "code_reviewer", "documentation",
        })
        for role, capabilities in AGENT_SPECIALIST_CAPABILITIES.items():
            with self.subTest(role=role):
                self.assertTrue(capabilities)
                self.assertTrue(set(capabilities).issubset(TOOL_REGISTRY))
    def test_rejects_dependency_cycles(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            _validate_plan({
                "tasks": [
                    {"id": "first", "title": "First", "depends_on": ["second"]},
                    {"id": "second", "title": "Second", "depends_on": ["first"]},
                ]
            })

    def test_rejects_non_topological_task_order(self):
        with self.assertRaisesRegex(ValueError, "ordered before"):
            _validate_plan({
                "tasks": [
                    {"id": "dependent", "title": "Dependent task", "depends_on": ["prerequisite"]},
                    {"id": "prerequisite", "title": "Prerequisite", "depends_on": []},
                ]
            })

    def test_rejects_unsupported_specialist_role(self):
        with self.assertRaisesRegex(ValueError, "unsupported specialist"):
            _validate_plan({
                "tasks": [
                    {"id": "first", "title": "Run arbitrary tool", "agent_role": "shell_root", "depends_on": []},
                ]
            })

    def test_rejects_unknown_dependencies(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            _validate_plan({
                "tasks": [
                    {"id": "first", "title": "First", "depends_on": ["missing"]},
                ]
            })

    def test_rejects_missing_or_invalid_task_acceptance_criteria(self):
        invalid_plans = [
            {"tasks": [{"id": "inspect", "title": "Inspect", "depends_on": []}]},
            {"tasks": [{"id": "inspect", "title": "Inspect", "depends_on": [], "acceptance_criteria": []}]},
            {"tasks": [{"id": "inspect", "title": "Inspect", "depends_on": [], "acceptance_criteria": [" ", " "] }]},
            {"tasks": [{"id": "inspect", "title": "Inspect", "depends_on": [], "acceptance_criteria": ["Same", "same"]}]},
        ]
        for plan in invalid_plans:
            with self.subTest(plan=plan), self.assertRaisesRegex(ValueError, "task-specific acceptance criteria"):
                _validate_plan(plan)

    def test_accepts_topological_dependency_graph(self):
        plan = _validate_plan({
            "summary": "Safe plan",
            "risks": [],
            "acceptance_criteria": ["Tests pass"],
            "tasks": [
                {"id": "inspect", "title": "Inspect repository", "depends_on": [], "acceptance_criteria": ["Repository structure and critical entry points are documented."]},
                {"id": "test", "title": "Add regression tests", "depends_on": ["inspect"], "acceptance_criteria": ["Regression tests cover the identified behavior and pass."]},
            ],
        })
        self.assertEqual([item["key"] for item in plan["tasks"]], ["inspect", "test"])


class AgentRunLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent-run-owner", password="StrongPassword123!")
        self.other = User.objects.create_user(username="agent-run-other", password="StrongPassword123!")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Agent runtime workspace",
            language="python",
            files={"main.py": "API_KEY = 'inline-secret-value-123'\ndef main():\n    return 1\n", "boundary.py": ("x" * 1780) + "\nAPI_KEY=boundary-secret-value-123456", ".env": "SECRET=must-not-leak", ".npmrc": "//registry/token=hidden", ".aws/credentials": "aws_secret_access_key=hidden", "keys/private.pem": "PRIVATE KEY"},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "free"))
    def test_create_is_idempotent_and_enqueues_background_job(self, usage):
        payload = {"workspace": self.workspace.id, "goal": "Review and improve the project"}
        first = self.client.post(
            "/api/ai/agent/runs/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="agent-run-idempotent-001",
        )
        self.assertEqual(first.status_code, 202)
        run_id = first.data["id"]
        self.assertEqual(first.data["status"], "queued")
        self.assertTrue(BackgroundJob.objects.filter(
            kind="ai_agent_plan",
            payload={"run_id": run_id},
            status="queued",
        ).exists())

        second = self.client.post(
            "/api/ai/agent/runs/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="agent-run-idempotent-001",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["id"], run_id)
        self.assertEqual(AgentRun.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(usage.call_count, 1)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="agent_run.created",
            target_id=str(run_id),
        ).exists())

    def test_unknown_legacy_agent_role_advertises_no_capabilities(self):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Review a legacy task safely",
            idempotency_key="legacy-role-run-0001",
            workspace_revision=self.workspace.revision,
        )
        AgentTask.objects.create(
            run=run,
            key="legacy",
            title="Legacy specialist task",
            agent_role="unknown_legacy_role",
            status="ready",
        )
        response = self.client.get(f"/api/ai/agent/runs/{run.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["tasks"][0]["allowed_capabilities"], [])

    def test_other_user_cannot_read_or_cancel_agent_run(self):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Private goal",
            idempotency_key="private-run-0001",
            workspace_revision=self.workspace.revision,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/ai/agent/runs/{run.id}/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/ai/agent/runs/{run.id}/cancel/").status_code, 404)

    def test_invalid_workspace_identifier_returns_client_error(self):
        response = self.client.post(
            "/api/ai/agent/runs/",
            {"workspace": "not-an-id", "goal": "Inspect safely"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_approval_marks_plan_only_and_does_not_mutate_workspace(self):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Review this workspace",
            status="awaiting_approval",
            idempotency_key="approve-plan-0001",
            workspace_revision=self.workspace.revision,
        )
        original_files = dict(self.workspace.files)
        response = self.client.post(f"/api/ai/agent/runs/{run.id}/approve/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(response.data["execution_state"], "plan_only_no_workspace_mutations")
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files, original_files)

    @patch("api.agent_tools.execute_readonly_tool", return_value={"file_count": 1, "findings": []})
    def test_approved_ready_task_executes_registered_readonly_tool_once(self, execute_tool):
        conversation = AIConversation.objects.create(owner=self.user, title="Agent evidence")
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            conversation=conversation,
            goal="Inspect this workspace safely",
            status="approved",
            idempotency_key="readonly-tool-run-0001",
            workspace_revision=self.workspace.revision,
        )
        task = AgentTask.objects.create(
            run=run,
            key="inspect",
            title="Inspect workspace",
            agent_role="architect",
            sequence=0,
            status="ready",
        )

        response = self.client.post(
            f"/api/ai/agent/runs/{run.id}/tasks/{task.id}/tools/inspect_workspace/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task_status"], "ready")
        self.assertEqual(response.data["run_status"], "approved")
        self.assertEqual(response.data["execution_state"], "read_only_analysis_no_workspace_mutations")
        self.assertTrue(response.data["analysis_only"])
        execute_tool.assert_called_once_with("inspect_workspace", self.workspace)
        task.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(task.status, "ready")
        self.assertEqual(run.status, "approved")
        self.assertIsNone(run.finished_at)
        self.assertTrue(AIMessage.objects.filter(
            conversation=conversation,
            role="tool",
            context__agent_run_id=run.id,
            context__agent_task_id=task.id,
            context__read_only=True,
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="agent_task.readonly_tool_invoked",
            target_id=str(task.id),
            metadata__read_only=True,
        ).exists())

        replay = self.client.post(
            f"/api/ai/agent/runs/{run.id}/tasks/{task.id}/tools/inspect_workspace/",
            {},
            format="json",
        )
        self.assertEqual(replay.status_code, 409)
        execute_tool.assert_called_once()

    @patch("api.agent_tools.execute_readonly_tool")
    def test_specialist_cannot_invoke_tool_outside_role_capabilities(self, execute_tool):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Frontend inspection",
            status="approved",
            idempotency_key="readonly-role-guard-001",
            workspace_revision=self.workspace.revision,
        )
        task = AgentTask.objects.create(
            run=run,
            key="frontend",
            title="Inspect frontend",
            agent_role="frontend",
            sequence=0,
            status="ready",
        )

        response = self.client.post(
            f"/api/ai/agent/runs/{run.id}/tasks/{task.id}/tools/review_security/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        execute_tool.assert_not_called()
        task.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(task.status, "ready")
        self.assertEqual(run.status, "approved")

    @patch("api.agent_tools.execute_readonly_tool")
    def test_specialist_tool_rejects_workspace_revision_changed_after_approval(self, execute_tool):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Inspect stable revision",
            status="approved",
            idempotency_key="readonly-stale-revision-01",
            workspace_revision=self.workspace.revision,
        )
        task = AgentTask.objects.create(
            run=run,
            key="inspect",
            title="Inspect workspace",
            agent_role="architect",
            sequence=0,
            status="ready",
        )
        self.workspace.files = {"main.py": "new_revision = True"}
        self.workspace.save(update_fields=["files"])

        response = self.client.post(
            f"/api/ai/agent/runs/{run.id}/tasks/{task.id}/tools/inspect_workspace/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "stale_workspace")
        execute_tool.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "ready")

    @patch("api.agent_tools.execute_readonly_tool")
    def test_other_user_cannot_invoke_private_run_task_tool(self, execute_tool):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Private workspace inspection",
            status="approved",
            idempotency_key="private-tool-run-0001",
            workspace_revision=self.workspace.revision,
        )
        task = AgentTask.objects.create(
            run=run,
            key="inspect",
            title="Inspect private workspace",
            agent_role="architect",
            sequence=0,
            status="ready",
        )

        self.client.force_authenticate(self.other)
        response = self.client.post(
            f"/api/ai/agent/runs/{run.id}/tasks/{task.id}/tools/inspect_workspace/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        execute_tool.assert_not_called()
        task.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(task.status, "ready")
        self.assertEqual(run.status, "approved")

    @patch("api.agent_tools.execute_readonly_tool")
    def test_blocked_task_cannot_run_before_dependencies_succeed(self, execute_tool):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Respect task dependency ordering",
            status="approved",
            idempotency_key="blocked-tool-run-0001",
            workspace_revision=self.workspace.revision,
        )
        prerequisite = AgentTask.objects.create(
            run=run,
            key="prerequisite",
            title="Complete prerequisite",
            agent_role="architect",
            sequence=0,
            status="ready",
        )
        blocked = AgentTask.objects.create(
            run=run,
            key="dependent",
            title="Run dependent analysis",
            agent_role="architect",
            sequence=1,
            status="blocked",
        )
        blocked.depends_on.add(prerequisite)

        response = self.client.post(
            f"/api/ai/agent/runs/{run.id}/tasks/{blocked.id}/tools/inspect_workspace/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        execute_tool.assert_not_called()
        blocked.refresh_from_db()
        prerequisite.refresh_from_db()
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(prerequisite.status, "ready")

    def test_runner_sync_status_is_owner_scoped_and_does_not_expose_worker_errors(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync",
            payload={"workspace_id": self.workspace.id, "owner_id": self.user.id, "requested_revision": 2},
            status="failed",
            attempts=3,
            max_attempts=3,
            error="private runner host, token=must-not-leak",
            result={"workspace_id": self.workspace.id, "revision": 2, "coalesced": False},
            idempotency_key="agent-runner-status-0001",
        )
        response = self.client.get(f"/api/ai/agent/sync-jobs/{job.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(response.data["workspace_id"], self.workspace.id)
        self.assertEqual(response.data["revision"], 2)
        self.assertEqual(response.data["attempts"], 3)
        self.assertIn("inspect Runner health", response.data["error"])
        self.assertNotIn("must-not-leak", json.dumps(response.data))
        self.assertNotIn("private runner host", json.dumps(response.data))
        self.assertNotIn("locked_by", response.data)
        self.assertNotIn("result", response.data)

    def test_other_user_cannot_read_runner_sync_status(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync",
            payload={"workspace_id": self.workspace.id, "owner_id": self.user.id, "requested_revision": 2},
            idempotency_key="agent-runner-status-private-001",
        )
        self.client.force_authenticate(self.other)
        response = self.client.get(f"/api/ai/agent/sync-jobs/{job.id}/")
        self.assertEqual(response.status_code, 404)

    def test_runner_sync_status_rejects_other_job_kinds(self):
        job = BackgroundJob.objects.create(
            kind="email",
            payload={"owner_id": self.user.id},
            idempotency_key="agent-runner-status-wrong-kind",
        )
        response = self.client.get(f"/api/ai/agent/sync-jobs/{job.id}/")
        self.assertEqual(response.status_code, 404)

    def test_cancel_stops_queued_run_and_job(self):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Cancel me",
            idempotency_key="cancel-run-0001",
            workspace_revision=self.workspace.revision,
        )
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload={"run_id": run.id},
            idempotency_key=f"agent-plan:{run.id}",
        )
        response = self.client.post(f"/api/ai/agent/runs/{run.id}/cancel/")
        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(run.status, "canceled")
        self.assertEqual(job.status, "canceled")

    @patch("api.ai_engine._call_provider")
    def test_worker_persists_plan_and_dependency_graph_without_mutating_workspace(self, provider):
        conversation = AIConversation.objects.create(owner=self.user, title="Agent plan")
        AIMessage.objects.create(
            conversation=conversation,
            role="user",
            content="Please inspect this. API_KEY=history-secret-value-123",
        )
        AIMessage.objects.create(
            conversation=conversation,
            role="assistant",
            content="context " * 2500,
        )
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            conversation=conversation,
            goal="Review the code and propose tests. password=synthetic-test-value" + ("x" * 9000),
            idempotency_key="worker-plan-0001",
            workspace_revision=self.workspace.revision,
        )
        provider.return_value = (
            json.dumps({
                "summary": "Review then verify",
                "risks": ["Potential regression"],
                "acceptance_criteria": ["Regression tests pass"],
                "tasks": [
                    {"id": "inspect", "title": "Inspect existing code", "description": "Review source", "depends_on": [], "acceptance_criteria": ["The relevant source modules and dependencies are identified."]},
                    {"id": "tests", "title": "Design regression tests", "description": "Cover edge cases", "agent_role": "testing", "depends_on": ["inspect"], "acceptance_criteria": ["Regression tests cover the stated edge cases and pass."]},
                ],
            }),
            None,
        )
        original_files = dict(self.workspace.files)
        result = build_agent_plan(run.id)

        run.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(run.status, "awaiting_approval")
        self.assertIsNone(run.finished_at)
        self.assertEqual(run.tasks.count(), 2)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="agent_run.plan_ready",
            target_id=str(run.id),
        ).exists())
        self.assertEqual(run.tasks.get(key="inspect").agent_role, "architect")
        self.assertEqual(run.tasks.get(key="tests").agent_role, "testing")
        self.assertEqual(run.tasks.get(key="inspect").acceptance_criteria, ["The relevant source modules and dependencies are identified."])
        self.assertEqual(run.tasks.get(key="tests").acceptance_criteria, ["Regression tests cover the stated edge cases and pass."])
        payload = self.client.get(f"/api/ai/agent/runs/{run.id}/").data
        testing_task = next(item for item in payload["tasks"] if item["key"] == "tests")
        self.assertEqual(testing_task["acceptance_criteria"], ["Regression tests cover the stated edge cases and pass."])
        self.assertIn("inspect_workspace", testing_task["allowed_capabilities"])
        self.assertNotIn("design_tests", testing_task["allowed_capabilities"])
        self.assertNotIn("shell", testing_task["allowed_capabilities"])
        self.assertNotIn("workspace_write", testing_task["allowed_capabilities"])
        self.assertNotIn("deploy", testing_task["allowed_capabilities"])
        self.assertEqual(run.tasks.get(key="inspect").status, "ready")
        self.assertEqual(run.tasks.get(key="tests").status, "blocked")
        self.assertEqual(
            list(run.tasks.get(key="tests").depends_on.values_list("key", flat=True)),
            ["inspect"],
        )
        self.assertEqual(self.workspace.files, original_files)
        sent_prompt = provider.call_args.args[0]
        self.assertNotIn("synthetic-test-value", sent_prompt)
        self.assertIn("[REDACTED]", sent_prompt)
        provider_goal = sent_prompt.split("GOAL:\n", 1)[1]
        self.assertLessEqual(len(provider_goal), 8000)
        self.assertEqual(len(run.goal), len("Review the code and propose tests. password=synthetic-test-value") + 9000)
        sent_context = provider.call_args.args[1]
        sent_history = provider.call_args.args[2]
        serialized_history = json.dumps(sent_history)
        self.assertNotIn("history-secret-value-123", serialized_history)
        self.assertIn("[REDACTED]", serialized_history)
        self.assertLessEqual(sum(len(item["content"]) for item in sent_history), 12000)
        self.assertTrue(all(len(item["content"]) <= 3000 for item in sent_history))
        self.assertNotIn(".env", sent_context["workspace"]["file_samples"])
        self.assertNotIn(".npmrc", sent_context["workspace"]["file_samples"])
        self.assertNotIn(".aws/credentials", sent_context["workspace"]["file_samples"])
        self.assertNotIn("keys/private.pem", sent_context["workspace"]["file_samples"])
        self.assertNotIn("inline-secret-value-123", sent_context["workspace"]["file_samples"]["main.py"])
        self.assertNotIn("boundary-secret-value-123456", sent_context["workspace"]["file_samples"]["boundary.py"])
        self.assertNotIn("boundary-s", sent_context["workspace"]["file_samples"]["boundary.py"])
        self.assertIn("[REDACTED]", sent_context["workspace"]["file_samples"]["boundary.py"])
        self.assertTrue(sent_context["workspace"]["secrets_excluded"])

    @patch("api.ai_engine._call_provider")
    def test_duplicate_delivery_does_not_call_provider_while_run_is_planning(self, provider):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Do not duplicate provider work",
            status="planning",
            idempotency_key="duplicate-plan-delivery-01",
            workspace_revision=self.workspace.revision,
        )

        result = build_agent_plan(run.id)

        self.assertEqual(result["status"], "planning")
        provider.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, "planning")

    @patch("api.ai_engine._call_provider")
    def test_failed_run_is_terminal_for_duplicate_delivery(self, provider):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Do not revive failed run",
            status="failed",
            idempotency_key="failed-plan-delivery-01",
            workspace_revision=self.workspace.revision,
        )

        result = build_agent_plan(run.id)

        self.assertEqual(result["status"], "failed")
        provider.assert_not_called()

    @patch("api.views._workspace_write_allowed", return_value=False)
    @patch("api.ai_engine._call_provider")
    def test_worker_rechecks_workspace_permission_before_provider_call(self, provider, _permission):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Inspect workspace",
            idempotency_key="revoked-access-0001",
            workspace_revision=self.workspace.revision,
        )
        with self.assertRaisesRegex(RuntimeError, "access was revoked"):
            build_agent_plan(run.id)
        provider.assert_not_called()

    def test_approval_rejects_stale_workspace_revision(self):
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Approve after review",
            status="awaiting_approval",
            idempotency_key="stale-approve-0001",
            workspace_revision=self.workspace.revision,
        )
        self.workspace.files = {"main.py": "changed = True"}
        self.workspace.save(update_fields=["files"])
        response = self.client.post(f"/api/ai/agent/runs/{run.id}/approve/")
        self.assertEqual(response.status_code, 409)
        run.refresh_from_db()
        self.assertEqual(run.status, "awaiting_approval")

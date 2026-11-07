import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .agent_runtime import _redact_sensitive_content, _validate_plan, build_agent_plan
from .models import AgentRun, AIConversation, AuditLog, BackgroundJob, CodeWorkspace


class AgentContextRedactionTests(TestCase):
    def test_redacts_named_credentials_and_common_provider_tokens(self):
        source = (
            'API_KEY="inline-secret-value-123"\\n'
            'password=super-secret-password-value\\n'
            'aws_access_key=AKIAABCDEFGHIJKLMNOP\\n'
            'github=ghp_abcdefghijklmnopqrstuvwxyz123456\\n'
            'openai=sk-proj-abcdefghijklmnopqrstuvwxyz123456\\n'
            'slack=xoxb-1234567890-abcdefghijk\\n'
        )
        redacted = _redact_sensitive_content(source)
        self.assertNotIn("inline-secret-value-123", redacted)
        self.assertNotIn("super-secret-password-value", redacted)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", redacted)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("sk-proj-abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("xoxb-1234567890-abcdefghijk", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redacts_pem_private_key_blocks(self):
        source = (
            "-----BEGIN PRIVATE KEY-----\\n"
            "MIIE-private-material-that-must-not-leak\\n"
            "-----END PRIVATE KEY-----"
        )
        redacted = _redact_sensitive_content(source)
        self.assertNotIn("MIIE-private-material-that-must-not-leak", redacted)
        self.assertIn("[REDACTED_PRIVATE_KEY]", redacted)


class AgentPlanValidationTests(TestCase):
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

    def test_accepts_topological_dependency_graph(self):
        plan = _validate_plan({
            "summary": "Safe plan",
            "risks": [],
            "acceptance_criteria": ["Tests pass"],
            "tasks": [
                {"id": "inspect", "title": "Inspect repository", "depends_on": []},
                {"id": "test", "title": "Add regression tests", "depends_on": ["inspect"]},
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
            files={"main.py": "def main():\n    return 1\n", ".env": "SECRET=must-not-leak", ".npmrc": "//registry/token=hidden", ".aws/credentials": "aws_secret_access_key=hidden", "keys/private.pem": "PRIVATE KEY"},
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
        run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            conversation=conversation,
            goal="Review the code and propose tests",
            idempotency_key="worker-plan-0001",
            workspace_revision=self.workspace.revision,
        )
        provider.return_value = (
            json.dumps({
                "summary": "Review then verify",
                "risks": ["Potential regression"],
                "acceptance_criteria": ["Regression tests pass"],
                "tasks": [
                    {"id": "inspect", "title": "Inspect existing code", "description": "Review source", "depends_on": []},
                    {"id": "tests", "title": "Design regression tests", "description": "Cover edge cases", "agent_role": "testing", "depends_on": ["inspect"]},
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
        self.assertEqual(run.tasks.count(), 2)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="agent_run.plan_ready",
            target_id=str(run.id),
        ).exists())
        self.assertEqual(run.tasks.get(key="inspect").agent_role, "architect")
        self.assertEqual(run.tasks.get(key="tests").agent_role, "testing")
        payload = self.client.get(f"/api/ai/agent/runs/{run.id}/").data
        testing_task = next(item for item in payload["tasks"] if item["key"] == "tests")
        self.assertIn("design_tests", testing_task["allowed_capabilities"])
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
        sent_context = provider.call_args.args[1]
        self.assertNotIn(".env", sent_context["workspace"]["file_samples"])
        self.assertNotIn(".npmrc", sent_context["workspace"]["file_samples"])
        self.assertNotIn(".aws/credentials", sent_context["workspace"]["file_samples"])
        self.assertNotIn("keys/private.pem", sent_context["workspace"]["file_samples"])
        self.assertNotIn("inline-secret-value-123", sent_context["workspace"]["file_samples"]["main.py"])
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

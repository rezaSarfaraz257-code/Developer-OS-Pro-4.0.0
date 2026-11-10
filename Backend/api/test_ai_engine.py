import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core import signing
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .ai_engine import _agent_parse_patch, _provider_config, _sanitize_provider_context, _build_input
from .models import AIConversation, AIMessage, AuditLog, CodeWorkspace


class AIEngineUnitTests(TestCase):
    def test_patch_contract_rejects_traversal(self):
        proposal = {
            "type": "patch_proposal",
            "changes": [{
                "path": "../settings.py",
                "operation": "modify",
                "reason": "bad",
                "content": "x",
            }],
        }
        self.assertIsNone(_agent_parse_patch(json.dumps(proposal)))

    def test_patch_contract_rejects_reserved_and_ambiguous_paths(self):
        invalid_paths = [
            ".git/config",
            "node_modules/pkg/index.js",
            "src//main.py",
            "src/./main.py",
            "src/../main.py",
            "C:/workspace/main.py",
            "src\\main.py",
            "/etc/passwd",
        ]
        for path in invalid_paths:
            with self.subTest(path=path):
                proposal = {
                    "type": "patch_proposal",
                    "changes": [{
                        "path": path,
                        "operation": "create",
                        "reason": "test",
                        "content": "safe = True\n",
                    }],
                }
                self.assertIsNone(_agent_parse_patch(json.dumps(proposal)))

    def test_patch_contract_rejects_secret_and_private_key_paths(self):
        invalid_paths = [
            ".env",
            ".env.production",
            ".npmrc",
            ".aws/credentials",
            ".ssh/id_ed25519",
            "certs/server.pem",
            "secrets/service.key",
            "private/identity.p12",
        ]
        for path in invalid_paths:
            with self.subTest(path=path):
                proposal = {
                    "type": "patch_proposal",
                    "changes": [{
                        "path": path,
                        "operation": "create",
                        "reason": "must never write credential material",
                        "content": "placeholder",
                    }],
                }
                self.assertIsNone(_agent_parse_patch(json.dumps(proposal)))

    def test_patch_contract_allows_documented_environment_templates(self):
        for path in (".env.example", ".env.sample", ".env.template"):
            with self.subTest(path=path):
                proposal = {
                    "type": "patch_proposal",
                    "changes": [{
                        "path": path,
                        "operation": "create",
                        "reason": "document required configuration",
                        "content": "API_URL=https://example.invalid",
                    }],
                }
                self.assertIsNotNone(_agent_parse_patch(json.dumps(proposal)))

    def test_patch_contract_rejects_duplicate_case_insensitive_paths(self):
        proposal = {
            "type": "patch_proposal",
            "changes": [
                {"path": "src/Main.py", "operation": "create", "content": "one"},
                {"path": "src/main.py", "operation": "create", "content": "two"},
            ],
        }
        self.assertIsNone(_agent_parse_patch(json.dumps(proposal)))

    def test_patch_contract_rejects_oversized_single_file(self):
        proposal = {
            "type": "patch_proposal",
            "changes": [{
                "path": "main.py",
                "operation": "create",
                "content": "x" * 1_000_001,
            }],
        }
        self.assertIsNone(_agent_parse_patch(json.dumps(proposal)))

    def test_patch_contract_accepts_complete_safe_change(self):
        proposal = {
            "type": "patch_proposal",
            "summary": "fix",
            "root_cause": "test",
            "confidence": 0.9,
            "affected_files": ["main.py"],
            "changes": [{
                "path": "main.py",
                "operation": "modify",
                "reason": "test",
                "content": "print('ok')\n",
            }],
            "test_plan": ["python -m py_compile main.py"],
            "risks": [],
            "requires_approval": True,
        }
        parsed = _agent_parse_patch(json.dumps(proposal))
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed["requires_approval"])

    @override_settings()
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "AI_MODEL": "test-model", "AI_API_PROTOCOL": "responses"}, clear=False)
    def test_provider_config_resolves_at_request_time(self):
        cfg = _provider_config()
        self.assertEqual(cfg["key"], "test-key")
        self.assertEqual(cfg["model"], "test-model")
        self.assertEqual(cfg["protocol"], "responses")


    def test_provider_context_excludes_secret_paths_and_redacts_inline_credentials(self):
        context = {
            "workspace": {
                "id": 7,
                "files": {
                    ".env": "API_KEY=must-never-leave-server",
                    ".env.production": "PASSWORD=another-secret",
                    ".ssh/id_ed25519": "PRIVATE KEY MATERIAL",
                    "certs/server.pem": "-----BEGIN PRIVATE KEY-----\\nprivate-material\\n-----END PRIVATE KEY-----",
                    "secrets/service.json": "API_KEY=directory-secret-value",
                    "private/identity.json": "PRIVATE_KEY=private-directory-secret",
                    "src/main.py": (
                        "API_KEY='inline-secret-value-123456'\\n"
                        "DATABASE_URL=postgres://app:database-password-value@db.example.test/app\\n"
                        "github='ghp_abcdefghijklmnopqrstuvwxyz123456'\\n"
                        "aws='AKIAABCDEFGHIJKLMNOP'\\n"
                    ),
                    ".env.example": "API_URL=https://example.invalid\\n",
                },
            },
            "agent_evidence": {
                "workspace": {
                    "files": {
                        ".env": "NESTED_SECRET=must-not-leave",
                        "src/worker.py": "password='nested-password-value'\\n",
                    },
                },
            },
        }

        sanitized = _sanitize_provider_context(context)
        serialized = json.dumps(sanitized)

        self.assertNotIn("must-never-leave-server", serialized)
        self.assertNotIn("another-secret", serialized)
        self.assertNotIn("PRIVATE KEY MATERIAL", serialized)
        self.assertNotIn("private-material", serialized)
        self.assertNotIn("directory-secret-value", serialized)
        self.assertNotIn("private-directory-secret", serialized)
        self.assertNotIn("inline-secret-value-123456", serialized)
        self.assertNotIn("database-password-value", serialized)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", serialized)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", serialized)
        self.assertNotIn("NESTED_SECRET=must-not-leave", serialized)
        self.assertNotIn("nested-password-value", serialized)
        self.assertIn(".env.example", serialized)
        self.assertIn("[REDACTED]", serialized)
        # Sanitization must not mutate the durable workspace snapshot in memory.
        self.assertEqual(context["workspace"]["files"][".env"], "API_KEY=must-never-leave-server")

    def test_provider_input_uses_sanitized_context_and_redacted_history(self):
        context = {
            "workspace": {
                "files": {
                    ".env": "API_KEY=hidden-provider-secret",
                    "app.py": "TOKEN='source-token-value-123456'\\n",
                },
            },
        }
        history = [{"role": "user", "content": "password='history-password-value'"}]
        payload = json.dumps(_build_input("Inspect safely API_KEY='current-request-secret-123456'", context, history), ensure_ascii=False)

        self.assertNotIn("current-request-secret-123456", payload)
        self.assertNotIn("hidden-provider-secret", payload)
        self.assertNotIn("source-token-value-123456", payload)
        self.assertNotIn("history-password-value", payload)
        self.assertIn("[REDACTED]", payload)


    def test_provider_context_redacts_prefixed_secret_keys_and_excludes_secret_like_paths(self):
        context = {
            "workspace": {
                "files": {
                    "config/prod.env": "API_KEY=path-secret-one",
                    ".envrc": "TOKEN=path-secret-two",
                    "config/secrets-prod.yaml": "PASSWORD=path-secret-three",
                    "config/prod-credentials.json": "TOKEN=path-secret-four",
                    "src/settings.py": (
                        "AWS_SECRET_ACCESS_KEY='aws-secret-value-123456789'\\n"
                        "AWS_SESSION_TOKEN='aws-session-value-123456789'\\n"
                        "GITHUB_TOKEN='github-env-token-123456789'\\n"
                        "STRIPE_SECRET_KEY='stripe-env-secret-123456789'\\n"
                    ),
                },
            },
        }

        serialized = json.dumps(_sanitize_provider_context(context))
        for secret in (
            "path-secret-one",
            "path-secret-two",
            "path-secret-three",
            "path-secret-four",
            "aws-secret-value-123456789",
            "aws-session-value-123456789",
            "github-env-token-123456789",
            "stripe-env-secret-123456789",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, serialized)
        self.assertIn("src/settings.py", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_provider_context_enforces_depth_and_metadata_node_budgets(self):
        deep = {"value": "deep-context-secret"}
        cursor = deep
        for _ in range(40):
            cursor["child"] = {}
            cursor = cursor["child"]
        cursor["value"] = "must-not-reach-provider"

        context = {
            "metadata": {f"field_{index}": f"value_{index}" for index in range(1_200)},
            "nested": deep,
        }
        context["metadata"]["credential_note"] = "AWS_SECRET_ACCESS_KEY=metadata-secret-value-123456"
        sanitized = _sanitize_provider_context(context)
        serialized = json.dumps(sanitized)

        self.assertLessEqual(len(sanitized["metadata"]), 1_000)
        self.assertNotIn("metadata-secret-value-123456", serialized)
        self.assertNotIn("must-not-reach-provider", serialized)
        self.assertIn("TRUNCATED_CONTEXT_DEPTH", serialized)

class AIHealthAPITests(TestCase):
    def setUp(self):
        # DRF throttle cache is external to TestCase database rollback.
        cache.clear()
        self.user = User.objects.create_user(username="ai-test", password="StrongPassword123!")
        self.client = APIClient()

    def test_health_does_not_expose_key(self):
        response = self.client.get("/api/ai/health/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        serialized = json.dumps(body)
        self.assertNotIn("test-key", serialized)
        self.assertIn("provider", body["details"])

    @patch("api.ai_engine._provider_config", return_value={"key": "secret-key", "base": "https://api.example.com/v1", "model": "test-model", "protocol": "responses"})
    def test_authenticated_health_hides_secret(self, _config):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/ai/health/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secret-key", json.dumps(response.json()))


class AIAgentApprovalTests(TestCase):
    def setUp(self):
        # DRF throttle cache is external to TestCase database rollback.
        cache.clear()
        self.user = User.objects.create_user(username="agent-test", password="StrongPassword123!")
        self.client = APIClient()

    def test_agent_requires_authentication(self):
        response = self.client.post("/api/ai/agent/", {"message": "inspect", "workspace": 1}, format="json")
        self.assertEqual(response.status_code, 401)

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "free"))
    @patch("api.ai_engine._call_provider")
    @patch("api.ai_engine._agent_evidence", return_value={"workspace": None, "repository": {"connected": False}})
    def test_agent_persists_conversation_and_reuses_bounded_history(
        self, _evidence, provider, _usage
    ):
        self.client.force_authenticate(self.user)
        provider.side_effect = [("First agent response", None), ("Second agent response", None)]

        first = self.client.post(
            "/api/ai/agent/",
            {"message": "Inspect my project"},
            format="json",
        )
        self.assertEqual(first.status_code, 200)
        conversation_id = first.data["conversation"]["id"]
        self.assertTrue(conversation_id)
        self.assertEqual(first.data["message"]["content"], "First agent response")
        self.assertEqual(first.data["persistence"], "saved")
        self.assertEqual(
            AIMessage.objects.filter(conversation_id=conversation_id).count(),
            2,
        )

        second = self.client.post(
            "/api/ai/agent/",
            {"message": "Continue the analysis", "conversation": conversation_id},
            format="json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["conversation"]["id"], conversation_id)
        self.assertEqual(second.data["message"]["content"], "Second agent response")
        history = provider.call_args_list[1].args[2]
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[0]["content"], "Inspect my project")
        self.assertEqual(AIMessage.objects.filter(conversation_id=conversation_id).count(), 4)
        self.assertEqual(_usage.call_count, 2)

    @patch("api.views._consume_usage", return_value=(False, 0, 0, "free"))
    @patch("api.ai_engine._call_provider")
    def test_agent_does_not_call_provider_when_usage_reservation_fails(self, provider, _usage):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/ai/agent/",
            {"message": "Run a costly agent analysis"},
            format="json",
        )
        self.assertEqual(response.status_code, 429)
        provider.assert_not_called()

    def test_agent_rejects_foreign_workspace_before_creating_conversation(self):
        workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Private workspace",
            files={"main.py": "print('private')\\n"},
        )
        other = User.objects.create_user(username="agent-workspace-other", password="StrongPassword123!")
        self.client.force_authenticate(other)
        response = self.client.post(
            "/api/ai/agent/",
            {"message": "Inspect this workspace", "workspace": workspace.id},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AIConversation.objects.filter(owner=other).exists())

    def test_agent_conversation_is_private_to_its_owner(self):
        from .models import AIConversation

        conversation = AIConversation.objects.create(owner=self.user, title="Private agent history")
        other = User.objects.create_user(username="agent-history-other", password="StrongPassword123!")
        self.client.force_authenticate(other)
        response = self.client.post(
            "/api/ai/agent/",
            {"message": "Read this history", "conversation": conversation.id},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_agent_apply_rejects_missing_token(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/ai/agent/apply/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_agent_apply_rejects_tampered_token(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/ai/agent/apply/", {"approval_token": "tampered"}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("api.views._runner_request", return_value=({"synced": True}, None))
    @patch("api.ai_engine._agent_runner_evidence", return_value={"status": "ok"})
    @patch("api.ai_engine._agent_diagnostics_evidence", return_value={"status": "clean"})
    def test_agent_apply_is_revision_locked_and_approval_cannot_be_replayed(
        self, _diagnostics, _runner_evidence, _runner_request
    ):
        self.client.force_authenticate(self.user)
        workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Agent approval test",
            files={"main.py": "print('before')\n"},
        )
        proposal = {
            "type": "patch_proposal",
            "changes": [{
                "path": "main.py",
                "operation": "modify",
                "reason": "test approved patch",
                "content": "print('after')\n",
            }],
        }
        token = signing.dumps({
            "user_id": self.user.id,
            "workspace_id": workspace.id,
            "workspace_revision": workspace.revision,
            "proposal": proposal,
        })

        response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": token},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        workspace.refresh_from_db()
        self.assertEqual(workspace.files["main.py"], "print('after')\n")
        self.assertEqual(response.data["status"], "applied_sync_pending")
        self.assertTrue(response.data["sync"]["queued"])
        self.assertEqual(response.data["sync"]["job_id"], response.data["verification"]["job_id"])
        from .models import BackgroundJob
        sync_job = BackgroundJob.objects.get(
            kind="ai_agent_runner_sync",
            idempotency_key=f"agent-runner-sync:{workspace.id}:{workspace.revision}",
        )
        self.assertEqual(sync_job.status, "queued")
        self.assertEqual(sync_job.payload["requested_revision"], workspace.revision)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user,
            action="ai_agent.patch_applied",
            target_id=str(workspace.id),
            metadata__previous_revision=1,
            metadata__revision=workspace.revision,
        ).exists())

        replay = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": token},
            format="json",
        )
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(workspace.revision, response.data["workspace"]["revision"])

    def test_agent_rollback_records_audit_event_without_file_contents(self):
        self.client.force_authenticate(self.user)
        workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Rollback audit test",
            files={"main.py": "print('current')\\n"},
        )
        response = self.client.post(
            "/api/ai/agent/rollback/",
            {
                "workspace": workspace.id,
                "revision": workspace.revision,
                "files": {"main.py": "print('restored')\\n"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        event = AuditLog.objects.get(
            user=self.user,
            action="ai_agent.workspace_rolled_back",
            target_id=str(workspace.id),
        )
        self.assertEqual(event.metadata["restored_file_count"], 1)
        self.assertNotIn("files", event.metadata)
        self.assertNotIn("print('restored')", json.dumps(event.metadata))

    def test_agent_rollback_rejects_unsafe_paths(self):
        self.client.force_authenticate(self.user)
        workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Rollback path test",
            files={"main.py": "print('safe')\\n"},
        )
        for path in ("../secret.txt", ".git/config", "node_modules/pkg.js", "src\\\\main.py"):
            with self.subTest(path=path):
                response = self.client.post(
                    "/api/ai/agent/rollback/",
                    {"workspace": workspace.id, "revision": workspace.revision, "files": {path: "bad"}},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

    @patch("api.views._workspace_write_allowed", return_value=False)
    def test_agent_rollback_rejects_read_only_workspace_access(self, _write_allowed):
        self.client.force_authenticate(self.user)
        workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Rollback permission test",
            files={"main.py": "print('current')\\n"},
        )
        response = self.client.post(
            "/api/ai/agent/rollback/",
            {
                "workspace": workspace.id,
                "revision": workspace.revision,
                "files": {"main.py": "print('old')\\n"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        workspace.refresh_from_db()
        self.assertEqual(workspace.files["main.py"], "print('current')\\n")

    def test_agent_apply_token_cannot_be_used_by_another_user(self):
        other = User.objects.create_user(username="agent-other", password="StrongPassword123!")
        self.client.force_authenticate(other)
        token = signing.dumps({
            "user_id": self.user.id,
            "workspace_id": 999,
            "workspace_revision": 1,
            "proposal": {
                "type": "patch_proposal",
                "changes": [{
                    "path": "main.py",
                    "operation": "create",
                    "reason": "test",
                    "content": "print('safe')",
                }],
            },
        })
        response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": token},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

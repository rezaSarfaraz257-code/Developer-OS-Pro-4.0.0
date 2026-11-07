import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from api.ai_engine import _extract_responses_text, _provider_config, _compact_context, _call_provider


class IntelligenceEngineTests(SimpleTestCase):
    def test_provider_defaults_to_openai_responses(self):
        with patch.dict(os.environ, {}, clear=True):
            config = _provider_config()
        self.assertEqual(config["base"], "https://api.openai.com/v1")
        self.assertEqual(config["model"], "gpt-6-luna")
        self.assertEqual(config["protocol"], "responses")

    def test_selected_workspace_files_respect_global_context_limit(self):
        context = {
            "workspace": {
                "id": 7,
                "name": "large workspace",
                "files": {f"src/file_{index}.py": "x" * 12000 for index in range(20)},
            },
            "projects": [],
            "tasks": [],
        }
        bounded = _compact_context(context)
        self.assertLessEqual(len(__import__("json").dumps(bounded, ensure_ascii=False)), 60000)
        self.assertTrue(bounded["context_truncated"])
        self.assertEqual(bounded["workspace"]["id"], 7)

    def test_workspace_inventory_files_respect_global_context_limit(self):
        context = {
            "workspaces": [
                {"id": index, "files": {f"file_{item}.py": "y" * 12000 for item in range(12)}}
                for index in range(5)
            ],
        }
        bounded = _compact_context(context)
        self.assertLessEqual(len(__import__("json").dumps(bounded, ensure_ascii=False)), 60000)

    def test_zero_history_limit_disables_history(self):
        from api import ai_engine
        with patch.object(ai_engine, "MAX_HISTORY_MESSAGES", 0):
            items = ai_engine._build_input("current", {}, [{"role": "user", "content": "old"}])
        self.assertEqual(len(items), 1)

    def test_history_is_bounded_by_total_character_budget(self):
        from api.ai_engine import _build_input
        history = [
            {"role": "user", "content": "u" * 20000},
            {"role": "assistant", "content": "a" * 20000},
            {"role": "user", "content": "latest"},
        ]
        items = _build_input("current", {}, history)
        bounded_history = items[:-1]
        self.assertLessEqual(sum(len(item["content"]) for item in bounded_history), 24000)
        self.assertEqual(bounded_history[-1]["content"], "latest")
        self.assertEqual(items[-1]["role"], "user")

    def test_extracts_responses_output_text(self):
        payload = {"output": [{"content": [{"text": "diagnosis"}]}, {"content": [{"text": "fix"}]}]}
        self.assertEqual(_extract_responses_text(payload), "diagnosis\nfix")

    def test_context_is_bounded(self):
        context = {"notes": [{"content": "x" * 5000} for _ in range(50)]}
        bounded = _compact_context(context)
        self.assertLessEqual(len(__import__("json").dumps(bounded, ensure_ascii=False)), 60000)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "AI_MODEL": "gpt-5", "AI_API_URL": "https://api.openai.com/v1"}, clear=False)
    @patch("api.ai_engine.requests.post")
    def test_responses_provider_returns_generated_text(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"output_text": "Use the diagnostics endpoint, then rerun the tests."}
        post.return_value = response

        answer, error = _call_provider("debug this", {"projects": []}, [])
        self.assertEqual(error, None)
        self.assertIn("diagnostics endpoint", answer)
        post.assert_called_once()
        self.assertTrue(post.call_args.kwargs["url"].endswith("/responses"))


    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "AI_MODEL": "gpt-6-luna", "AI_API_URL": "https://api.openai.com/v1"}, clear=False)
    @patch("api.ai_engine.requests.post")
    def test_gpt_6_luna_uses_responses_without_temperature(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"output_text": "ok"}
        post.return_value = response
        answer, error = _call_provider("hello", {"projects": []}, [])
        self.assertEqual(error, None)
        self.assertEqual(answer, "ok")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertNotIn("temperature", payload)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "AI_MODEL": "gpt-6-luna", "AI_API_URL": "https://api.openai.com/v1"}, clear=False)
    @patch("api.ai_engine.requests.post")
    def test_provider_http_errors_are_structured(self, post):
        import requests
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"error": {"message": "invalid api key"}}
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        post.return_value = response
        answer, provider_error = _call_provider("hello", {"projects": []}, [])
        self.assertEqual(answer, "")
        self.assertIn('"code": "AI_AUTH_ERROR"', provider_error)
        self.assertIn('"provider_status": 401', provider_error)

class AgentEvidenceTests(SimpleTestCase):
    def test_agent_context_excludes_secret_files_and_redacts_inline_credentials(self):
        from api.ai_engine import _agent_context_file_samples

        samples = _agent_context_file_samples({
            "src/main.py": "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\\nprint('safe')",
            ".env": "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
            "config/.env.production": "DATABASE_URL=postgres://user:password123@db.example/app",
            "config/credentials.production": "API_KEY=another-secret-value",
            "config/secrets.production.json": "private-secret",
            ".aws/credentials": "aws_secret_access_key=example-secret-value",
            "certs/server.pem": "-----BEGIN PRIVATE KEY-----\\nprivate-material\\n-----END PRIVATE KEY-----",
            "docs/readme.md": "Public documentation",
        })

        serialized = __import__("json").dumps(samples)
        self.assertIn("src/main.py", samples)
        self.assertIn("docs/readme.md", samples)
        self.assertNotIn("sk-proj-abcdefghijklmnopqrstuvwxyz1234567890", serialized)
        self.assertNotIn("password123", serialized)
        self.assertNotIn("example-secret-value", serialized)
        self.assertNotIn("another-secret-value", serialized)
        self.assertNotIn("private-secret", serialized)
        self.assertNotIn("private-material", serialized)
        self.assertNotIn(".env", serialized)
        self.assertIn("[REDACTED", serialized)

    def test_agent_context_file_samples_enforce_file_and_total_character_budgets(self):
        from api.ai_engine import _agent_context_file_samples

        samples = _agent_context_file_samples({
            f"src/file_{index}.py": "x" * 5000 for index in range(120)
        })

        self.assertLessEqual(len(samples), 120)
        self.assertLessEqual(sum(len(value) for value in samples.values()), 24000)
        self.assertTrue(all(len(value) <= 1800 for value in samples.values()))

    def test_agent_evidence_uses_secret_filtered_workspace_samples(self):
        from api.ai_engine import _agent_evidence
        from unittest.mock import patch

        user = object()
        request = Mock()
        request.data = {"workspace": 7}
        request.user = user
        workspace = Mock(
            id=7,
            name="Evidence workspace",
            project_id=None,
            language="python",
            framework="",
            runtime="python",
            package_manager="",
            active_file="main.py",
            revision=3,
            files={
                "main.py": "API_KEY=abcdefghijklmno\\nprint('safe')",
                ".env": "API_KEY=must-not-leak",
            },
        )
        with (
            patch("api.views._workspace_for_user", return_value=workspace),
            patch("api.ai_engine._agent_repository_evidence", return_value={"connected": False}),
            patch("api.ai_engine._agent_diagnostics_evidence", return_value={"status": "skipped"}),
            patch("api.ai_engine._agent_runner_evidence", return_value={"status": "skipped"}),
        ):
            evidence = _agent_evidence(request)

        serialized = __import__("json").dumps(evidence)
        self.assertTrue(evidence["workspace"]["secrets_excluded"])
        self.assertIn("main.py", evidence["workspace"]["files"])
        self.assertNotIn(".env", evidence["workspace"]["files"])
        self.assertNotIn("abcdefghijklmno", serialized)
        self.assertNotIn("must-not-leak", serialized)

    def test_agent_evidence_is_bounded(self):
        from api.ai_engine import _compact_context
        evidence = {"workspace": {"files": {"main.py": "x" * 70000}}, "repository": {}, "diagnostics": {}, "runner": {}}
        bounded = _compact_context(evidence)
        self.assertLessEqual(len(__import__("json").dumps(bounded, ensure_ascii=False)), 60000)

    def test_agent_instructions_require_structured_evidence(self):
        from api.ai_engine import _agent_instructions
        text = _agent_instructions()
        self.assertIn("affected_files", text)
        self.assertIn("Never claim files were changed", text)


class SafeWorkspaceContextTests(SimpleTestCase):
    def test_safe_workspace_context_degrades_on_context_exception(self):
        from unittest.mock import patch
        from api import ai_engine

        with patch("api.views._workspace_context", side_effect=RuntimeError("context failure")):
            context = ai_engine._safe_workspace_context(object())

        self.assertEqual(context["projects"], [])
        self.assertEqual(context["workspaces"], [])
        self.assertEqual(context["context_error_type"], "RuntimeError")

    def test_compact_context_accepts_workspace_inventory(self):
        from api import ai_engine

        context = {
            "workspaces": [{
                "id": 1,
                "files": {"app.py": "print('ok')"}
            }],
            "projects": [],
            "tasks": [],
            "notes": [],
            "snippets": [],
        }
        compact = ai_engine._compact_context(context)
        self.assertEqual(compact["workspaces"][0]["files"]["app.py"], "print('ok')")


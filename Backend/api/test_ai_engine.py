import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .ai_engine import _agent_parse_patch, _provider_config


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


class AIHealthAPITests(TestCase):
    def setUp(self):
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
        self.user = User.objects.create_user(username="agent-test", password="StrongPassword123!")
        self.client = APIClient()

    def test_agent_requires_authentication(self):
        response = self.client.post("/api/ai/agent/", {"message": "inspect", "workspace": 1}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_agent_apply_rejects_missing_token(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/ai/agent/apply/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_agent_apply_rejects_tampered_token(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/ai/agent/apply/", {"approval_token": "tampered"}, format="json")
        self.assertEqual(response.status_code, 400)

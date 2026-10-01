import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from api.ai_engine import _extract_responses_text, _provider_config, _compact_context, _call_provider


class IntelligenceEngineTests(SimpleTestCase):
    def test_provider_defaults_to_openai_responses(self):
        with patch.dict(os.environ, {}, clear=True):
            config = _provider_config()
        self.assertEqual(config["base"], "https://api.openai.com/v1")
        self.assertEqual(config["model"], "gpt-5")
        self.assertEqual(config["protocol"], "responses")

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

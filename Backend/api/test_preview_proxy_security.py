from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from api.models import CodeWorkspace
from api.views import IDE_PREVIEW_MAX_RESPONSE_BYTES, _safe_preview_proxy_path


class PreviewProxySecurityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="preview-proxy-security",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Preview proxy security",
            files={"index.html": "<h1>safe</h1>"},
            active_file="index.html",
        )

    def _upstream(self, *, status=200, headers=None, chunks=(b"ok",)):
        response = Mock()
        response.status_code = status
        response.headers = headers or {"Content-Type": "text/html"}
        response.iter_content.return_value = iter(chunks)
        response.close = Mock()
        return response

    def test_preview_path_rejects_traversal_and_ambiguous_paths(self):
        for path in ("../exec", "nested/../../exec", r"nested\\..\\exec", "bad\npath", "%2e%2e/exec"):
            with self.subTest(path=path):
                self.assertIsNone(_safe_preview_proxy_path(path))

    def test_preview_path_quotes_url_delimiters(self):
        self.assertEqual(_safe_preview_proxy_path("assets/a b.js"), "assets/a%20b.js")
        self.assertEqual(_safe_preview_proxy_path("a%3Fb"), "a%3Fb")

    @patch("api.views.RUNNER_URL", "https://runner.example")
    @patch("api.views.RUNNER_TOKEN", "runner-test-token")
    @patch("api.views.requests.get")
    def test_preview_proxy_disables_redirects(self, get):
        get.return_value = self._upstream(status=302, headers={"Location": "http://169.254.169.254/"})
        response = self.client.get(f"/api/ide/workspaces/{self.workspace.pk}/preview/index.html")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "preview_redirect_blocked")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["stream"])
        get.return_value.close.assert_called_once()

    @patch("api.views.RUNNER_URL", "https://runner.example")
    @patch("api.views.RUNNER_TOKEN", "runner-test-token")
    @patch("api.views.requests.get")
    def test_preview_proxy_rejects_declared_oversized_response(self, get):
        upstream = self._upstream(headers={"Content-Length": str(IDE_PREVIEW_MAX_RESPONSE_BYTES + 1)})
        get.return_value = upstream
        response = self.client.get(f"/api/ide/workspaces/{self.workspace.pk}/preview/index.html")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "preview_response_too_large")
        upstream.iter_content.assert_not_called()
        upstream.close.assert_called_once()

    @patch("api.views.RUNNER_URL", "https://runner.example")
    @patch("api.views.RUNNER_TOKEN", "runner-test-token")
    @patch("api.views.requests.get")
    def test_preview_proxy_bounds_streamed_body_without_content_length(self, get):
        upstream = self._upstream(headers={"Content-Type": "application/octet-stream"}, chunks=(
            b"x" * IDE_PREVIEW_MAX_RESPONSE_BYTES,
            b"y",
        ))
        get.return_value = upstream
        response = self.client.get(f"/api/ide/workspaces/{self.workspace.pk}/preview/index.html")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "preview_response_too_large")
        upstream.close.assert_called_once()

    @patch("api.views.RUNNER_URL", "https://runner.example/")
    @patch("api.views.RUNNER_TOKEN", "runner-test-token")
    @patch("api.views.requests.get")
    def test_preview_proxy_returns_bounded_content_with_security_headers(self, get):
        upstream = self._upstream(headers={"Content-Type": "text/html"}, chunks=(b"<h1>", b"ok</h1>"))
        get.return_value = upstream
        response = self.client.get(f"/api/ide/workspaces/{self.workspace.pk}/preview/index.html")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"<h1>ok</h1>")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(get.call_args.args[0], f"https://runner.example/preview/{self.workspace.pk}/index.html")
        upstream.close.assert_called_once()

    @patch("api.views.RUNNER_URL", "")
    @patch("api.views.RUNNER_TOKEN", "")
    @patch("api.views.requests.get")
    def test_preview_proxy_fails_closed_when_runner_is_not_configured(self, get):
        response = self.client.get(f"/api/ide/workspaces/{self.workspace.pk}/preview/index.html")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "preview_not_configured")
        get.assert_not_called()

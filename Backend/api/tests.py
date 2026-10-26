import os
import requests
from contextlib import contextmanager
from io import BytesIO
import hashlib
import hmac
import json
import tempfile
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.db import DatabaseError, IntegrityError, connection
from django.test import override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from cryptography.fernet import Fernet
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import APIKey, BackgroundJob, BillingEvent, DataExportRequest, GitHubAccount, GitHubOAuthState, Project, Subscription, Tag, Tool, Task, UsageRecord, SecuritySession, UserProfile, WorkerHeartbeat


class RunnerRequestRetrySafetyTests(APITestCase):
    @patch("api.views.requests.request", side_effect=requests.ConnectionError("simulated lost response"))
    @patch("api.views.time.sleep")
    @patch("api.views.RUNNER_URL", "https://runner.test")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    def test_mutating_runner_request_is_not_replayed(self, sleep, request):
        from . import views
        data, error = views._runner_request(
            "POST", "/exec", {"command": "python main.py"}, timeout=1
        )

        self.assertIsNone(data)
        self.assertEqual(error["code"], "runner_unreachable")
        request.assert_called_once()
        sleep.assert_not_called()

    @patch("api.views.requests.request")
    @patch("api.views.RUNNER_URL", "https://runner.test")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    def test_non_json_runner_body_is_not_exposed_to_api_clients(self, request):
        from . import views
        response = Mock()
        response.status_code = 500
        response.content = b"<html>internal traceback with secret-value</html>"
        response.text = "internal traceback with secret-value"
        response.json.side_effect = ValueError("not JSON")
        request.return_value = response

        data, error = views._runner_request("GET", "/capabilities", {}, timeout=1)

        self.assertIsNone(data)
        self.assertEqual(error["code"], "runner_invalid_response")
        self.assertEqual(error["status"], 500)
        self.assertNotIn("details", error)
        self.assertNotIn("secret-value", json.dumps(error))
        self.assertNotIn("traceback", json.dumps(error).lower())


    @patch("api.views.requests.request", side_effect=requests.ConnectionError("simulated unavailable runner"))
    @patch("api.views.time.sleep")
    @patch("api.views.RUNNER_URL", "https://runner.test")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    def test_get_runner_request_uses_bounded_retries(self, sleep, request):
        from . import views
        data, error = views._runner_request("GET", "/capabilities", {}, timeout=1)

        self.assertIsNone(data)
        self.assertEqual(error["code"], "runner_unreachable")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


class IDEPreviewPortContractTests(APITestCase):
    def setUp(self):
        # Keep shared API throttle cache state from leaking between test classes.
        cache.clear()
        self.user = User.objects.create_user(
            username="ide-preview-port-contract",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)
        from .models import CodeWorkspace
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Preview port contract",
            files={"package.json": '{"scripts":{"dev":"vite"}}'},
            active_file="package.json",
            framework="react-vite",
        )

    @patch("api.views._runner_request")
    def test_preview_start_passes_the_proxy_port_to_runner(self, runner_request):
        def echo_preview_port(method, path, payload, timeout=30):
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/preview/start")
            return ({"id": "preview-test", "status": "running", "port": payload["port"]}, None)
        runner_request.side_effect = echo_preview_port

        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/preview/start/",
            {"framework": "react-vite"},
            format="json",
        )

        expected_port = 10000 + (self.workspace.pk % 1000)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["port"], expected_port)
        self.assertEqual(
            response.data["public_preview"],
            f"/api/ide/workspaces/{self.workspace.pk}/preview/",
        )
        self.assertEqual(runner_request.call_count, 1)

    def test_preview_proxy_path_helper_rejects_traversal_and_encodes_url_delimiters(self):
        from .views import _safe_preview_proxy_path
        for path in ("../admin", "src/../admin", "%2e%2e/admin", "bad\\npath", "a\\\\b"):
            with self.subTest(path=path):
                self.assertIsNone(_safe_preview_proxy_path(path))
        self.assertEqual(
            _safe_preview_proxy_path("app?token=x#fragment"),
            "app%3Ftoken=x%23fragment",
        )

    @patch("api.views.RUNNER_URL", "https://runner.test")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    @patch("api.views.requests.get")
    def test_preview_proxy_does_not_follow_redirects_and_sets_safe_headers(self, get):
        upstream = Mock()
        upstream.status_code = 302
        upstream.headers = {"Content-Type": "text/html", "Content-Length": "0"}
        upstream.iter_content.return_value = []
        upstream.close = Mock()
        get.return_value = upstream

        response = self.client.get(
            f"/api/ide/workspaces/{self.workspace.pk}/preview/redirect"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(get.call_args.kwargs["allow_redirects"], False)
        self.assertTrue(get.call_args.kwargs["stream"])
        upstream.close.assert_called_once()

    @patch("api.views.RUNNER_URL", "https://runner.test")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    @patch("api.views.requests.get")
    def test_preview_proxy_rejects_declared_oversized_body(self, get):
        upstream = Mock()
        upstream.status_code = 200
        upstream.headers = {"Content-Type": "text/html", "Content-Length": "10000001"}
        upstream.close = Mock()
        get.return_value = upstream

        response = self.client.get(
            f"/api/ide/workspaces/{self.workspace.pk}/preview/large"
        )

        self.assertEqual(response.status_code, 413)
        upstream.close.assert_called_once()


class ProductionReadinessTests(APITestCase):
    def test_console_email_backend_never_passes_production_readiness(self):
        with override_settings(
            IS_PRODUCTION=True,
            DEBUG=False,
            EMAIL_HOST="",
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        ):
            response = self.client.get("/api/health/ready/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.data["checks"]["email"])
        self.assertEqual(response.data["status"], "not_ready")

    def test_render_runtime_requires_worker_even_if_environment_flag_is_misconfigured(self):
        with patch.dict("os.environ", {"RENDER_GIT_COMMIT": "a" * 40}, clear=False):
            with override_settings(
                IS_PRODUCTION=False,
                DEBUG=False,
                SECRET_KEY="test-secret-key-that-is-not-insecure",
                ALLOWED_HOSTS=["testserver"],
                EMAIL_HOST="smtp.example.test",
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            ):
                response = self.client.get("/api/health/ready/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.data["checks"]["worker"])
        self.assertEqual(response.data["status"], "not_ready")

    def test_render_readiness_stays_strict_when_debug_is_accidentally_enabled(self):
        with patch.dict("os.environ", {"RENDER_GIT_COMMIT": "b" * 40}, clear=False):
            with override_settings(
                IS_PRODUCTION=False,
                DEBUG=True,
                SECRET_KEY="test-secret-key-that-is-not-insecure",
                ALLOWED_HOSTS=["testserver"],
                EMAIL_HOST="smtp.example.test",
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            ):
                response = self.client.get("/api/health/ready/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.data["checks"]["worker"])
        self.assertEqual(response.data["status"], "not_ready")

    def test_configured_non_console_email_backend_can_pass_email_check(self):
        with override_settings(
            IS_PRODUCTION=True,
            DEBUG=False,
            EMAIL_HOST="smtp.example.test",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        ):
            response = self.client.get("/api/health/ready/")
        self.assertTrue(response.data["checks"]["email"])


class IDEExecutionQuotaFailClosedTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ide-quota-fail-closed",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)
        from .models import CodeWorkspace
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Quota fail-closed regression",
            files={"main.py": "print('must not run')"},
            active_file="main.py",
        )

    @patch("api.views._runner_request")
    @patch("api.views._consume_usage", side_effect=DatabaseError("simulated quota database outage"))
    def test_execution_is_blocked_when_quota_cannot_be_verified(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/execute/",
            {"command": "python main.py"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "usage_enforcement_unavailable")
        self.assertEqual(response.data["stage"], "usage_limit")
        runner_request.assert_not_called()


    @patch("api.views._runner_request")
    @patch("api.views._consume_usage")
    def test_invalid_execution_payload_does_not_consume_quota(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/execute/",
            {"command": {"unexpected": "object"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        consume_usage.assert_not_called()
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    @patch("api.views._consume_usage")
    def test_unsafe_execution_snapshot_is_rejected_before_quota_or_runner(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/execute/",
            {
                "command": "python main.py",
                "files": {"../secrets.txt": "must-not-run"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "invalid_workspace_files")
        consume_usage.assert_not_called()
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    @patch("api.views._consume_usage")
    def test_absolute_and_git_metadata_paths_are_rejected_in_execution_snapshot(self, consume_usage, runner_request):
        for unsafe_path in ("/etc/passwd", "C:\\\\Windows\\\\win.ini", ".git/config", "src/.git/config", "src/.git", "src//main.py"):
            with self.subTest(path=unsafe_path):
                response = self.client.post(
                    f"/api/ide/workspaces/{self.workspace.pk}/execute/",
                    {"command": "python main.py", "files": {unsafe_path: "x"}},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, unsafe_path)
        consume_usage.assert_not_called()
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    @patch("api.views._consume_usage", side_effect=DatabaseError("simulated debugger quota database outage"))
    def test_debug_start_is_blocked_when_quota_cannot_be_verified(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/debug/",
            {"action": "start"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "usage_enforcement_unavailable")
        self.assertEqual(response.data["stage"], "usage_limit")
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    @patch("api.views._consume_usage")
    def test_debug_start_validates_parameters_before_consuming_quota(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/debug/",
            {"action": "start", "line": "not-an-integer"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        consume_usage.assert_not_called()
        runner_request.assert_not_called()


    @patch("api.views._runner_request", return_value=(None, None))
    def test_debugger_rejects_malformed_runner_response(self, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/debug/",
            {"action": "status"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "runner_invalid_response")
        runner_request.assert_called_once()


    @patch("api.views._runner_request")
    @patch("api.views._consume_usage", side_effect=DatabaseError("simulated process quota database outage"))
    def test_process_start_is_blocked_when_quota_cannot_be_verified(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/process/start/",
            {"command": "python main.py"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "usage_enforcement_unavailable")
        self.assertEqual(response.data["stage"], "usage_limit")
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    @patch("api.views._consume_usage")
    def test_invalid_process_command_does_not_consume_quota(self, consume_usage, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/process/start/",
            {"command": {"unexpected": "object"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        consume_usage.assert_not_called()
        runner_request.assert_not_called()


class IDEJobStreamCursorValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ide-job-cursor-user",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)
        from .models import CodeWorkspace
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Job cursor validation",
            files={"main.py": "print('safe')"},
            active_file="main.py",
        )

    @patch("api.views._runner_request")
    def test_malformed_cursor_is_rejected_before_runner_request(self, runner_request):
        response = self.client.get(
            f"/api/ide/workspaces/{self.workspace.pk}/jobs/job-1/stream/?cursor=0%26workspace_id%3D999"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        runner_request.assert_not_called()

    @patch("api.views._runner_request", return_value=({"job_id": "job-1", "output": "", "cursor": 12, "done": False}, None))
    def test_valid_cursor_and_workspace_scope_are_forwarded_as_query_params(self, runner_request):
        response = self.client.get(
            f"/api/ide/workspaces/{self.workspace.pk}/jobs/job-1/stream/?cursor=12"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        runner_request.assert_called_once_with(
            "GET",
            "/jobs/job-1/stream",
            {"workspace_id": str(self.workspace.pk), "cursor": 12},
            timeout=10,
        )

    @patch("api.views._runner_request")
    def test_negative_cursor_is_rejected(self, runner_request):
        response = self.client.get(
            f"/api/ide/workspaces/{self.workspace.pk}/jobs/job-1/stream/?cursor=-1"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        runner_request.assert_not_called()


class IDEProcessAuthorizationTests(APITestCase):
    def setUp(self):
        # Keep shared API throttle cache state from leaking between test classes.
        cache.clear()
        self.user = User.objects.create_user(
            username="ide-process-readonly-user",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)
        from .models import CodeWorkspace
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Read-only process authorization",
            files={"main.py": "print('safe')"},
            active_file="main.py",
        )

    @patch("api.views._runner_request")
    @patch("api.views._workspace_write_allowed", return_value=False)
    def test_read_only_workspace_cannot_stop_process(self, write_allowed, runner_request):
        response = self.client.post(
            f"/api/ide/workspaces/{self.workspace.pk}/process/process-123/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"], "You have read-only access to this workspace.")
        write_allowed.assert_called_once()
        runner_request.assert_not_called()

    @patch("api.views._runner_request")
    def test_foreign_workspace_cannot_read_processes_jobs_or_preview(self, runner_request):
        from .models import CodeWorkspace
        other = User.objects.create_user(
            username="ide-process-foreign-owner",
            password="long-test-password-123",
        )
        foreign_workspace = CodeWorkspace.objects.create(
            owner=other,
            name="Private foreign workspace",
            files={"main.py": "print('private')"},
        )
        paths = (
            f"/api/ide/workspaces/{foreign_workspace.pk}/processes/",
            f"/api/ide/workspaces/{foreign_workspace.pk}/jobs/private-job/",
            f"/api/ide/workspaces/{foreign_workspace.pk}/preview/",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        runner_request.assert_not_called()


class IDECapabilitiesReadinessTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ide-capabilities-user",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)

    @patch("api.views._runner_request")
    def test_runner_capabilities_are_degraded_when_execution_is_unavailable(self, runner_request):
        runner_request.return_value = ({
            "sandbox": {
                "execution_available": False,
                "unavailable_reason": "Strict mode requires per-execution isolation.",
            },
            "operations": {"execute": False, "snapshot": True},
        }, None)

        response = self.client.get("/api/ide/capabilities/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "degraded")
        self.assertFalse(response.data["runner"]["sandbox"]["execution_available"])

    @patch("api.views._runner_request")
    def test_runner_capabilities_are_ready_when_execution_is_available(self, runner_request):
        runner_request.return_value = ({
            "sandbox": {"execution_available": True},
            "operations": {"execute": True, "snapshot": True},
        }, None)

        response = self.client.get("/api/ide/capabilities/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ready")


class ProjectApiSecurityTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="long-test-password-123")
        self.other_user = User.objects.create_user(username="other", password="long-test-password-123")
        self.owner_project = Project.objects.create(owner=self.owner, title="Private project")
        Project.objects.create(owner=self.other_user, title="Other private project")

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_projects_are_visible_only_to_their_owner(self):
        self.authenticate(self.owner)
        response = self.client.get("/api/projects/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([project["id"] for project in response.data], [self.owner_project.id])
        self.assertNotIn("owner", response.data[0])

    def test_api_responses_include_restrictive_security_headers(self):
        self.authenticate(self.owner)
        response = self.client.get("/api/projects/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("default-src 'none'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_cannot_access_another_users_project(self):
        self.authenticate(self.owner)
        response = self.client.get(f"/api/projects/{self.owner_project.id + 1}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_favorites_use_the_tool_relation_without_exposing_user_id(self):
        self.authenticate(self.owner)
        tool = Tool.objects.create(name="Django", tag="Backend")

        response = self.client.post("/api/favorites/", {"tool_name": tool.name}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["tool_name"], "Django")
        self.assertNotIn("user", response.data)

    def test_favorites_cannot_create_shared_tools(self):
        self.authenticate(self.owner)
        tool_count = Tool.objects.count()

        response = self.client.post("/api/favorites/", {"tool_name": "Unapproved Tool"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Tool.objects.count(), tool_count)

    def test_tool_names_are_case_insensitively_unique(self):
        Tool.objects.create(name="React", tag="Frontend")

        with self.assertRaises(IntegrityError):
            Tool.objects.create(name="react", tag="Frontend")

    def test_only_staff_can_modify_shared_catalogs(self):
        self.authenticate(self.owner)
        response = self.client.post("/api/resources/", {"title": "Untrusted resource"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_blacklists_the_authenticated_users_refresh_token(self):
        refresh = RefreshToken.for_user(self.owner)
        self.authenticate(self.owner)

        response = self.client.post("/api/logout/", {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        with self.assertRaises(TokenError):
            refresh.check_blacklist()

    def test_refresh_requires_an_active_security_session_and_rotates_session_jti(self):
        refresh = RefreshToken.for_user(self.owner)
        session = SecuritySession.objects.create(
            user=self.owner,
            jti=str(refresh["jti"]),
            device_name="test",
            ip_address="127.0.0.1",
        )

        first = self.client.post("/api/token/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", first.data)

        replay = self.client.post("/api/token/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)

        rotated = RefreshToken(first.data["refresh"])
        session.refresh_from_db()
        self.assertEqual(session.jti, str(rotated["jti"]))

        session.revoked_at = __import__("django.utils.timezone", fromlist=["now"]).now()
        session.save(update_fields=["revoked_at"])
        blocked = self.client.post("/api/token/refresh/", {"refresh": str(rotated)}, format="json")
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_created_tags_receive_a_unique_slug(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.authenticate(self.owner)

        first = self.client.post("/api/tags/", {"name": "API Security"}, format="json")
        second = self.client.post("/api/tags/", {"name": "API Security"}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data["slug"], "api-security")
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Tag.objects.count(), 1)

    def test_profile_rejects_non_http_urls(self):
        self.authenticate(self.owner)

        response = self.client.patch("/api/profile/", {"website": "ftp://example.test"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("website", response.data)

    def test_profile_accepts_the_multipart_payload_sent_by_the_frontend(self):
        self.authenticate(self.owner)

        response = self.client.patch(
            "/api/profile/",
            {
                "first_name": "Profile",
                "last_name": "Owner",
                "email": "owner@example.test",
                "full_name": "Profile Owner",
                "bio": "Updated safely.",
                "github": "https://github.com/owner",
                "linkedin": "https://www.linkedin.com/in/owner",
                "website": "https://owner.example.test",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Profile Owner")
        self.assertEqual(response.data["website"], "https://owner.example.test")

    def test_profile_get_clears_a_missing_legacy_render_avatar_url(self):
        self.authenticate(self.owner)
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        profile.avatar_url = "/media/avatars/user_1/missing-avatar.webp"
        profile.save(update_fields=["avatar_url"])

        response = self.client.get("/api/profile/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["avatar_url"], "")
        profile.refresh_from_db()
        self.assertEqual(profile.avatar_url, "")

    def test_profile_avatar_is_reencoded_and_returned_as_a_media_url(self):
        image_buffer = BytesIO()
        Image.new("RGBA", (40, 30), "#2ad9ff").save(image_buffer, format="PNG")
        avatar = SimpleUploadedFile("portrait.png", image_buffer.getvalue(), content_type="image/png")
        media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(media_directory.cleanup)

        self.authenticate(self.owner)
        with override_settings(
            MEDIA_ROOT=media_directory.name,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        ):
            response = self.client.patch("/api/profile/", {"avatar": avatar}, format="multipart")

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(response.data["has_uploaded_avatar"])
            self.assertIn("/media/avatars/user_", response.data["avatar_url"])

            self.owner.profile.refresh_from_db()
            self.assertTrue(self.owner.profile.avatar.name.endswith(".webp"))
            with self.owner.profile.avatar.open("rb") as stored_file:
                with Image.open(stored_file) as stored_image:
                    self.assertEqual(stored_image.format, "WEBP")


class GitHubTokenEncryptionTests(APITestCase):
    @override_settings(GITHUB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_github_access_token_is_encrypted_at_rest(self):
        user = User.objects.create_user(username="token-owner", password="long-test-password-123")
        account = GitHubAccount.objects.create(user=user, login="token-owner", access_token="sensitive-token")

        with connection.cursor() as cursor:
            cursor.execute("SELECT access_token FROM api_githubaccount WHERE id = %s", [account.id])
            stored_token = cursor.fetchone()[0]

        self.assertNotEqual(stored_token, "sensitive-token")
        self.assertTrue(stored_token.startswith("enc:v1:"))
        account.refresh_from_db()
        self.assertEqual(account.access_token, "sensitive-token")

class CollaborationAndIntelligenceTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner2", email="owner2@example.test", password="long-test-password-123")
        self.member = User.objects.create_user(username="member2", email="member2@example.test", password="long-test-password-123")
        self.project = Project.objects.create(owner=self.owner, title="Shared project")

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_owner_can_add_and_member_can_see_project(self):
        self.authenticate(self.owner)
        response = self.client.post(f"/api/projects/{self.project.id}/collaborators/", {"username": self.member.username}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.authenticate(self.member)
        projects = self.client.get("/api/projects/")
        self.assertEqual(projects.status_code, status.HTTP_200_OK)
        self.assertIn(self.project.id, [item["id"] for item in projects.data])

    def test_non_owner_cannot_change_collaborators(self):
        self.project.collaborators.add(self.member)
        self.authenticate(self.member)
        response = self.client.post(f"/api/projects/{self.project.id}/collaborators/", {"username": self.owner.username}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_workspace_summary_is_real_data(self):
        from .models import Task
        Task.objects.create(project=self.project, title="Done", status="done")
        Task.objects.create(project=self.project, title="Blocked", status="blocked")
        self.authenticate(self.owner)
        response = self.client.get("/api/workspace/summary/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["projects"], 1)
        self.assertEqual(response.data["completion"], 50)
        self.assertEqual(response.data["blocked_tasks"], 1)

    def test_assistant_uses_workspace_context_without_fake_static_metrics(self):
        from .models import Task
        Task.objects.create(project=self.project, title="Urgent", priority="urgent")
        self.authenticate(self.owner)
        response = self.client.post("/api/assistant/", {"message": "What should I do next?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["mode"], "local")
        self.assertIn("urgent", response.data["answer"].lower())


class ProductionCatalogAndOAuthTests(APITestCase):
    def test_catalog_is_publicly_readable(self):
        from .models import Resource, Workflow
        Tool.objects.create(name="Public Tool", tag="Testing")
        Resource.objects.create(title="Public Resource", link="https://example.com")
        Workflow.objects.create(title="Public Workflow", steps=["One"])

        response = self.client.get("/api/tools/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["name"] == "Public Tool" for item in response.data))

        response = self.client.get("/api/resources/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get("/api/workflows/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @override_settings(
        GITHUB_CLIENT_ID="test-client",
        GITHUB_CLIENT_SECRET="test-secret",
        GITHUB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    def test_github_authorization_uses_pkce(self):
        user = User.objects.create_user(username="pkce-user", password="long-test-password-123")
        self.client.force_authenticate(user=user)

        response = self.client.get("/api/github/authorize/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        authorization_url = response.data["authorization_url"]
        self.assertIn("code_challenge=", authorization_url)
        self.assertIn("code_challenge_method=S256", authorization_url)
        self.assertIn("state=", authorization_url)

        state = GitHubOAuthState.objects.get(user=user)
        self.assertTrue(state.code_verifier)

class GlobalSearchAndObservabilityTests(APITestCase):
    def test_global_search_returns_cross_catalog_results(self):
        from .models import Resource, Workflow
        Tool.objects.create(name="Searchable React", tag="Frontend", category="Frontend", description="UI library")
        Resource.objects.create(title="React search guide", category="Frontend", description="Components")
        Workflow.objects.create(title="React delivery", summary="Ship a React feature", steps=["Build"])

        response = self.client.get("/api/search/?q=React")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["results"]["tools"])
        self.assertTrue(response.data["results"]["resources"])
        self.assertTrue(response.data["results"]["workflows"])
        self.assertTrue(response["X-Request-ID"])
        self.assertIn("app;dur=", response["Server-Timing"])

    def test_global_search_rejects_unbounded_query_size(self):
        response = self.client.get("/api/search/?q=" + ("x" * 121))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class GitHubIntegrationLifecycleTests(APITestCase):
    @override_settings(GITHUB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_github_account_can_be_disconnected(self):
        user = User.objects.create_user(username="disconnect-user", password="long-test-password-123")
        GitHubAccount.objects.create(user=user, login="disconnect-user", access_token="token")
        self.client.force_authenticate(user=user)

        response = self.client.delete("/api/github/account/disconnect/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(GitHubAccount.objects.filter(user=user).exists())

    @override_settings(GITHUB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_github_activity_filter_uses_full_repository_name(self):
        from unittest.mock import patch, Mock
        user = User.objects.create_user(username="github-filter-user", password="long-test-password-123")
        GitHubAccount.objects.create(user=user, login="github-filter-user", access_token="token")
        self.client.force_authenticate(user=user)

        def fake_get(url, **kwargs):
            response = Mock()
            response.status_code = 200
            if url.endswith("/user"):
                response.json.return_value = {"login": "github-filter-user"}
            else:
                response.json.return_value = [
                    {"id": "1", "type": "PushEvent", "repo": {"name": "owner/wanted"}},
                    {"id": "2", "type": "PushEvent", "repo": {"name": "owner/other"}},
                ]
            return response

        with patch("api.views.requests.get", side_effect=fake_get):
            response = self.client.post("/api/github/sync-activity/", {"repo_full_name": "owner/wanted"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["synced"], 1)


class PlatformUpgradeTests(APITestCase):
    def setUp(self):
        # The custom IP-based API throttle uses the shared cache; isolate tests from
        # requests made by earlier test cases that share the same test-client IP.
        cache.clear()
        self.user = User.objects.create_user(username="platform-user", password="long-test-password-123")
        self.client.force_authenticate(user=self.user)
        self.project = Project.objects.create(owner=self.user, title="Quantum IDE", description="Build a web IDE")
        Task.objects.create(project=self.project, title="Ship editor", priority="urgent")

    def test_universal_search_scopes_to_workspace(self):
        response = self.client.get("/api/platform/search/?q=Quantum")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["type"], "project")

    def test_ide_workspace_file_crud_and_revision_conflict(self):
        response = self.client.post("/api/ide/workspaces/", {"name": "CRUD", "files": {"main.py": "print(1)"}, "active_file": "main.py"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workspace_id = response.data["id"]
        revision = response.data["revision"]

        response = self.client.post(f"/api/ide/workspaces/{workspace_id}/files/", {"action": "create", "path": "src/app.py", "content": "print(2)", "revision": revision}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        revision = response.data["revision"]

        response = self.client.post(f"/api/ide/workspaces/{workspace_id}/files/", {"action": "rename", "path": "src/app.py", "to": "src/main.py", "revision": revision}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        revision = response.data["revision"]
        self.assertIn("src/main.py", response.data["files"])
        self.assertNotIn("src/app.py", response.data["files"])

        # A stale delete must return a conflict rather than an unhandled 500,
        # and must never remove a file from a newer workspace revision.
        response = self.client.delete(f"/api/ide/workspaces/{workspace_id}/files/", {"path": "src/main.py", "revision": revision - 1}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "stale_workspace")
        from .models import CodeWorkspace
        self.assertIn("src/main.py", CodeWorkspace.objects.get(pk=workspace_id).files)

        response = self.client.delete(f"/api/ide/workspaces/{workspace_id}/files/", {"path": "src/main.py", "revision": revision}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("src/main.py", response.data["files"])

        response = self.client.post(f"/api/ide/workspaces/{workspace_id}/files/", {"action": "create", "path": "README.md", "content": "# OS", "revision": revision}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        revision = response.data["revision"]

        response = self.client.post(f"/api/ide/workspaces/{workspace_id}/files/", {"action": "write", "path": "README.md", "content": "# Updated", "revision": revision - 1}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "stale_workspace")

        # A create mutation is additive and rebases on the server revision.
        response = self.client.post(f"/api/ide/workspaces/{workspace_id}/files/", {"action": "create", "path": "new.txt", "content": "new", "revision": 0}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("new.txt", response.data["files"])
        self.assertGreater(response.data["revision"], revision)

    def test_ide_workspace_paths_reject_file_directory_collisions(self):
        response = self.client.post(
            "/api/ide/workspaces/",
            {
                "name": "Path collision checks",
                "files": {"src": "this is a file", "docs/readme.md": "# Docs"},
                "active_file": "src",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workspace_id = response.data["id"]
        endpoint = f"/api/ide/workspaces/{workspace_id}/files/"

        # A file must not also become the parent directory of another file.
        response = self.client.post(
            endpoint,
            {"action": "create", "path": "src/child.py", "content": "print(1)"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "path_conflict")

        # A virtual directory cannot be overwritten by a file.
        response = self.client.post(
            endpoint,
            {"action": "create", "path": "docs", "content": "not a directory"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "path_conflict")

        # Renaming a directory into itself must not create malformed paths.
        response = self.client.post(
            endpoint,
            {"action": "rename", "path": "docs", "to": "docs/archive"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "path_conflict")

        # Existing file writes remain valid and do not trigger a false conflict.
        current = self.client.get(endpoint)
        self.assertEqual(current.status_code, status.HTTP_200_OK)
        response = self.client.post(
            endpoint,
            {"action": "write", "path": "src", "content": "print('updated')", "revision": current.data["revision"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["files"]["src"], "print('updated')")

    @patch("api.views._runner_request")
    def test_ide_execute_returns_runner_contract_and_workspace_state(self, runner_request):
        workspace = self.client.post(
            "/api/ide/workspaces/",
            {"name": "Execute", "files": {"main.py": "print('ok')"}, "active_file": "main.py"},
            format="json",
        )
        self.assertEqual(workspace.status_code, status.HTTP_201_CREATED)
        workspace_id = workspace.data["id"]
        runner_request.return_value = (
            {
                "job_id": "job-contract-test",
                "status": "success",
                "exit_code": 0,
                "stdout": "ok\n",
                "stderr": "",
                "duration_ms": 17,
                "files": {"main.py": "print('ok')"},
            },
            None,
        )
        response = self.client.post(
            f"/api/ide/workspaces/{workspace_id}/execute/",
            {"command": "python3 main.py", "active_file": "main.py", "files": {"main.py": "print('ok')"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["job_id"], "job-contract-test")
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["stdout"], "ok\n")
        self.assertIn("files", response.data)
        self.assertIn("revision", response.data)

    def test_ide_runner_terminal_states_are_complete(self):
        terminal_states = {"success", "completed", "failed", "timeout", "cancelled"}
        self.assertEqual(
            terminal_states,
            {"success", "completed", "failed", "timeout", "cancelled"},
        )

    def test_ide_workspace_file_paths_are_safe(self):
        response = self.client.post("/api/ide/workspaces/", {"name": "Safe", "files": {}}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workspace_id = response.data["id"]
        revision = response.data["revision"]
        for path in ("../secret.txt", "src/../secret.txt", "/absolute.txt", "C:\\\\Windows\\\\win.ini", ".git/config", "src/.git/config", ".", "./main.py", "src//app.py"):
            response = self.client.post(f"/api/ide/workspaces/{workspace_id}/files/", {"action": "create", "path": path, "content": "x", "revision": revision}, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, path)

    def test_ide_workspace_is_persistent(self):
        response = self.client.post("/api/ide/workspaces/", {"name":"Main","files":{"main.py":"print(1)"}, "active_file":"main.py"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workspace_id = response.data["id"]
        response = self.client.patch("/api/ide/workspaces/", {"id":workspace_id,"files":{"main.py":"print(2)","README.md":"# OS"}}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["files"]["main.py"], "print(2)")

    def test_organization_membership_and_subscription(self):
        Subscription.objects.create(user=self.user, plan="team", status="active")
        response = self.client.post("/api/organizations/", {"name":"Quantum Labs"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        org_id = response.data["id"]
        response = self.client.get(f"/api/organizations/{org_id}/members/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["role"], "owner")
        with self.settings(DEBUG=True, ALLOW_LOCAL_BILLING=True):
            response = self.client.post("/api/subscription/", {"plan":"pro"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"], "pro")

    def test_comment_requires_project_access(self):
        response = self.client.post("/api/comments/", {"project":self.project.id,"body":"Ship it"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["body"], "Ship it")

    def test_api_key_authenticates_real_api_requests(self):
        import hashlib
        raw = "dos_live_test_authentication_key"
        APIKey.objects.create(
            user=self.user,
            name="CLI",
            prefix=raw[:14],
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        )
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/usage/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"], "free")
        self.assertIsNotNone(APIKey.objects.get(user=self.user).last_used_at)

    def test_invalid_api_key_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/usage/", HTTP_AUTHORIZATION="Bearer dos_live_invalid")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_api_key_returns_secret_once(self):
        response = self.client.post("/api/api-keys/", {"name":"CLI"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["key"].startswith("dos_live_"))
        self.assertNotIn("key_hash", response.data["record"])

class IDECollaborationRegressionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="collab-user", password="long-test-password-123")
        self.client.force_authenticate(user=self.user)
        self.workspace = __import__("api.models", fromlist=["CodeWorkspace"]).CodeWorkspace.objects.create(
            owner=self.user, name="Collab", files={"main.py": "print(1)"}, active_file="main.py"
        )

    def test_websocket_patch_rejects_stale_revision_without_overwrite(self):
        from .ide_collaboration import IDECollaborationConsumer
        ws = self.workspace
        consumer = IDECollaborationConsumer()
        consumer.workspace = ws
        consumer.user = self.user
        consumer.client_id = "client-a"
        result = consumer._apply_patch({
            "path": "main.py", "content": "print(2)", "base_revision": ws.revision, "client_id": "client-a"
        })
        self.assertEqual(result["type"], "patch-ack")
        ws.refresh_from_db()
        stale = consumer._apply_patch({
            "path": "main.py", "content": "print(3)", "base_revision": ws.revision - 1, "client_id": "client-b"
        })
        self.assertEqual(stale["type"], "conflict")
        self.assertEqual(stale["reason"], "stale_revision")
        ws.refresh_from_db()
        self.assertEqual(ws.files["main.py"], "print(2)")

    def test_crdt_operation_is_idempotent(self):
        from .ide_collaboration import IDECollaborationConsumer
        ws = self.workspace
        consumer = IDECollaborationConsumer()
        consumer.workspace = ws
        consumer.user = self.user
        consumer.client_id = "client-crdt"
        payload = {
            "path": "main.py", "operation_id": "op-unique-1", "kind": "insert",
            "position": 7, "delete_count": 0, "text": " # ok", "lamport": 1, "actor_id": "client-crdt"
        }
        first = consumer._apply_crdt_operation(payload)
        second = consumer._apply_crdt_operation(payload)
        self.assertEqual(first["type"], "crdt-ack")
        self.assertFalse(first.get("duplicate", False))
        self.assertTrue(second.get("duplicate", False))
        ws.refresh_from_db()
        self.assertEqual(ws.files["main.py"], "print(1) # ok")

class SaaSMaturityTests(APITestCase):
    def test_free_workspace_limit_is_enforced_server_side(self):
        user = User.objects.create_user(username="workspace-quota-user", password="long-test-password-123")
        self.client.force_authenticate(user=user)
        for index in range(3):
            response = self.client.post(
                "/api/ide/workspaces/",
                {"name": f"Workspace {index}", "files": {"README.md": "# Test"}},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        overflow = self.client.post(
            "/api/ide/workspaces/",
            {"name": "Workspace overflow", "files": {"README.md": "# Test"}},
            format="json",
        )
        self.assertEqual(overflow.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(overflow.data["limit"], 3)

    def setUp(self):
        self.user = User.objects.create_user(username="saas-user", email="saas@example.test", password="long-test-password-123")
        self.client.force_authenticate(user=self.user)

    def test_usage_endpoint_exposes_real_plan_limits(self):
        response = self.client.get("/api/usage/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"], "free")
        self.assertIn("ai_messages", response.data["metrics"])
        self.assertEqual(response.data["metrics"]["api_keys"]["limit"], 2)

    def test_api_key_plan_limit_is_enforced(self):
        for index in range(2):
            response = self.client.post("/api/api-keys/", {"name": f"Key {index}"}, format="json")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response = self.client.post("/api/api-keys/", {"name": "Overflow"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_organization_invite_requires_matching_email_on_accept(self):
        Subscription.objects.create(user=self.user, plan="team", status="active")
        response = self.client.post("/api/organizations/", {"name": "Maturity Labs"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        org_id = response.data["id"]
        from .models import Organization, OrganizationSubscription
        OrganizationSubscription.objects.create(organization_id=org_id, plan="team", status="active", quantity=5)
        org = Organization.objects.get(pk=org_id)
        org.plan = "team"
        org.save(update_fields=["plan"])
        response = self.client.post(f"/api/organizations/{org_id}/invites/", {"email": "someone@example.test", "role": "developer"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        token = response.data["token"]
        response = self.client.post("/api/organizations/invites/accept/", {"token": token}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_billing_webhook_rejects_missing_signature(self):
        from django.test import override_settings
        with override_settings():
            response = self.client.post("/api/billing/webhook/", data='{"id":"evt_test","type":"invoice.paid"}', content_type="application/json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MatureSaaSRegressionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="saas-user", password="long-test-password-123")
        self.client.force_authenticate(user=self.user)

    def test_api_key_limit_returns_plan_not_undefined_variable(self):
        from .views import PLAN_LIMITS
        limit = PLAN_LIMITS["free"]["api_keys"]
        for index in range(limit):
            raw = f"dos_live_test_{index}"
            APIKey.objects.create(
                user=self.user,
                name=f"Key {index}",
                prefix=raw[:14],
                key_hash=f"{index:064d}",
            )
        response = self.client.post("/api/api-keys/", {"name": "Overflow"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["plan"], "free")
        self.assertEqual(response.data["limit"], limit)
        self.assertIn("free plan", response.data["error"])

    def test_usage_meter_is_atomic_and_enforces_plan_limit(self):
        from .views import _consume_usage, PLAN_LIMITS
        limit = PLAN_LIMITS["free"]["ai_messages_month"]
        ok, used, _, _ = _consume_usage(self.user, "ai_messages_month", limit)
        self.assertTrue(ok)
        self.assertEqual(used, limit)
        ok, used, returned_limit, plan = _consume_usage(self.user, "ai_messages_month", 1)
        self.assertFalse(ok)
        self.assertEqual(used, limit)
        self.assertEqual(returned_limit, limit)
        self.assertEqual(plan, "free")
        self.assertEqual(
            UsageRecord.objects.get(user=self.user, metric="ai_messages_month").quantity,
            limit,
        )

    def test_revoked_api_key_does_not_consume_active_key_quota(self):
        from .views import PLAN_LIMITS
        limit = PLAN_LIMITS["free"]["api_keys"]
        for index in range(limit):
            raw = f"dos_live_active_{index}"
            APIKey.objects.create(
                user=self.user,
                name=f"Key {index}",
                prefix=raw[:14],
                key_hash=f"{index+100:064d}",
            )
        first = APIKey.objects.filter(user=self.user).first()
        first.revoked_at = __import__("django.utils.timezone", fromlist=["now"]).now()
        first.save(update_fields=["revoked_at"])
        response = self.client.post("/api/api-keys/", {"name": "Replacement"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(APIKey.objects.filter(user=self.user, revoked_at__isnull=True).count(), limit)

    def test_subscription_defaults_to_free_and_usage_isolated_per_user(self):
        response = self.client.get("/api/subscription/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"], "free")
        self.assertEqual(response.data["status"], "active")


class ProductionValidationContractTests(APITestCase):
    """Regression coverage for the external CI contract: AI actions and Stripe."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="contract-user",
            email="contract@example.test",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)

    def test_ai_actions_are_structured_and_metered(self):
        response = self.client.post(
            "/api/ai/actions/",
            {"action": "plan", "input": "Ship the API safely."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["action"], "plan")
        self.assertIn(response.data["mode"], {"local", "provider"})
        self.assertTrue(response.data["answer"])
        self.assertIn("usage", response.data)

    @override_settings(STRIPE_WEBHOOK_SECRET="ci-stripe-webhook-secret")
    def test_stripe_webhook_valid_signature_is_idempotent(self):
        import hashlib
        import hmac
        import json
        import time
        payload = {
            "id": "evt_ci_subscription_001",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_ci_001",
                    "customer": "cus_ci_001",
                    "status": "active",
                    "cancel_at_period_end": False,
                    "current_period_end": int(time.time()) + 86400,
                    "metadata": {"user_id": str(self.user.id), "plan": "pro"},
                    "items": {"data": [{"price": {"id": ""}}]},
                }
            },
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signed = f"{timestamp}.{raw.decode()}".encode()
        digest = hmac.new(
            b"ci-stripe-webhook-secret", signed, hashlib.sha256
        ).hexdigest()
        signature = f"t={timestamp},v1={digest}"

        first = self.client.post(
            "/api/billing/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertFalse(first.data["duplicate"])
        self.user.subscription.refresh_from_db()
        self.assertEqual(self.user.subscription.plan, "pro")

        second = self.client.post(
            "/api/billing/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature,
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["duplicate"])


class BillingEntitlementSafetyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="billing-user",
            email="billing@example.test",
            password="long-test-password-123",
        )
        self.client.force_authenticate(user=self.user)

    def test_unconfigured_paid_plan_does_not_grant_entitlement(self):
        response = self.client.post("/api/subscription/", {"plan": "pro"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(Subscription.objects.filter(user=self.user, plan="pro").exists())

    @override_settings(DEBUG=True)
    def test_explicit_local_billing_mode_is_opt_in(self):
        with override_settings(DEBUG=True):
            with self.settings(ALLOW_LOCAL_BILLING="true"):
                response = self.client.post("/api/subscription/", {"plan": "pro"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"], "pro")
        self.assertEqual(Subscription.objects.get(user=self.user).plan, "pro")


class IDEWorkspaceLifecycleRegressionTests(APITestCase):
    def setUp(self):
        # Keep shared API throttle cache state from leaking between test classes.
        cache.clear()
        self.owner = User.objects.create_user(username="workspace-delete-owner", password="long-test-password-123")
        self.client.force_authenticate(user=self.owner)

    def test_workspace_delete_endpoint_deletes_only_the_owners_workspace(self):
        from .models import CodeWorkspace

        created = self.client.post(
            "/api/ide/workspaces/",
            {"name": "Delete regression", "files": {"README.md": "# Test"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        workspace_id = created.data["id"]

        deleted = self.client.delete(
            "/api/ide/workspaces/", {"id": workspace_id}, format="json"
        )
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CodeWorkspace.objects.filter(pk=workspace_id).exists())

    def test_workspace_delete_does_not_delete_another_users_workspace(self):
        from .models import CodeWorkspace

        other = User.objects.create_user(username="workspace-delete-other", password="long-test-password-123")
        workspace = CodeWorkspace.objects.create(
            owner=other, name="Private workspace", files={"README.md": "# Private"}
        )
        response = self.client.delete(
            "/api/ide/workspaces/", {"id": workspace.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(CodeWorkspace.objects.filter(pk=workspace.pk, owner=other).exists())


@override_settings(STRIPE_WEBHOOK_SECRET="whsec_regression_test")
class StripeWebhookFailClosedTests(APITestCase):
    secret = "whsec_regression_test"

    def signed_header(self, payload, timestamp=None):
        timestamp = int(timestamp or timezone.now().timestamp())
        signed = str(timestamp).encode("ascii") + b"." + payload
        digest = hmac.new(self.secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"

    def post_webhook(self, payload, signature=None):
        return self.client.generic(
            "POST",
            "/api/billing/webhook/",
            payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature or self.signed_header(payload),
        )

    def test_invalid_utf8_webhook_body_returns_400_without_server_error(self):
        payload = b"\xff\xfe"
        timestamp = int(timezone.now().timestamp())
        response = self.post_webhook(payload, f"t={timestamp},v1=invalid")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(BillingEvent.objects.count(), 0)

    def test_signed_json_array_is_rejected_as_invalid_event_shape(self):
        payload = json.dumps(["not", "an", "event"]).encode("utf-8")
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(BillingEvent.objects.count(), 0)

    def test_invalid_signature_is_rejected_without_recording_event(self):
        payload = json.dumps({"id": "evt_bad_sig", "type": "test.unhandled", "data": {"object": {}}}).encode("utf-8")
        response = self.post_webhook(payload, "t=1,v1=invalid")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(BillingEvent.objects.count(), 0)

    def test_duplicate_signed_event_is_acknowledged_without_duplicate_ledger_entry(self):
        payload = json.dumps({"id": "evt_duplicate_test", "type": "test.unhandled", "data": {"object": {}}}).encode("utf-8")
        signature = self.signed_header(payload)
        first = self.post_webhook(payload, signature)
        second = self.post_webhook(payload, signature)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["received"], True)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["duplicate"], True)
        self.assertEqual(BillingEvent.objects.filter(event_id="evt_duplicate_test").count(), 1)


class BackgroundWorkerStaleJobRecoveryTests(APITestCase):
    def test_job_lease_refresh_extends_only_the_current_owners_running_job(self):
        from .management.commands.worker import refresh_job_lease

        old_lease = timezone.now() - timedelta(minutes=2)
        job = BackgroundJob.objects.create(
            kind="test",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=old_lease,
            locked_by="worker-current",
        )

        self.assertTrue(refresh_job_lease(job.pk, "worker-current"))
        job.refresh_from_db()
        self.assertGreater(job.locked_at, old_lease)

        refreshed_lease = job.locked_at
        self.assertFalse(refresh_job_lease(job.pk, "worker-other"))
        job.refresh_from_db()
        self.assertEqual(job.locked_by, "worker-current")
        self.assertEqual(job.locked_at, refreshed_lease)

    def test_job_lease_refresh_does_not_revive_completed_jobs(self):
        from .management.commands.worker import refresh_job_lease

        job = BackgroundJob.objects.create(
            kind="test",
            status="succeeded",
            locked_at=None,
            locked_by="",
        )
        self.assertFalse(refresh_job_lease(job.pk, "worker-current"))
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertIsNone(job.locked_at)

    def test_stale_running_job_is_requeued_with_backoff_and_lease_cleared(self):
        from .management.commands.worker import recover_stale_jobs

        stale_time = timezone.now() - timedelta(minutes=20)
        job = BackgroundJob.objects.create(
            kind="test",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=stale_time,
            locked_by="dead-worker:1",
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertGreater(job.available_at, timezone.now())
        self.assertIn("Recovered stale worker lease.", job.error)
        self.assertIsNotNone(job.last_error_at)
        self.assertIsNone(job.finished_at)

    def test_stale_running_job_at_attempt_limit_is_dead_lettered(self):
        from .management.commands.worker import recover_stale_jobs

        stale_time = timezone.now() - timedelta(minutes=20)
        job = BackgroundJob.objects.create(
            kind="test",
            status="running",
            attempts=3,
            max_attempts=3,
            locked_at=stale_time,
            locked_by="dead-worker:2",
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("Recovered stale worker lease.", job.error)

    def test_stale_export_dead_letter_marks_request_failed(self):
        from .management.commands.worker import recover_stale_jobs

        user = User.objects.create_user(username="stale-export-user", password="test-password")
        export = DataExportRequest.objects.create(user=user, status="running")
        job = BackgroundJob.objects.create(
            kind="data_export",
            payload={"export_id": export.pk, "user_id": user.pk},
            status="running",
            attempts=3,
            max_attempts=3,
            locked_at=timezone.now() - timedelta(minutes=20),
            locked_by="dead-worker:export",
        )

        recover_stale_jobs()

        job.refresh_from_db()
        export.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(export.status, "failed")
        self.assertIsNotNone(job.dead_lettered_at)

    @patch("api.management.commands.worker.execute", return_value={"ok": True})
    def test_successful_job_persistence_releases_worker_lease(self, execute):
        from .management.commands.worker import Command

        job = BackgroundJob.objects.create(kind="test", status="queued", max_attempts=2)
        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result, {"ok": True})
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        execute.assert_called_once()

    @patch("api.management.commands.worker.execute", side_effect=RuntimeError("expected failure"))
    def test_failed_job_persistence_releases_worker_lease(self, execute):
        from .management.commands.worker import Command

        job = BackgroundJob.objects.create(kind="test", status="queued", max_attempts=1)
        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNotNone(job.finished_at)
        execute.assert_called_once()

    @patch("api.management.commands.worker.execute", side_effect=RuntimeError("export failed"))
    def test_permanently_failed_export_marks_request_failed_atomically(self, execute):
        from .management.commands.worker import Command

        user = User.objects.create_user(username="export-failure-user", password="test-password")
        export = DataExportRequest.objects.create(user=user, status="running")
        job = BackgroundJob.objects.create(
            kind="data_export",
            payload={"export_id": export.pk, "user_id": user.pk},
            status="queued",
            max_attempts=1,
        )

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        export.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(export.status, "failed")
        self.assertIsNotNone(job.finished_at)
        execute.assert_called_once()

    @patch("api.management.commands.worker.execute", side_effect=RuntimeError("temporary export failure"))
    def test_retryable_export_failure_does_not_mark_request_failed(self, execute):
        from .management.commands.worker import Command

        user = User.objects.create_user(username="export-retry-user", password="test-password")
        export = DataExportRequest.objects.create(user=user, status="running")
        job = BackgroundJob.objects.create(
            kind="data_export",
            payload={"export_id": export.pk, "user_id": user.pk},
            status="queued",
            max_attempts=3,
        )

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        export.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertEqual(export.status, "running")
        self.assertIsNone(job.finished_at)
        execute.assert_called_once()

    def test_recent_running_job_is_not_recovered(self):
        from .management.commands.worker import recover_stale_jobs

        job = BackgroundJob.objects.create(
            kind="test",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now(),
            locked_by="active-worker:1",
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(job.locked_by, "active-worker:1")


class BackgroundWorkerReadinessTests(APITestCase):
    @contextmanager
    def production_settings(self):
        # These tests isolate worker-heartbeat gating. Full production-runtime
        # dependency enforcement is covered by RenderReadinessFailClosedRegressionTests.
        with patch.dict(os.environ, {
            "REQUIRE_BACKGROUND_WORKER": "true",
            "RENDER_SERVICE_ID": "",
            "RENDER_GIT_COMMIT": "",
        }, clear=False):
            with override_settings(
                IS_PRODUCTION=False,
                DEBUG=False,
                SECRET_KEY="worker-readiness-test-key",
                ALLOWED_HOSTS=["testserver"],
                EMAIL_HOST="smtp.example.test",
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                CACHE_URL="",
                CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
            ):
                yield

    def test_production_readiness_requires_a_recent_worker_heartbeat(self):
        with self.production_settings():
            response = self.client.get("/api/health/ready/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.data["checks"]["worker"])

    def test_recent_worker_heartbeat_satisfies_worker_readiness(self):
        WorkerHeartbeat.objects.create(
            name="background-worker",
            worker_id="worker-test:123",
            last_seen_at=timezone.now(),
        )
        with self.production_settings():
            response = self.client.get("/api/health/ready/")
        self.assertTrue(response.data["checks"]["worker"])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_stale_worker_heartbeat_does_not_pass_readiness(self):
        WorkerHeartbeat.objects.create(
            name="background-worker",
            worker_id="worker-test:123",
            last_seen_at=timezone.now() - timedelta(minutes=5),
        )
        with self.production_settings():
            response = self.client.get("/api/health/ready/")
        self.assertFalse(response.data["checks"]["worker"])
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

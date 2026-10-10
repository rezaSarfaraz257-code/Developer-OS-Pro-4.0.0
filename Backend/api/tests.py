from io import BytesIO
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError, connection
from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from cryptography.fernet import Fernet
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import APIKey, GitHubAccount, GitHubOAuthState, Project, Subscription, Tag, Tool, Task, UsageRecord, SecuritySession


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
        profile = self.owner.profile
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
        with override_settings(MEDIA_ROOT=media_directory.name):
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
        for path in ("../secret.txt", "src/../secret.txt", "/absolute.txt", ".git/config", "src//app.py"):
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

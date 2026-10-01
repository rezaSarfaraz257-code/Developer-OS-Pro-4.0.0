from io import BytesIO
import tempfile

from django.contrib.auth.models import User
from django.db import IntegrityError, connection
from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from cryptography.fernet import Fernet
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import APIKey, GitHubAccount, Project, Subscription, Tag, Tool, Task, UsageRecord, CodeWorkspace


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
        response = self.client.patch("/api/profile/", {"first_name": "Profile", "last_name": "Owner", "email": "owner@example.test", "full_name": "Profile Owner", "bio": "Updated safely.", "github": "https://github.com/owner", "linkedin": "https://www.linkedin.com/in/owner", "website": "https://owner.example.test"}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Profile Owner")
        self.assertEqual(response.data["website"], "https://owner.example.test")

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
        Task.objects.create(project=self.project, title="Done", status="done")
        Task.objects.create(project=self.project, title="Blocked", status="blocked")
        self.authenticate(self.owner)
        response = self.client.get("/api/workspace/summary/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["projects"], 1)
        self.assertEqual(response.data["completion"], 50)
        self.assertEqual(response.data["blocked_tasks"], 1)

    def test_assistant_uses_workspace_context_without_fake_static_metrics(self):
        Task.objects.create(project=self.project, title="Urgent", priority="urgent")
        self.authenticate(self.owner)
        response = self.client.post("/api/assistant/", {"message": "What should I do next?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["mode"], "local")
        self.assertIn("urgent", response.data["answer"].lower())


class RepositoryEngineTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="repo-owner", password="long-test-password-123")
        self.member = User.objects.create_user(username="repo-member", password="long-test-password-123")
        self.authenticate(self.owner)
        response = self.client.post("/api/repositories/", {"name": "Native Repo", "files": {"README.md": "one", "src/app.py": "print(1)"}}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.repo_id = response.data["id"]

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_repository_list_and_detail_are_available(self):
        response = self.client.get("/api/repositories/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        detail = self.client.get(f"/api/repositories/{self.repo_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["files"]["README.md"], "one")

    def test_native_branch_commit_and_diff_round_trip(self):
        commit = self.client.post(f"/api/repositories/{self.repo_id}/native/commits/", {"branch": "main", "message": "Update", "files": {"README.md": "two", "src/app.py": "print(2)"}}, format="json")
        self.assertEqual(commit.status_code, status.HTTP_201_CREATED)
        branches = self.client.get(f"/api/repositories/{self.repo_id}/native/branches/")
        self.assertEqual(branches.status_code, status.HTTP_200_OK)
        self.assertIn("main", branches.data["branches"])
        diff = self.client.get(f"/api/repositories/{self.repo_id}/native/diff/?branch=main")
        self.assertEqual(diff.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["path"] == "README.md" for item in diff.data["files"]))

    def test_repository_object_store_is_visible_to_a_member(self):
        from .models import Activity
        repo = CodeWorkspace.objects.get(pk=self.repo_id)
        Activity.objects.create(actor=self.owner, verb="added repository member", message=self.member.username, related_type="repo_member", related_id=repo.id, metadata={"user_id": self.member.id, "username": self.member.username, "revoked": False})
        self.authenticate(self.member)
        response = self.client.get(f"/api/repositories/{self.repo_id}/native/commits/?branch=main")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data)

    def test_invalid_paths_are_rejected(self):
        response = self.client.post(f"/api/repositories/{self.repo_id}/native/commits/", {"branch": "main", "message": "bad", "files": {"../secret.txt": "no"}}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


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

    @override_settings(GITHUB_CLIENT_ID="test-client", GITHUB_CLIENT_SECRET="test-secret", GITHUB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_github_authorization_uses_pkce(self):
        user = User.objects.create_user(username="pkce-user", password="long-test-password-123")
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/github/authorize/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        authorization_url = response.data["authorization_url"]
        self.assertIn("code_challenge=", authorization_url)
        self.assertIn("code_challenge_method=S256", authorization_url)
        self.assertIn("state=", authorization_url)

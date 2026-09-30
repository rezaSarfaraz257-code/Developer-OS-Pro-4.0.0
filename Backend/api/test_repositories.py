from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, CodeWorkspace


class IndependentRepositoryApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="repo-user", password="long-test-password-123")
        self.client.force_authenticate(user=self.user)

    def test_create_repository_is_independent_from_github(self):
        response = self.client.post(
            "/api/repositories/",
            {"name": "Private OS", "files": {"README.md": "# Private OS\n"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["branch"], "main")
        self.assertEqual(response.data["file_count"], 1)
        self.assertTrue(CodeWorkspace.objects.filter(owner=self.user, runtime="developer-os-repository").exists())

    def test_push_history_and_pull_restore_a_snapshot(self):
        created = self.client.post(
            "/api/repositories/",
            {"name": "Snapshot Repo", "files": {"main.py": "print(1)"}},
            format="json",
        )
        repo_id = created.data["id"]

        self.client.patch(
            f"/api/repositories/{repo_id}/",
            {"files": {"main.py": "print(2)", "README.md": "# Snapshot"}},
            format="json",
        )
        pushed = self.client.post(
            f"/api/repositories/{repo_id}/push/",
            {"message": "Add README and update code", "branch": "main"},
            format="json",
        )
        self.assertEqual(pushed.status_code, status.HTTP_201_CREATED)
        self.assertEqual(pushed.data["commit"]["files"], 2)

        history = self.client.get(f"/api/repositories/{repo_id}/history/?branch=main")
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(history.data), 1)

        self.client.patch(
            f"/api/repositories/{repo_id}/",
            {"files": {"main.py": "broken"}},
            format="json",
        )
        pulled = self.client.post(
            f"/api/repositories/{repo_id}/pull/",
            {"branch": "main"},
            format="json",
        )
        self.assertEqual(pulled.status_code, status.HTTP_200_OK)
        self.assertEqual(pulled.data["repository"]["files"]["main.py"], "print(2)")
        self.assertEqual(pulled.data["repository"]["files"]["README.md"], "# Snapshot")

    def test_repository_isolation(self):
        created = self.client.post("/api/repositories/", {"name": "Private"}, format="json")
        repo_id = created.data["id"]
        other = User.objects.create_user(username="other-repo-user", password="long-test-password-123")
        self.client.force_authenticate(user=other)
        response = self.client.get(f"/api/repositories/{repo_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_clone_creates_a_separate_working_tree(self):
        created = self.client.post(
            "/api/repositories/",
            {"name": "Source", "files": {"README.md": "source"}},
            format="json",
        )
        clone = self.client.post(
            f"/api/repositories/{created.data['id']}/clone/",
            {"name": "Clone"},
            format="json",
        )
        self.assertEqual(clone.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(created.data["id"], clone.data["id"])
        self.assertEqual(clone.data["files"]["README.md"], "source")
        self.assertTrue(Activity.objects.filter(actor=self.user, related_type="repository_commit", related_id=clone.data["id"]).exists())


class NativeRepositoryEngineTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="native-user", password="long-test-password-123")
        self.client.force_authenticate(user=self.user)

    def repo(self):
        response = self.client.post(
            "/api/repositories/",
            {"name": "Native Engine", "files": {"README.md": "# Native\n", "app.py": "print(1)"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data["id"]

    def test_native_commit_creates_content_addressed_tree(self):
        repo_id = self.repo()
        response = self.client.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "main", "message": "Update app", "files": {"README.md": "# Native\n", "app.py": "print(2)"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["commit"]["id"]), 64)
        refs = self.client.get(f"/api/repositories/{repo_id}/native/refs/")
        self.assertEqual(refs.status_code, status.HTTP_200_OK)
        self.assertEqual(refs.data["head"], response.data["commit"]["id"])

    def test_native_branch_and_merge_with_conflict_detection(self):
        repo_id = self.repo()
        branch = self.client.post(
            f"/api/repositories/{repo_id}/native/branches/",
            {"branch": "feature/test", "from": "main"},
            format="json",
        )
        self.assertEqual(branch.status_code, status.HTTP_200_OK)
        update = self.client.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "feature/test", "message": "Feature", "files": {"README.md": "# Native\n", "app.py": "print(2)"}},
            format="json",
        )
        self.assertEqual(update.status_code, status.HTTP_201_CREATED)
        merge = self.client.post(
            f"/api/repositories/{repo_id}/native/merge/",
            {"source": "feature/test", "target": "main"},
            format="json",
        )
        self.assertEqual(merge.status_code, status.HTTP_200_OK)
        self.assertTrue(merge.data["merged"])

    def test_native_diff_and_tags(self):
        repo_id = self.repo()
        commit = self.client.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "main", "message": "Change", "files": {"README.md": "# Native\n", "app.py": "print(99)"}},
            format="json",
        )
        self.assertEqual(commit.status_code, status.HTTP_201_CREATED)
        diff = self.client.get(f"/api/repositories/{repo_id}/native/diff/?branch=main")
        self.assertEqual(diff.status_code, status.HTTP_200_OK)
        self.assertIn("app.py", [item["path"] for item in diff.data["files"]])
        tag = self.client.post(
            f"/api/repositories/{repo_id}/native/tags/",
            {"name": "v1.0.0", "branch": "main"},
            format="json",
        )
        self.assertEqual(tag.status_code, status.HTTP_201_CREATED)
        tags = self.client.get(f"/api/repositories/{repo_id}/native/tags/list/")
        self.assertEqual(tags.status_code, status.HTTP_200_OK)
        self.assertEqual(tags.data[0]["name"], "v1.0.0")

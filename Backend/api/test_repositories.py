from django.contrib.auth.models import User
from django.test import TransactionTestCase
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

    def test_delete_repository_is_serialized_and_records_audit_event(self):
        created = self.client.post(
            "/api/repositories/",
            {"name": "Delete safely", "files": {"README.md": "# Delete safely"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        repo_id = created.data["id"]

        deleted = self.client.delete(f"/api/repositories/{repo_id}/")

        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CodeWorkspace.objects.filter(pk=repo_id).exists())
        self.assertTrue(
            Activity.objects.filter(
                actor=self.user,
                verb="deleted a repository",
                related_type="repository_deleted",
                related_id=repo_id,
            ).exists()
        )

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
        repo_before_push = CodeWorkspace.objects.get(pk=repo_id).revision
        pushed = self.client.post(
            f"/api/repositories/{repo_id}/push/",
            {"message": "Add README and update code", "branch": "main"},
            format="json",
        )
        self.assertEqual(pushed.status_code, status.HTTP_201_CREATED)
        self.assertEqual(pushed.data["commit"]["files"], 2)
        self.assertEqual(pushed.data["repository"]["revision"], repo_before_push + 1)

        history = self.client.get(f"/api/repositories/{repo_id}/history/?branch=main")
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(history.data), 1)

        self.client.patch(
            f"/api/repositories/{repo_id}/",
            {"files": {"main.py": "broken"}},
            format="json",
        )
        repo_before_pull = CodeWorkspace.objects.get(pk=repo_id).revision
        pulled = self.client.post(
            f"/api/repositories/{repo_id}/pull/",
            {"branch": "main"},
            format="json",
        )
        self.assertEqual(pulled.status_code, status.HTTP_200_OK)
        self.assertEqual(pulled.data["repository"]["revision"], repo_before_pull + 1)
        self.assertEqual(pulled.data["repository"]["files"]["main.py"], "print(2)")
        self.assertEqual(pulled.data["repository"]["files"]["README.md"], "# Snapshot")


    def test_workspace_sync_push_advances_repository_revision_once(self):
        repository = self.client.post(
            "/api/repositories/",
            {"name": "Workspace Sync", "files": {"main.py": "print('repo')"}},
            format="json",
        )
        workspace = CodeWorkspace.objects.create(
            owner=self.user, name="Workspace source", files={"main.py": "print('workspace')"},
            active_file="main.py",
        )
        repo_id = repository.data["id"]
        revision_before = CodeWorkspace.objects.get(pk=repo_id).revision
        response = self.client.post(
            f"/api/repositories/{repo_id}/sync-workspace/",
            {"workspace_id": workspace.pk, "direction": "push"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["repository"]["revision"], revision_before + 1)
        self.assertEqual(response.data["repository"]["files"]["main.py"], "print('workspace')")

    def test_snapshot_repository_endpoints_reject_invalid_branch_refs(self):
        created = self.client.post(
            "/api/repositories/",
            {"name": "Invalid refs", "files": {"main.py": "print(1)"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        repo_id = created.data["id"]

        invalid_checkout = self.client.post(
            f"/api/repositories/{repo_id}/branch/",
            {"branch": "../main"},
            format="json",
        )
        invalid_push = self.client.post(
            f"/api/repositories/{repo_id}/push/",
            {"branch": "feature//bad", "message": "Reject invalid ref"},
            format="json",
        )
        invalid_history = self.client.get(
            f"/api/repositories/{repo_id}/history/?branch=.hidden"
        )
        invalid_pull = self.client.post(
            f"/api/repositories/{repo_id}/pull/",
            {"branch": "main.lock"},
            format="json",
        )

        for response in (
            invalid_checkout,
            invalid_push,
            invalid_history,
            invalid_pull,
        ):
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

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

    def test_locked_repository_returns_not_found_after_concurrent_delete(self):
        from django.http import Http404
        from .repository_engine import _locked_repo

        user = User.objects.create_user(
            username="deleted-repo-lock-test",
            password="long-test-password-123",
        )
        repo = CodeWorkspace.objects.create(
            owner=user,
            name="Deleted during mutation preflight",
            runtime="developer-os-repository",
            framework="main",
            files={"README.md": "# race"},
        )
        repo_id = repo.pk
        repo.delete()

        with self.assertRaises(Http404):
            _locked_repo(user, repo_id)

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

    def test_native_commit_defaults_to_repository_branch_when_branch_is_omitted(self):
        repo_id = self.repo()
        CodeWorkspace.objects.filter(pk=repo_id).update(framework="feature/default")
        response = self.client.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"message": "Use configured branch", "files": {"app.py": "print('branch default')"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["commit"]["branch"], "feature/default")
        self.assertEqual(
            CodeWorkspace.objects.get(pk=repo_id).framework,
            "feature/default",
        )

    def test_native_branch_endpoints_reject_invalid_branch_references(self):
        repo_id = self.repo()
        invalid_source = self.client.post(
            f"/api/repositories/{repo_id}/native/branches/",
            {"branch": "feature/safe", "from": "../main"},
            format="json",
        )
        invalid_merge = self.client.post(
            f"/api/repositories/{repo_id}/native/merge/",
            {"source": "../feature", "target": "main"},
            format="json",
        )
        invalid_diff = self.client.get(
            f"/api/repositories/{repo_id}/native/diff/?branch=..%2Fmain"
        )
        self.assertEqual(invalid_source.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_merge.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_diff.status_code, status.HTTP_400_BAD_REQUEST)

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
        from . import repository_engine
        merge_payload = repository_engine._read_object(
            self.user, repo_id, "commit", merge.data["head"]
        )
        self.assertEqual(
            merge_payload.get("parents"),
            [merge_payload.get("parent"), update.data["commit"]["id"]],
        )
        self.assertIn(
            update.data["commit"]["id"],
            repository_engine._ancestor_chain(self.user, repository_engine._repo(self.user, repo_id), merge.data["head"]),
        )

    def test_native_tags_reject_invalid_git_refs(self):
        repo_id = self.repo()
        for invalid_name in ("../release", "feature//release", ".hidden", "release.lock"):
            response = self.client.post(
                f"/api/repositories/{repo_id}/native/tags/",
                {"name": invalid_name},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

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


    def test_native_commit_uses_working_tree_after_acquiring_lock(self):
        from unittest.mock import patch
        from . import repository_engine

        repo_id = self.repo()
        original_locked_repo = repository_engine._locked_repo

        def update_before_lock(user, pk):
            CodeWorkspace.objects.filter(pk=pk).update(
                files={"README.md": "# Latest\n", "app.py": "print('latest')"}
            )
            return original_locked_repo(user, pk)

        with patch("api.repository_engine._locked_repo", side_effect=update_before_lock):
            response = self.client.post(
                f"/api/repositories/{repo_id}/native/commits/",
                {"branch": "main", "message": "Commit latest working tree"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        locked_repo = repository_engine._repo(self.user, repo_id)
        committed = repository_engine._commit_files(
            self.user, locked_repo, response.data["commit"]["id"]
        )
        self.assertEqual(committed["app.py"], "print('latest')")

    def test_native_tags_reject_unknown_commits_and_prevent_retargeting(self):
        repo_id = self.repo()
        missing = self.client.post(
            f"/api/repositories/{repo_id}/native/tags/",
            {"name": "v1.0.0", "commit": "f" * 64},
            format="json",
        )
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

        first = self.client.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "main", "message": "First tag target", "files": {"app.py": "print(1)"}},
            format="json",
        )
        second = self.client.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "main", "message": "Second tag target", "files": {"app.py": "print(2)"}},
            format="json",
        )
        tag = self.client.post(
            f"/api/repositories/{repo_id}/native/tags/",
            {"name": "v1.0.0", "commit": first.data["commit"]["id"]},
            format="json",
        )
        duplicate = self.client.post(
            f"/api/repositories/{repo_id}/native/tags/",
            {"name": "v1.0.0", "commit": second.data["commit"]["id"]},
            format="json",
        )
        self.assertEqual(tag.status_code, status.HTTP_201_CREATED)
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)

    def test_member_revocation_blocks_subsequent_native_mutations(self):
        repo_id = self.repo()
        member = User.objects.create_user(
            username="native-member",
            password="long-test-password-123",
        )
        added = self.client.post(
            f"/api/repositories/{repo_id}/native/members/",
            {"user_id": member.id},
            format="json",
        )
        self.assertEqual(added.status_code, status.HTTP_201_CREATED)

        collaborator = self.client.__class__()
        collaborator.force_authenticate(user=member)
        before_revoke = collaborator.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "main", "message": "Member commit", "files": {"app.py": "print(3)"}},
            format="json",
        )
        self.assertEqual(before_revoke.status_code, status.HTTP_201_CREATED)

        revoked = self.client.delete(
            f"/api/repositories/{repo_id}/native/members/",
            {"user_id": member.id},
            format="json",
        )
        self.assertEqual(revoked.status_code, status.HTTP_204_NO_CONTENT)
        after_revoke = collaborator.post(
            f"/api/repositories/{repo_id}/native/commits/",
            {"branch": "main", "message": "Must be rejected", "files": {"app.py": "print(4)"}},
            format="json",
        )
        self.assertEqual(after_revoke.status_code, status.HTTP_404_NOT_FOUND)


class RepositoryPushSerializationTests(TransactionTestCase):
    """The branch-head read must be protected by the repository row lock."""

    def test_push_reads_branch_head_inside_the_locking_transaction(self):
        from unittest.mock import patch
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from rest_framework.test import APIClient
        from . import repository

        user = User.objects.create_user(
            username="repo-serialized-push",
            password="long-test-password-123",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        created = client.post(
            "/api/repositories/",
            {"name": "Serialized Push", "files": {"main.py": "print(1)"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        repo_id = created.data["id"]

        if not connection.features.has_select_for_update:
            self.skipTest("This regression requires PostgreSQL-style row-level SELECT FOR UPDATE locks.")

        original_commit_qs = repository._commit_qs
        commit_qs_calls = 0

        def assert_branch_head_read_is_atomic(*args, **kwargs):
            nonlocal commit_qs_calls
            commit_qs_calls += 1
            if commit_qs_calls == 1:
                self.assertTrue(
                    connection.in_atomic_block,
                    "The branch head must be read inside the locking transaction.",
                )
                self.assertTrue(
                    any(
                        "FOR UPDATE" in query["sql"].upper()
                        and "CODEWORKSPACE" in query["sql"].upper()
                        for query in captured_queries.captured_queries
                    ),
                    "The repository row lock must be acquired before reading the branch head.",
                )
            return original_commit_qs(*args, **kwargs)

        with CaptureQueriesContext(connection) as captured_queries:
            with patch("api.repository._commit_qs", side_effect=assert_branch_head_read_is_atomic):
                response = client.post(
                    f"/api/repositories/{repo_id}/push/",
                    {"branch": "main", "message": "Serialized commit"},
                    format="json",
                )

        self.assertTrue(
            any("FOR UPDATE" in query["sql"].upper() and "CODEWORKSPACE" in query["sql"].upper() for query in captured_queries),
            "Push must issue SELECT FOR UPDATE against the repository row.",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertGreaterEqual(commit_qs_calls, 2)
        commit = Activity.objects.get(
            actor=user,
            related_type="repository_commit",
            related_id=repo_id,
            message="Serialized commit",
        )
        self.assertIsNotNone(commit.metadata.get("parent"))

    def test_native_commit_reads_head_after_repository_row_lock(self):
        from unittest.mock import patch
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from rest_framework.test import APIClient
        from . import repository_engine

        if not connection.features.has_select_for_update:
            self.skipTest("Requires PostgreSQL-style row-level SELECT FOR UPDATE locks.")

        user = User.objects.create_user(
            username="native-serialized-commit",
            password="long-test-password-123",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        created = client.post(
            "/api/repositories/",
            {"name": "Native Serialized", "files": {"main.py": "print(1)"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        repo_id = created.data["id"]
        original_head = repository_engine._head
        head_calls = 0

        def assert_head_read_is_locked(*args, **kwargs):
            nonlocal head_calls
            head_calls += 1
            if head_calls == 1:
                self.assertTrue(connection.in_atomic_block)
                self.assertTrue(
                    any(
                        "FOR UPDATE" in query["sql"].upper()
                        and "CODEWORKSPACE" in query["sql"].upper()
                        for query in captured_queries.captured_queries
                    ),
                    "Native branch head must be read after locking CodeWorkspace.",
                )
            return original_head(*args, **kwargs)

        with CaptureQueriesContext(connection) as captured_queries:
            with patch("api.repository_engine._head", side_effect=assert_head_read_is_locked):
                response = client.post(
                    f"/api/repositories/{repo_id}/native/commits/",
                    {"branch": "main", "message": "Locked native commit", "files": {"main.py": "print(2)"}},
                    format="json",
                )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertGreaterEqual(head_calls, 1)


    def test_concurrent_snapshot_pushes_form_one_parent_chain(self):
        from django.db import connection
        if not connection.features.has_select_for_update:
            self.skipTest("Requires PostgreSQL-style row-level SELECT FOR UPDATE locks.")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from rest_framework.test import APIClient

        user = User.objects.create_user(
            username="snapshot-concurrent-push",
            password="long-test-password-123",
        )
        setup = APIClient()
        setup.force_authenticate(user=user)
        created = setup.post(
            "/api/repositories/",
            {"name": "Concurrent Snapshot", "files": {"main.py": "print(0)"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        repo_id = created.data["id"]
        barrier = Barrier(2)

        def push(message, content):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                barrier.wait(timeout=15)
                response = client.post(
                    f"/api/repositories/{repo_id}/push/",
                    {"branch": "main", "message": message},
                    format="json",
                )
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(push, "Concurrent A", "print('A')")
            second = pool.submit(push, "Concurrent B", "print('B')")
            results = [first.result(timeout=30), second.result(timeout=30)]

        self.assertTrue(all(code == status.HTTP_201_CREATED for code, _ in results), results)
        hashes = {
            data["commit"]["hash"]
            for _, data in results
        }
        parents = {
            data["commit"]["parent"]
            for _, data in results
        }
        self.assertTrue(
            any(parent in hashes for parent in parents),
            "Concurrent snapshot pushes must serialize into a single parent chain.",
        )

    def test_concurrent_native_commits_form_one_parent_chain(self):
        from django.db import connection
        if not connection.features.has_select_for_update:
            self.skipTest("Requires PostgreSQL-style row-level SELECT FOR UPDATE locks.")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from rest_framework.test import APIClient

        user = User.objects.create_user(
            username="native-concurrent-commit",
            password="long-test-password-123",
        )
        setup = APIClient()
        setup.force_authenticate(user=user)
        created = setup.post(
            "/api/repositories/",
            {"name": "Concurrent Native", "files": {"main.py": "print(0)"}},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        repo_id = created.data["id"]
        barrier = Barrier(2)

        def commit(message, content):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                barrier.wait(timeout=15)
                response = client.post(
                    f"/api/repositories/{repo_id}/native/commits/",
                    {
                        "branch": "main",
                        "message": message,
                        "files": {"main.py": content},
                    },
                    format="json",
                )
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(commit, "Native concurrent A", "print('A')")
            second = pool.submit(commit, "Native concurrent B", "print('B')")
            results = [first.result(timeout=30), second.result(timeout=30)]

        self.assertTrue(all(code == status.HTTP_201_CREATED for code, _ in results), results)
        hashes = {
            data["commit"]["id"]
            for _, data in results
        }
        parents = {
            data["commit"]["parent"]
            for _, data in results
        }
        self.assertTrue(
            any(parent in hashes for parent in parents),
            "Concurrent native commits must serialize into a single parent chain.",
        )

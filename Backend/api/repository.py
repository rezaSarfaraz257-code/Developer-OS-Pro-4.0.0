"""Independent repository management for Developer OS.

This module deliberately does not depend on GitHub. A CodeWorkspace is the
working tree and Activity records are immutable server-side commit snapshots.
The API exposes repository-style create/list/commit(push)/pull/history,
branch checkout, clone, and diff metadata while preserving the existing IDE.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Activity, CodeWorkspace

REPOSITORY_RUNTIME = "developer-os-repository"
DEFAULT_BRANCH = "main"
MAX_FILES = 2000
MAX_FILE_BYTES = 1_500_000
MAX_TOTAL_BYTES = 25_000_000
SAFE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._@+\- /]+$")


def _clean_files(raw):
    if not isinstance(raw, dict):
        raise ValueError("files must be an object mapping paths to text content.")
    if len(raw) > MAX_FILES:
        raise ValueError(f"A repository may contain at most {MAX_FILES} files.")
    cleaned = {}
    total = 0
    for path, content in raw.items():
        path = str(path).strip()
        if not path or len(path) > 500 or not SAFE_PATH.fullmatch(path):
            raise ValueError(f"Unsafe repository path: {path!r}")
        if not isinstance(content, str):
            raise ValueError(f"Repository file {path!r} must contain text.")
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ValueError(f"Repository file {path!r} exceeds the per-file limit.")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Repository working tree exceeds the size limit.")
        cleaned[path] = content
    return dict(sorted(cleaned.items()))


def _commit_hash(repo_id, branch, message, parent, files):
    payload = json.dumps(
        {"repo": repo_id, "branch": branch, "message": message, "parent": parent, "files": files},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _commit_qs(user, repo_id=None, branch=None):
    qs = Activity.objects.filter(actor=user, related_type="repository_commit")
    if repo_id is not None:
        qs = qs.filter(related_id=repo_id)
    if branch:
        qs = qs.filter(metadata__branch=branch)
    return qs.order_by("-created_at", "-id")


def _repo(user, pk):
    return get_object_or_404(CodeWorkspace, pk=pk, owner=user, runtime=REPOSITORY_RUNTIME)


def _commit_payload(activity):
    meta = activity.metadata or {}
    return {
        "id": activity.id,
        "hash": meta.get("hash", ""),
        "short_hash": meta.get("hash", "")[:10],
        "message": activity.message,
        "branch": meta.get("branch", DEFAULT_BRANCH),
        "parent": meta.get("parent"),
        "files": len(meta.get("files", {})),
        "created_at": activity.created_at,
    }


def _repo_payload(repo, user, include_files=False):
    branch = repo.framework or DEFAULT_BRANCH
    commits = _commit_qs(user, repo.id, branch)
    head = commits.first()
    payload = {
        "id": repo.id,
        "name": repo.name,
        "slug": repo.name.lower().replace(" ", "-")[:160],
        "branch": branch,
        "revision": repo.revision,
        "files": repo.files if include_files else None,
        "file_count": len(repo.files or {}),
        "size_bytes": sum(len(str(v).encode("utf-8")) for v in (repo.files or {}).values()),
        "head": _commit_payload(head) if head else None,
        "updated_at": repo.updated_at,
        "created_at": repo.created_at,
    }
    if not include_files:
        payload.pop("files")
    return payload


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def repositories_api(request):
    if request.method == "GET":
        repos = CodeWorkspace.objects.filter(owner=request.user, runtime=REPOSITORY_RUNTIME).order_by("-updated_at")
        return Response([_repo_payload(repo, request.user) for repo in repos])

    name = str(request.data.get("name") or "").strip()
    if not name or len(name) > 160:
        return Response({"error": "A repository name between 1 and 160 characters is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        files = _clean_files(request.data.get("files") or {"README.md": f"# {name}\n\nDeveloper OS independent repository.\n"})
    except ValueError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        repo = CodeWorkspace.objects.create(
            owner=request.user,
            name=name,
            runtime=REPOSITORY_RUNTIME,
            framework=DEFAULT_BRANCH,
            package_manager="developer-os",
            files=files,
            active_file=next(iter(files), "README.md"),
            language="polyglot",
        )
        initial_message = "Initial repository snapshot"
        initial_hash = _commit_hash(repo.id, DEFAULT_BRANCH, initial_message, None, files)
        Activity.objects.create(
            actor=request.user,
            verb="created repository",
            message=initial_message,
            related_type="repository_commit",
            related_id=repo.id,
            metadata={"hash": initial_hash, "branch": DEFAULT_BRANCH, "parent": None, "files": deepcopy(files)},
        )
        Activity.objects.create(
            actor=request.user,
            verb="created a repository",
            message=name,
            related_type="repository",
            related_id=repo.id,
            metadata={"branch": DEFAULT_BRANCH, "file_count": len(files)},
        )
    return Response(_repo_payload(repo, request.user, include_files=True), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def repository_detail_api(request, pk):
    repo = _repo(request.user, pk)
    if request.method == "GET":
        return Response(_repo_payload(repo, request.user, include_files=True))
    if request.method == "DELETE":
        name = repo.name
        repo.delete()
        Activity.objects.create(actor=request.user, verb="deleted a repository", message=name, related_type="repository_deleted", related_id=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)

    if "files" in request.data:
        try:
            files = _clean_files(request.data["files"])
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        repo.files = files
    if "active_file" in request.data:
        active = str(request.data.get("active_file") or "")
        if active and active not in (repo.files or {}):
            return Response({"error": "active_file does not exist in the working tree."}, status=status.HTTP_400_BAD_REQUEST)
        repo.active_file = active
    repo.save()
    return Response(_repo_payload(repo, request.user, include_files=True))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def repository_push_api(request, pk):
    repo = _repo(request.user, pk)
    branch = str(request.data.get("branch") or repo.framework or DEFAULT_BRANCH).strip()
    message = str(request.data.get("message") or "Update repository").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", branch):
        return Response({"error": "Invalid branch name."}, status=status.HTTP_400_BAD_REQUEST)
    if not message or len(message) > 500:
        return Response({"error": "A commit message between 1 and 500 characters is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        files = _clean_files(repo.files or {})
    except ValueError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    previous = _commit_qs(request.user, repo.id, branch).first()
    parent = (previous.metadata or {}).get("hash") if previous else None
    commit_hash = _commit_hash(repo.id, branch, message, parent, files)
    if previous and (previous.metadata or {}).get("hash") == commit_hash:
        return Response({"error": "Nothing to push.", "commit": _commit_payload(previous)}, status=status.HTTP_409_CONFLICT)

    with transaction.atomic():
        repo.framework = branch
        repo.files = files
        repo.revision = int(repo.revision or 1) + 1
        repo.save(update_fields=["framework", "files", "revision", "updated_at"])
        commit = Activity.objects.create(
            actor=request.user,
            verb="pushed repository commit",
            message=message,
            related_type="repository_commit",
            related_id=repo.id,
            metadata={"hash": commit_hash, "branch": branch, "parent": parent, "files": deepcopy(files)},
        )
    return Response({"repository": _repo_payload(repo, request.user), "commit": _commit_payload(commit)}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def repository_history_api(request, pk):
    repo = _repo(request.user, pk)
    branch = str(request.query_params.get("branch") or repo.framework or DEFAULT_BRANCH)
    try:
        requested_limit = int(request.query_params.get("limit") or 50)
    except (TypeError, ValueError):
        requested_limit = 50
    limit = min(max(requested_limit, 1), 100)
    commits = _commit_qs(request.user, repo.id, branch)[:limit]
    return Response([_commit_payload(commit) for commit in commits])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def repository_pull_api(request, pk):
    repo = _repo(request.user, pk)
    branch = str(request.data.get("branch") or repo.framework or DEFAULT_BRANCH)
    commit_hash = str(request.data.get("commit") or "").strip()
    commits = _commit_qs(request.user, repo.id, branch)
    commit = commits.filter(metadata__hash=commit_hash).first() if commit_hash else commits.first()
    if not commit:
        return Response({"error": "No remote commit exists for this branch."}, status=status.HTTP_404_NOT_FOUND)
    files = _clean_files((commit.metadata or {}).get("files") or {})
    repo.framework = branch
    repo.files = files
    repo.active_file = next(iter(files), "")
    repo.revision = int(repo.revision or 1) + 1
    repo.save()
    Activity.objects.create(actor=request.user, verb="pulled repository commit", message=commit.message, related_type="repository_pull", related_id=repo.id, metadata={"hash": (commit.metadata or {}).get("hash"), "branch": branch})
    return Response({"repository": _repo_payload(repo, request.user, include_files=True), "commit": _commit_payload(commit)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def repository_branch_api(request, pk):
    repo = _repo(request.user, pk)
    branch = str(request.data.get("branch") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", branch):
        return Response({"error": "Invalid branch name."}, status=status.HTTP_400_BAD_REQUEST)
    existing = _commit_qs(request.user, repo.id, branch).first()
    repo.framework = branch
    if existing and request.data.get("pull", True):
        repo.files = _clean_files((existing.metadata or {}).get("files") or {})
        repo.active_file = next(iter(repo.files), "")
    repo.save()
    return Response(_repo_payload(repo, request.user, include_files=True))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def repository_clone_api(request, pk):
    source = _repo(request.user, pk)
    name = str(request.data.get("name") or f"{source.name} Clone").strip()
    if not name or len(name) > 160:
        return Response({"error": "A valid clone name is required."}, status=status.HTTP_400_BAD_REQUEST)
    repo = CodeWorkspace.objects.create(
        owner=request.user,
        name=name,
        runtime=REPOSITORY_RUNTIME,
        framework=source.framework or DEFAULT_BRANCH,
        package_manager="developer-os",
        files=deepcopy(source.files or {}),
        active_file=source.active_file,
        language="polyglot",
    )
    source_head = _commit_qs(request.user, source.id, source.framework or DEFAULT_BRANCH).first()
    if source_head:
        meta = deepcopy(source_head.metadata or {})
        meta["repo_id"] = repo.id
        meta["hash"] = _commit_hash(repo.id, meta.get("branch", DEFAULT_BRANCH), source_head.message, None, repo.files)
        meta["parent"] = None
        Activity.objects.create(actor=request.user, verb="cloned repository", message=source_head.message, related_type="repository_commit", related_id=repo.id, metadata=meta)
    return Response(_repo_payload(repo, request.user, include_files=True), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def repository_sync_workspace_api(request, pk):
    """Synchronize a native Developer OS repository with an IDE workspace.

    This is deliberately provider-neutral: no GitHub, GitLab, Bitbucket, or
    external remote is involved. The repository lives inside Developer OS.
    """
    repo = _repo(request.user, pk)
    workspace_id = request.data.get("workspace_id")
    direction = str(request.data.get("direction") or "push").lower()
    workspace = get_object_or_404(CodeWorkspace, pk=workspace_id, owner=request.user)
    if direction not in {"push", "pull"}:
        return Response({"error": "direction must be push or pull."}, status=status.HTTP_400_BAD_REQUEST)

    if direction == "push":
        try:
            files = _clean_files(workspace.files or {})
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        repo.files = files
        repo.active_file = workspace.active_file if workspace.active_file in files else next(iter(files), "")
        repo.framework = repo.framework or DEFAULT_BRANCH
        repo.revision = int(repo.revision or 1) + 1
        repo.save()
        return Response({
            "direction": "push",
            "repository": _repo_payload(repo, request.user, include_files=True),
            "workspace_id": workspace.id,
            "file_count": len(files),
        })

    files = _clean_files(repo.files or {})
    workspace.files = files
    workspace.active_file = repo.active_file if repo.active_file in files else next(iter(files), "")
    workspace.save()
    return Response({
        "direction": "pull",
        "repository": _repo_payload(repo, request.user, include_files=True),
        "workspace": {
            "id": workspace.id,
            "files": workspace.files,
            "active_file": workspace.active_file,
            "revision": workspace.revision,
        },
    })

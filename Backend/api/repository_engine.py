"""Native repository engine for Developer OS.

This layer keeps GitHub optional. It uses content-addressed blob/tree/commit
records stored by Developer OS itself, while preserving the existing
snapshot repository API for backward compatibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from copy import deepcopy

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Activity, CodeWorkspace
from django.contrib.auth.models import User

RUNTIME = "developer-os-repository"
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")
MAX_FILES = 2000
MAX_FILE_BYTES = 1_500_000
MAX_TOTAL_BYTES = 25_000_000


def _repo(user, pk):
    repo = CodeWorkspace.objects.get(pk=pk, runtime=RUNTIME)
    if repo.owner_id == user.id:
        return repo
    # Membership is an append-only event stream. An older grant must not
    # remain effective after a newer revocation event.
    latest_membership = _member_rows(repo).filter(metadata__user_id=user.id).first()
    if not latest_membership or (latest_membership.metadata or {}).get("revoked", False):
        raise CodeWorkspace.DoesNotExist
    return repo


def _is_owner(user, repo):
    return repo.owner_id == user.id


def _locked_repo(user, pk):
    """Acquire the repository mutation lock and revalidate membership."""
    repo = get_object_or_404(
        CodeWorkspace.objects.select_for_update(),
        pk=pk,
        runtime=RUNTIME,
    )
    if repo.owner_id != user.id:
        # Re-check membership after acquiring the repository lock so a
        # concurrent revocation cannot authorize a stale mutation request.
        _repo(user, repo.pk)
    return repo

def _member_rows(repo):
    return Activity.objects.filter(
        related_type="repo_member", related_id=repo.id
    ).order_by("-created_at", "-id")


def _files(raw):
    if not isinstance(raw, dict) or len(raw) > MAX_FILES:
        raise ValueError("Invalid repository tree.")
    out, total = {}, 0
    for path, value in raw.items():
        path = str(path).strip()
        if not path or len(path) > 500 or path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"Unsafe path: {path}")
        if not isinstance(value, str):
            raise ValueError(f"File {path} must be text.")
        size = len(value.encode())
        if size > MAX_FILE_BYTES:
            raise ValueError(f"File {path} exceeds the size limit.")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Repository exceeds the size limit.")
        out[path] = value
    return dict(sorted(out.items()))


def _hash(kind, payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256((kind + "\0").encode() + raw).hexdigest()


def _objects(user, repo_id, kind=None):
    """Read repository objects independently of the requesting actor.

    Repository membership is authorized by _repo(). Object rows are therefore
    keyed by the globally unique repository id rather than by actor. The old
    actor filter made collaborators unable to read the owner's commits, trees,
    and blobs even though _repo() correctly granted repository access.
    """
    if kind is None:
        return Activity.objects.filter(
            related_id=repo_id,
            related_type__startswith="repo_object:",
        ).order_by("-created_at", "-id")
    return Activity.objects.filter(
        related_id=repo_id,
        related_type=f"repo_object:{kind}",
    ).order_by("-created_at", "-id")


def _write_object(user, repo_id, kind, payload):
    oid = _hash(kind, payload)
    existing = _objects(user, repo_id, kind).filter(metadata__oid=oid).first()
    if existing:
        return oid
    Activity.objects.create(
        actor=user,
        verb=f"stored repository {kind}",
        message=oid[:12],
        related_type=f"repo_object:{kind}",
        related_id=repo_id,
        metadata={"oid": oid, "payload": payload},
    )
    return oid


def _read_object(user, repo_id, kind, oid):
    row = _objects(user, repo_id, kind).filter(metadata__oid=oid).first()
    return (row.metadata or {}).get("payload") if row else None


def _tree(user, repo_id, files):
    blobs = {}
    for path, content in files.items():
        blobs[path] = _write_object(user, repo_id, "blob", {"path": path, "content": content})
    return _write_object(user, repo_id, "tree", {"files": blobs})


def _commits(user, repo_id, branch=None):
    qs = _objects(user, repo_id, "commit")
    if branch:
        qs = qs.filter(metadata__branch=branch)
    return qs


def _head(user, repo, branch):
    row = _commits(user, repo.id, branch).order_by("-created_at", "-id").first()
    return (row.metadata or {}).get("oid") if row else None


def _commit(user, repo, branch, message, files, parent=None, author=None, parents=None):
    files = _files(files)
    tree = _tree(user, repo.id, files)
    payload = {
        "tree": tree,
        "parent": parent,
        "branch": branch,
        "message": message,
        "author": author or user.username,
    }
    if parents:
        # Preserve both merge parents inside the hashed commit payload.
        payload["parents"] = list(parents)
    oid = _hash("commit", payload)
    existing = _commits(user, repo.id, branch).filter(metadata__oid=oid).first()
    if existing:
        return oid, tree, False
    Activity.objects.create(
        actor=user,
        verb="committed repository",
        message=message,
        related_type="repo_object:commit",
        related_id=repo.id,
        metadata={"oid": oid, "payload": payload, "branch": branch},
    )
    return oid, tree, True


def _commit_files(user, repo, oid):
    payload = _read_object(user, repo.id, "commit", oid)
    if not payload:
        return {}
    tree = _read_object(user, repo.id, "tree", payload.get("tree")) or {}
    files = {}
    for path, blob_oid in (tree.get("files") or {}).items():
        blob = _read_object(user, repo.id, "blob", blob_oid) or {}
        files[path] = blob.get("content", "")
    return files


def _ancestor_chain(user, repo, oid, limit=1000):
    # Walk the full commit DAG, not only the first parent, so merge commits
    # retain reachable history from both sides.
    result = []
    seen = set()
    pending = deque([oid] if oid else [])
    while pending and len(result) < limit:
        current = pending.popleft()
        if not current or current in seen:
            continue
        seen.add(current)
        result.append(current)
        payload = _read_object(user, repo.id, "commit", current) or {}
        parents = payload.get("parents") or ([payload.get("parent")] if payload.get("parent") else [])
        pending.extend(parent for parent in parents if parent and parent not in seen)
    return result


def _branch_heads(user, repo):
    result = {}
    for row in _commits(user, repo.id):
        meta = row.metadata or {}
        branch = meta.get("branch")
        oid = meta.get("oid")
        if branch and branch not in result:
            result[branch] = oid
    return result


def _three_way(base, ours, theirs):
    merged = dict(base)
    conflicts = []
    keys = set(base) | set(ours) | set(theirs)
    for key in keys:
        b, o, t = base.get(key), ours.get(key), theirs.get(key)
        if o == t:
            if o is None:
                merged.pop(key, None)
            else:
                merged[key] = o
        elif o == b:
            if t is None:
                merged.pop(key, None)
            else:
                merged[key] = t
        elif t == b:
            if o is None:
                merged.pop(key, None)
            else:
                merged[key] = o
        else:
            conflicts.append(key)
    return merged, conflicts


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def refs_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    return Response({"head": _head(request.user, repo, repo.framework or "main"), "branches": _branch_heads(request.user, repo)})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def native_commit_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    branch = str(request.data.get("branch") if request.method == "POST" else request.query_params.get("branch") or repo.framework or "main").strip()
    if not SAFE_BRANCH.fullmatch(branch):
        return Response({"error": "Invalid branch."}, status=400)
    if request.method == "GET":
        rows = _commits(request.user, repo.id, branch)[:100]
        return Response([{"id": r.metadata.get("oid"), "message": r.message, "parent": r.metadata.get("payload", {}).get("parent"), "tree": r.metadata.get("payload", {}).get("tree"), "branch": branch, "created_at": r.created_at} for r in rows])
    message = str(request.data.get("message") or "").strip()
    if not message or len(message) > 500:
        return Response({"error": "Commit message is required."}, status=400)
    with transaction.atomic():
        # Every repository writer must serialize on this row before reading
        # branch heads or mutating the working tree. Read the default working
        # tree only after acquiring the lock to avoid committing a stale copy.
        repo = _locked_repo(request.user, repo.pk)
        files = request.data.get("files", repo.files or {})
        try:
            files = _files(files)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        parent = _head(request.user, repo, branch)
        oid, tree, created = _commit(request.user, repo, branch, message, files, parent)
        repo.framework = branch
        repo.files = _files(files)
        # CodeWorkspace.save() owns revision increments. Do not increment here
        # as well or one mutation appears as two revisions to the editor.
        repo.save()
    return Response({"commit": {"id": oid, "tree": tree, "parent": parent, "branch": branch, "created": created}}, status=201 if created else 200)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def native_branch_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    if request.method == "GET":
        return Response({"branches": _branch_heads(request.user, repo), "current": repo.framework or "main"})
    branch = str(request.data.get("branch") or "").strip()
    if not SAFE_BRANCH.fullmatch(branch):
        return Response({"error": "Invalid branch."}, status=400)
    source = str(request.data.get("from") or repo.framework or "main")
    with transaction.atomic():
        repo = _locked_repo(request.user, repo.pk)
        source_head = _head(request.user, repo, source)
        if not source_head:
            source_files = _files(repo.files or {})
            source_head, _, _ = _commit(request.user, repo, source, "Initial repository snapshot", source_files, None, request.user.username)
        if branch not in _branch_heads(request.user, repo):
            files = _commit_files(request.user, repo, source_head)
            _commit(request.user, repo, branch, f"Branch {branch} from {source}", files, source_head, request.user.username)
        repo.framework = branch
        repo.files = _commit_files(request.user, repo, _head(request.user, repo, branch) or source_head)
        repo.save()
        branch_head = _head(request.user, repo, branch)
    return Response({"branch": branch, "head": branch_head})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def native_merge_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    source = str(request.data.get("source") or "").strip()
    target = str(request.data.get("target") or repo.framework or "main").strip()
    with transaction.atomic():
        # Re-read both branch heads only after taking the same repository lock
        # used by native commits and branch changes.
        repo = _locked_repo(request.user, repo.pk)
        source_head, target_head = _head(request.user, repo, source), _head(request.user, repo, target)
        if not source_head or not target_head:
            return Response({"error": "Both branches must contain commits."}, status=400)
        if source_head == target_head:
            return Response({"merged": True, "message": "Already up to date.", "head": target_head})
        source_chain = _ancestor_chain(request.user, repo, source_head)
        target_chain = set(_ancestor_chain(request.user, repo, target_head))
        base = next((oid for oid in source_chain if oid in target_chain), None)
        if not base:
            return Response({"error": "No common ancestor."}, status=409)
        merged, conflicts = _three_way(_commit_files(request.user, repo, base), _commit_files(request.user, repo, target_head), _commit_files(request.user, repo, source_head))
        if conflicts:
            return Response({"merged": False, "conflicts": conflicts, "base": base, "ours": target_head, "theirs": source_head}, status=409)
        merge_message = str(request.data.get("message") or f"Merge {source} into {target}").strip()
        if not merge_message or len(merge_message) > 500:
            return Response({"error": "Merge message must be between 1 and 500 characters."}, status=400)
        oid, tree, _ = _commit(
            request.user,
            repo,
            target,
            merge_message,
            merged,
            target_head,
            parents=[target_head, source_head],
        )
        repo.framework = target
        repo.files = merged
        repo.save()
    return Response({"merged": True, "head": oid, "tree": tree, "branch": target})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def native_diff_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    branch = str(request.query_params.get("branch") or repo.framework or "main")
    head = _head(request.user, repo, branch)
    if not head:
        return Response({"branch": branch, "files": [], "head": None})
    payload = _read_object(request.user, repo.id, "commit", head) or {}
    parent = payload.get("parent")
    if not parent:
        return Response({"branch": branch, "files": [{"path": path, "added": True, "deleted": False} for path in _commit_files(request.user, repo, head)], "head": head})
    before, after = _commit_files(request.user, repo, parent), _commit_files(request.user, repo, head)
    changed = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            changed.append({"path": path, "added": path not in before, "deleted": path not in after})
    return Response({"branch": branch, "files": changed, "head": head, "parent": parent})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def native_tag_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    name = str(request.data.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", name):
        return Response({"error": "Invalid tag name."}, status=400)
    with transaction.atomic():
        repo = _locked_repo(request.user, repo.pk)
        head = str(request.data.get("commit") or _head(request.user, repo, repo.framework or "main") or "").strip()
        if not head:
            return Response({"error": "Repository has no commits."}, status=400)
        if not _read_object(request.user, repo.id, "commit", head):
            return Response({"error": "Commit does not exist in this repository."}, status=404)
        for row in _objects(request.user, repo.id, "tag"):
            payload = (row.metadata or {}).get("payload") or {}
            if payload.get("name") == name:
                if payload.get("commit") == head:
                    return Response({"name": name, "commit": head}, status=200)
                return Response({"error": "Tag name already exists."}, status=409)
        _write_object(request.user, repo.id, "tag", {"name": name, "commit": head})
    return Response({"name": name, "commit": head}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def native_tag_list_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    tags = []
    for row in _objects(request.user, repo.id, "tag"):
        payload = (row.metadata or {}).get("payload") or {}
        tags.append({"name": payload.get("name"), "commit": payload.get("commit")})
    return Response(tags)


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def native_members_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    if request.method == "GET":
        rows = _member_rows(repo)
        return Response([{"user_id": (r.metadata or {}).get("user_id"), "username": (r.metadata or {}).get("username"), "revoked": (r.metadata or {}).get("revoked", False), "created_at": r.created_at} for r in rows])
    with transaction.atomic():
        # Membership changes share the repository lock with collaborator writes.
        # This makes revocation linearizable with _locked_repo membership checks.
        repo = _locked_repo(request.user, repo.pk)
        if not _is_owner(request.user, repo):
            return Response({"error": "Only the repository owner can manage members."}, status=403)
        user_id = request.data.get("user_id")
        try:
            member = User.objects.get(pk=user_id)
        except (User.DoesNotExist, TypeError, ValueError):
            return Response({"error": "User not found."}, status=404)
        if request.method == "POST":
            Activity.objects.create(actor=request.user, verb="added repository member", message=member.username, related_type="repo_member", related_id=repo.id, metadata={"user_id": member.id, "username": member.username, "revoked": False})
            return Response({"user_id": member.id, "username": member.username, "revoked": False}, status=201)
        Activity.objects.create(actor=request.user, verb="revoked repository member", message=member.username, related_type="repo_member", related_id=repo.id, metadata={"user_id": member.id, "username": member.username, "revoked": True})
    return Response(status=204)

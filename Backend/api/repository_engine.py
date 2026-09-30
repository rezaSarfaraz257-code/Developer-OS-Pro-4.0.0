"""Native repository engine for Developer OS.

This layer keeps GitHub optional. It uses content-addressed blob/tree/commit
records stored by Developer OS itself, while preserving the existing
snapshot repository API for backward compatibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from django.db import transaction
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
    member = Activity.objects.filter(
        related_type="repo_member",
        related_id=repo.id,
        metadata__user_id=user.id,
        metadata__revoked=False,
    ).exists()
    if not member:
        raise CodeWorkspace.DoesNotExist
    return repo


def _is_owner(user, repo):
    return repo.owner_id == user.id


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
    qs = Activity.objects.filter(actor=user, related_type=f"repo_object:{kind}" if kind else "")
    if kind is None:
        qs = Activity.objects.filter(actor=user, related_type__startswith="repo_object:")
    return qs.filter(related_id=repo_id).order_by("-created_at", "-id")


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
    row = _commits(user, repo.id, branch).first()
    return (row.metadata or {}).get("oid") if row else None


def _commit(user, repo, branch, message, files, parent=None, author=None):
    files = _files(files)
    tree = _tree(user, repo.id, files)
    payload = {
        "tree": tree,
        "parent": parent,
        "branch": branch,
        "message": message,
        "author": author or user.username,
    }
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
    result = []
    seen = set()
    while oid and oid not in seen and len(result) < limit:
        seen.add(oid)
        result.append(oid)
        payload = _read_object(user, repo.id, "commit", oid) or {}
        oid = payload.get("parent")
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
    files = request.data.get("files", repo.files or {})
    parent = _head(request.user, repo, branch)
    with transaction.atomic():
        oid, tree, created = _commit(request.user, repo, branch, message, files, parent)
        repo.framework = branch
        repo.files = _files(files)
        repo.revision = int(repo.revision or 1) + 1
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
    source_head = _head(request.user, repo, source)
    if not source_head:
        return Response({"error": "Source branch has no commits."}, status=404)
    if branch not in _branch_heads(request.user, repo):
        files = _commit_files(request.user, repo, source_head)
        _commit(request.user, repo, branch, f"Branch {branch} from {source}", files, source_head, request.user.username)
    repo.framework = branch
    repo.files = _commit_files(request.user, repo, _head(request.user, repo, branch) or source_head)
    repo.save()
    return Response({"branch": branch, "head": _head(request.user, repo, branch)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def native_merge_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    source = str(request.data.get("source") or "").strip()
    target = str(request.data.get("target") or repo.framework or "main").strip()
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
    merge_message = str(request.data.get("message") or f"Merge {source} into {target}")
    oid, tree, _ = _commit(request.user, repo, target, merge_message, merged, target_head)
    merge_row = _commits(request.user, repo.id, target).filter(metadata__oid=oid).first()
    if merge_row:
        meta = merge_row.metadata or {}
        payload = meta.get("payload") or {}
        payload["second_parent"] = source_head
        meta["payload"] = payload
        merge_row.metadata = meta
        merge_row.save(update_fields=["metadata"])
    repo.framework, repo.files = target, merged
    repo.save()
    return Response({"merged": True, "commit": {"id": oid, "tree": tree, "parent": target_head, "second_parent": source_head}})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def native_diff_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    branch = str(request.query_params.get("branch") or repo.framework or "main")
    rows = list(_commits(request.user, repo.id, branch)[:2])
    if not rows:
        return Response({"branch": branch, "files": []})
    current = _commit_files(request.user, repo, rows[0].metadata["oid"])
    parent_oid = (rows[0].metadata.get("payload") or {}).get("parent")
    previous = _commit_files(request.user, repo, parent_oid) if parent_oid else {}
    changes = []
    for path in sorted(set(current) | set(previous)):
        if path not in previous:
            changes.append({"path": path, "status": "added"})
        elif path not in current:
            changes.append({"path": path, "status": "deleted"})
        elif current[path] != previous[path]:
            changes.append({"path": path, "status": "modified"})
    return Response({"branch": branch, "head": rows[0].metadata["oid"], "files": changes})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def native_tag_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    name = str(request.data.get("name") or "").strip()
    branch = str(request.data.get("branch") or repo.framework or "main").strip()
    head = _head(request.user, repo, branch)
    if not name or not head or not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", name):
        return Response({"error": "Valid tag name and branch are required."}, status=400)
    _write_object(request.user, repo.id, "tag", {"name": name, "target": head, "branch": branch})
    return Response({"tag": name, "target": head}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def native_tag_list_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    tags = []
    for row in _objects(request.user, repo.id, "tag"):
        p = row.metadata.get("payload", {})
        tags.append(p)
    return Response(tags)


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def native_members_api(request, pk):
    try:
        repo = _repo(request.user, pk)
    except CodeWorkspace.DoesNotExist:
        return Response({"error": "Repository not found."}, status=404)
    if request.method == "GET":
        active = {}
        for row in _member_rows(repo):
            meta = row.metadata or {}
            uid = meta.get("user_id")
            if uid and uid not in active and not meta.get("revoked"):
                active[uid] = {"user_id": uid, "username": meta.get("username"), "role": meta.get("role", "viewer")}
        return Response(list(active.values()))
    if not _is_owner(request.user, repo):
        return Response({"error": "Only the repository owner can manage members."}, status=403)
    username = str(request.data.get("username") or "").strip()
    role = str(request.data.get("role") or "viewer").strip()
    if role not in {"admin", "developer", "viewer"}:
        return Response({"error": "Invalid repository role."}, status=400)
    target = User.objects.filter(username=username).first()
    if not target or target.id == repo.owner_id:
        return Response({"error": "User not found or already owner."}, status=404)
    if request.method == "DELETE":
        Activity.objects.create(actor=request.user, verb="revoked repository member", message=username, related_type="repo_member", related_id=repo.id, metadata={"user_id": target.id, "username": target.username, "role": role, "revoked": True})
        return Response(status=204)
    Activity.objects.create(actor=request.user, verb="added repository member", message=username, related_type="repo_member", related_id=repo.id, metadata={"user_id": target.id, "username": target.username, "role": role, "revoked": False})
    return Response({"user_id": target.id, "username": target.username, "role": role}, status=201)

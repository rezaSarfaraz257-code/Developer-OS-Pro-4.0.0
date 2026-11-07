"""Durable, approval-gated engineering-agent planning runtime.

Planning runs on the existing BackgroundJob worker. This module deliberately
creates plans and dependency graphs only; it never executes shell commands,
writes workspace files, deploys, or changes repository state.
"""
from __future__ import annotations

import json
import re
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AgentRun, AgentTask, AIConversation, AIMessage, AuditLog, BackgroundJob

User = get_user_model()
_AGENT_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,120}$")
_MAX_TASKS = 12
AGENT_SPECIALIST_ROLES = {
    "product_manager": "Product Manager: clarify scope, requirements, milestones, and acceptance criteria",
    "architect": "Software Architect: inspect system boundaries, architecture, and integration risks",
    "frontend": "Frontend Engineer: own accessible UI, client state, and frontend integration",
    "backend": "Backend Engineer: own APIs, services, permissions, and backend behavior",
    "database": "Database Engineer: own schema, migrations, query behavior, and data integrity",
    "ui_ux": "UI/UX Designer: own interaction design, usability, and visual consistency",
    "testing": "Test Engineer: own regression strategy, edge cases, and verification evidence",
    "security": "Security Engineer: own threat modeling, authorization, secrets, and secure defaults",
    "devops": "DevOps Engineer: own CI/CD, deployment configuration, observability, and rollback",
    "debugger": "Debugger: isolate failures, reproduce defects, and propose minimal fixes",
    "code_reviewer": "Code Reviewer: independently review correctness, maintainability, and regressions",
    "documentation": "Technical Writer: maintain accurate developer and user documentation",
}
# Server-owned, read-only capability contracts. These are metadata only; no
# capability below is a shell, deployment, workspace-write, or arbitrary-tool grant.
AGENT_SPECIALIST_CAPABILITIES = {
    "product_manager": ["inspect_workspace", "draft_requirements"],
    "architect": ["inspect_workspace", "analyze_dependencies", "draft_architecture"],
    "frontend": ["inspect_workspace", "analyze_frontend", "review_accessibility"],
    "backend": ["inspect_workspace", "analyze_backend", "review_api_contracts"],
    "database": ["inspect_workspace", "analyze_schema", "review_data_integrity"],
    "ui_ux": ["inspect_workspace", "review_accessibility", "draft_interaction_spec"],
    "testing": ["inspect_workspace", "design_tests", "review_test_coverage"],
    "security": ["inspect_workspace", "review_security", "identify_secret_exposure"],
    "devops": ["inspect_workspace", "review_ci_configuration", "review_deployment_config"],
    "debugger": ["inspect_workspace", "analyze_failures", "draft_debug_plan"],
    "code_reviewer": ["inspect_workspace", "review_changes", "identify_regressions"],
    "documentation": ["inspect_workspace", "draft_documentation"],
}


def _redact_sensitive_content(value):
    """Remove common credential material before workspace text reaches an AI provider."""
    if not isinstance(value, str) or not value:
        return value
    patterns = [
        # Named credentials and password-bearing connection strings.
        (re.compile(r'(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?key|password|passwd|authorization|private[_-]?key)\b\s*[:=]\s*)([\"\']?)([^\s,;\"\']{8,})'), r'\1\2[REDACTED]'),
        # Well-known provider token formats.
        (re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'), '[REDACTED_AWS_KEY]'),
        (re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'), '[REDACTED_GITHUB_TOKEN]'),
        (re.compile(r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|rk_live_[A-Za-z0-9]{16,}|sk_live_[A-Za-z0-9]{16,})\b'), '[REDACTED_PROVIDER_KEY]'),
        (re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'), '[REDACTED_SLACK_TOKEN]'),
        (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'), '[REDACTED_PRIVATE_KEY]'),
    ]
    redacted = value
    for pattern, replacement in patterns:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _workspace_access(workspace_id, user):
    from .views import _workspace_for_user, _workspace_write_allowed

    workspace = _workspace_for_user(workspace_id, user)
    if not _workspace_write_allowed(workspace, user):
        raise Http404
    return workspace


def _run_payload(run):
    tasks = list(run.tasks.order_by("sequence", "id"))
    return {
        "id": run.id,
        "goal": run.goal,
        "status": run.status,
        "workspace": run.workspace_id,
        "workspace_revision": run.workspace_revision,
        "conversation": run.conversation_id,
        "plan": run.plan,
        "execution_state": "plan_only_no_workspace_mutations",
        "error": run.error,
        "approved_at": run.approved_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "tasks": [
            {
                "id": task.id,
                "key": task.key,
                "title": task.title,
                "description": task.description,
                "agent_role": task.agent_role,
                "allowed_capabilities": AGENT_SPECIALIST_CAPABILITIES.get(task.agent_role, ["inspect_workspace"]),
                "status": task.status,
                "sequence": task.sequence,
                "depends_on": list(task.depends_on.order_by("sequence").values_list("key", flat=True)),
            }
            for task in tasks
        ],
    }


def _validate_plan(raw):
    if not isinstance(raw, dict):
        raise ValueError("Agent planner returned an invalid plan object.")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= _MAX_TASKS:
        raise ValueError("Agent plan must contain between 1 and 12 tasks.")

    normalized = []
    keys = set()
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            raise ValueError("Agent plan contains an invalid task.")
        key = str(item.get("id") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        agent_role = str(item.get("agent_role") or "architect").strip().lower()
        dependencies = item.get("depends_on") or []
        if not _AGENT_KEY_RE.fullmatch(key) or key in keys:
            raise ValueError("Agent plan contains an invalid or duplicate task ID.")
        if not title or len(title) > 180 or len(description) > 2000:
            raise ValueError("Agent task title or description is invalid.")
        if agent_role not in AGENT_SPECIALIST_ROLES:
            raise ValueError("Agent task references an unsupported specialist role.")
        if not isinstance(dependencies, list) or len(dependencies) > _MAX_TASKS:
            raise ValueError("Agent task dependencies are invalid.")
        normalized.append({
            "key": key,
            "title": title,
            "description": description,
            "agent_role": agent_role,
            "depends_on": [str(value).strip().lower() for value in dependencies],
            "sequence": index,
        })
        keys.add(key)

    positions = {item["key"]: item["sequence"] for item in normalized}
    for item in normalized:
        if len(item["depends_on"]) != len(set(item["depends_on"])):
            raise ValueError("Agent task contains duplicate dependencies.")
        if any(dep not in keys or dep == item["key"] for dep in item["depends_on"]):
            raise ValueError("Agent task references an unknown or invalid dependency.")
    # Kahn's algorithm rejects cycles before checking the provider's order.
    pending = {item["key"]: set(item["depends_on"]) for item in normalized}
    resolved = set()
    while len(resolved) < len(pending):
        ready = {key for key, deps in pending.items() if key not in resolved and deps <= resolved}
        if not ready:
            raise ValueError("Agent plan contains a dependency cycle.")
        resolved.update(ready)

    if any(positions[dep] >= item["sequence"] for item in normalized for dep in item["depends_on"]):
        raise ValueError("Agent plan tasks must be ordered before their dependents.")

    return {
        "summary": str(raw.get("summary") or "").strip()[:2000],
        "risks": [str(value)[:500] for value in raw.get("risks", [])[:12]] if isinstance(raw.get("risks", []), list) else [],
        "acceptance_criteria": [str(value)[:500] for value in raw.get("acceptance_criteria", [])[:12]] if isinstance(raw.get("acceptance_criteria", []), list) else [],
        "tasks": normalized,
    }


def build_agent_plan(run_id):
    """Worker entry point: produce and persist a bounded DAG, never execute it."""
    from .ai_engine import _call_provider

    with transaction.atomic():
        run = AgentRun.objects.select_for_update().select_related(
            "owner", "workspace"
        ).get(pk=run_id)
        if run.status == "canceled":
            return {"run_id": run.id, "status": "canceled"}
        # A second delivery must not start a duplicate provider request while
        # the first worker still owns the planning transition. Stale-lease
        # recovery explicitly resets abandoned runs to queued before retry.
        if run.status == "planning":
            return {"run_id": run.id, "status": "planning"}
        if run.status in {"awaiting_approval", "approved", "running", "succeeded", "failed"}:
            return {"run_id": run.id, "status": run.status}
        run.status = "planning"
        run.started_at = run.started_at or timezone.now()
        run.error = ""
        run.save(update_fields=["status", "started_at", "error", "updated_at"])
        workspace_id = run.workspace_id
        owner_id = run.owner_id
        conversation_id = run.conversation_id
        goal = run.goal
        revision = run.workspace_revision

    from .models import CodeWorkspace
    from .views import _workspace_write_allowed
    workspace = CodeWorkspace.objects.get(pk=workspace_id)
    if not _workspace_write_allowed(workspace, run.owner):
        raise RuntimeError("Workspace access was revoked before agent planning began.")
    if workspace.revision != revision:
        raise RuntimeError("Workspace changed before agent planning began. Create a new run from the latest revision.")

    files = workspace.files if isinstance(workspace.files, dict) else {}
    safe_files = {}
    total_chars = 0
    for path, value in list(files.items())[:120]:
        normalized_path = str(path).replace("\\", "/").lower()
        parts = normalized_path.split("/")
        name = parts[-1] if parts else ""
        sensitive_name = (
            name == ".env"
            or name.startswith(".env.")
            or name in {".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials", "secrets.json", "service-account.json"}
            or name.startswith(("credentials.", "secret.", "secrets."))
            or any(part in {".aws", ".ssh", "secrets", "credentials"} for part in parts[:-1])
            or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"))
        )
        if sensitive_name:
            continue
        if not isinstance(value, str):
            continue
        remaining = 24000 - total_chars
        if remaining <= 0:
            break
        bounded = value[:min(1800, remaining)]
        bounded = _redact_sensitive_content(bounded)
        safe_files[str(path)[:500]] = bounded
        total_chars += len(bounded)

    context = {
        "workspace": {
            "id": workspace.id,
            "name": workspace.name,
            "language": workspace.language,
            "framework": workspace.framework,
            "runtime": workspace.runtime,
            "revision": workspace.revision,
            "file_inventory": list(safe_files.keys()),
            "file_samples": safe_files,
            "secrets_excluded": True,
        },
        "planning_contract": {
            "max_tasks": _MAX_TASKS,
            "no_execution": True,
            "human_approval_required": True,
        },
    }
    history = []
    if conversation_id:
        history = list(
            AIMessage.objects.filter(conversation_id=conversation_id, conversation__owner_id=owner_id)
            .order_by("-created_at", "-id")
            .values("role", "content")[:8]
        )[::-1]

    prompt = (
        "Create a safe, dependency-aware engineering implementation plan for the user's goal. "
        "Return ONLY valid JSON with keys summary, risks, acceptance_criteria, tasks. "
        "tasks must be an array of 1 to 12 objects with id, title, description, agent_role, depends_on. "
        "agent_role must be one of: " + ", ".join(AGENT_SPECIALIST_ROLES) + ". "
        "Assign each task to the most relevant specialist. Use unique lowercase snake_case IDs. "
        "depends_on is an array of task IDs. "
        "Order tasks topologically. Include inspection and verification tasks when relevant. "
        "Do not claim code was changed, commands were run, or tests passed. "
        "Do not request or expose secrets. No shell commands or deployment actions.\n\n"
        f"GOAL:\n{goal}"
    )
    answer, provider_error = _call_provider(prompt, context, history, "autonomous engineering plan")
    if provider_error or not answer:
        raise RuntimeError("AI planning provider is unavailable; no plan was generated.")

    text = answer.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        plan = _validate_plan(json.loads(text))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"AI planner returned an invalid task graph: {str(exc)[:300]}") from exc

    with transaction.atomic():
        run = AgentRun.objects.select_for_update().select_related("owner").get(pk=run_id)
        if run.status == "canceled":
            return {"run_id": run.id, "status": "canceled"}
        workspace = CodeWorkspace.objects.select_for_update().get(pk=run.workspace_id)
        if not _workspace_write_allowed(workspace, run.owner):
            raise RuntimeError("Workspace access was revoked while the plan was being generated.")
        if workspace.revision != revision:
            raise RuntimeError("Workspace changed while the plan was being generated; review a fresh plan.")
        run.tasks.all().delete()
        task_objects = {}
        for item in plan["tasks"]:
            task_objects[item["key"]] = AgentTask.objects.create(
                run=run,
                key=item["key"],
                title=item["title"],
                description=item["description"],
                agent_role=item["agent_role"],
                sequence=item["sequence"],
                status="ready" if not item["depends_on"] else "blocked",
            )
        for item in plan["tasks"]:
            task_objects[item["key"]].depends_on.set(
                [task_objects[key] for key in item["depends_on"]]
            )
        run.plan = {
            "summary": plan["summary"],
            "risks": plan["risks"],
            "acceptance_criteria": plan["acceptance_criteria"],
            "task_count": len(plan["tasks"]),
            "requires_human_approval": True,
        }
        run.status = "awaiting_approval"
        run.error = ""
        run.finished_at = timezone.now()
        run.save(update_fields=["plan", "status", "error", "finished_at", "updated_at"])
        AuditLog.objects.create(
            user_id=run.owner_id,
            action="agent_run.plan_ready",
            target_type="AgentRun",
            target_id=str(run.id),
            metadata={"task_count": len(plan["tasks"]), "workspace_revision": revision},
        )
        if run.conversation_id:
            AIMessage.objects.create(
                conversation_id=run.conversation_id,
                role="assistant",
                content=json.dumps(run.plan, ensure_ascii=False),
                context={"agent_run_id": run.id, "task_graph": plan["tasks"]},
            )
    return {"run_id": run_id, "status": "awaiting_approval", "task_count": len(plan["tasks"])}


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_run_create_api(request):
    goal = str(request.data.get("goal") or "").strip()
    if not goal or len(goal) > 12000:
        return Response({"error": "goal is required and must be at most 12000 characters."}, status=400)
    workspace_id = request.data.get("workspace")
    if not workspace_id:
        return Response({"error": "workspace is required."}, status=400)
    try:
        workspace_id = int(workspace_id)
        if workspace_id < 1:
            raise ValueError
    except (TypeError, ValueError):
        return Response({"error": "workspace must be a positive integer ID."}, status=400)

    try:
        workspace = _workspace_access(workspace_id, request.user)
    except Http404:
        return Response({"error": "Workspace not found or access denied."}, status=404)

    idempotency_key = str(
        request.headers.get("Idempotency-Key")
        or request.data.get("idempotency_key")
        or uuid.uuid4()
    ).strip()
    if not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
        return Response({"error": "Invalid idempotency key."}, status=400)

    conversation_id = request.data.get("conversation")
    if conversation_id:
        conversation = get_object_or_404(AIConversation, pk=conversation_id, owner=request.user)
        if conversation.project_id and workspace.project_id not in (None, conversation.project_id):
            return Response({"error": "Conversation and workspace projects do not match."}, status=400)
    else:
        conversation = None

    from .views import _consume_usage
    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        existing = AgentRun.objects.filter(owner=request.user, idempotency_key=idempotency_key).first()
        if existing:
            if existing.workspace_id != workspace.id or existing.goal != goal:
                return Response({"error": "Idempotency key was already used for a different agent run."}, status=409)
            return Response(_run_payload(existing), status=200)

        allowed, used, limit, plan_name = _consume_usage(request.user, "ai_messages_month", 1)
        if not allowed:
            return Response({
                "error": "Monthly AI usage limit reached.",
                "plan": plan_name,
                "used": used,
                "limit": limit,
            }, status=429)

        if conversation is None:
            conversation = AIConversation.objects.create(
                owner=request.user,
                project_id=workspace.project_id,
                title=goal[:200],
            )
        run = AgentRun.objects.create(
            owner=request.user,
            workspace=workspace,
            conversation=conversation,
            goal=goal,
            idempotency_key=idempotency_key,
            workspace_revision=workspace.revision,
        )
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload={"run_id": run.id},
            idempotency_key=f"agent-plan:{run.id}",
            max_attempts=2,
        )
        AIMessage.objects.create(
            conversation=conversation,
            role="user",
            content=goal,
            context={"agent_run_id": run.id, "workspace_id": workspace.id},
        )
        AuditLog.objects.create(
            user=request.user,
            action="agent_run.created",
            target_type="AgentRun",
            target_id=str(run.id),
            metadata={"workspace_id": workspace.id, "workspace_revision": workspace.revision},
        )
        return Response({**_run_payload(run), "job_id": job.id}, status=202)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_run_detail_api(request, pk):
    run = get_object_or_404(AgentRun.objects.prefetch_related("tasks__depends_on"), pk=pk, owner=request.user)
    return Response(_run_payload(run))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_run_cancel_api(request, pk):
    with transaction.atomic():
        run = get_object_or_404(AgentRun.objects.select_for_update(), pk=pk, owner=request.user)
        if run.status in {"succeeded", "failed", "canceled"}:
            return Response(_run_payload(run), status=409)
        previous_status = run.status
        run.status = "canceled"
        run.finished_at = timezone.now()
        run.error = ""
        run.tasks.exclude(status__in=["succeeded", "failed", "canceled"]).update(status="canceled")
        run.save(update_fields=["status", "finished_at", "error", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action="agent_run.canceled",
            target_type="AgentRun",
            target_id=str(run.id),
            metadata={"previous_status": previous_status},
        )
        BackgroundJob.objects.filter(
            kind="ai_agent_plan",
            payload__run_id=run.id,
            status="queued",
        ).update(status="canceled", finished_at=timezone.now())
    return Response(_run_payload(run))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_run_approve_api(request, pk):
    from .views import _workspace_for_user, _workspace_write_allowed

    with transaction.atomic():
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related("workspace"),
            pk=pk,
            owner=request.user,
        )
        if run.status != "awaiting_approval":
            return Response({"error": "Only plans awaiting approval can be approved."}, status=409)
        workspace = _workspace_for_user(run.workspace_id, request.user, for_update=True)
        if not _workspace_write_allowed(workspace, request.user):
            return Response({"error": "Workspace write permission is required."}, status=403)
        if workspace.revision != run.workspace_revision:
            return Response({"error": "Workspace changed after planning. Generate a fresh plan.", "code": "stale_workspace"}, status=409)
        run.status = "approved"
        run.approved_at = timezone.now()
        run.save(update_fields=["status", "approved_at", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action="agent_run.plan_approved",
            target_type="AgentRun",
            target_id=str(run.id),
            metadata={"workspace_revision": run.workspace_revision},
        )
    return Response(_run_payload(run))

"""Approval-linked, durable Runner verification for engineering AgentTasks.

Reuses the existing AI patch-apply approval and Runner synchronization outbox.
It creates no second patch format, approval token, or Runner transport.
"""
from __future__ import annotations

import hashlib
import re

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AgentRun, AgentTask, AuditLog, BackgroundJob

# Never forward a user/model-provided shell string to Runner.
VERIFICATION_PROFILES = {
    "python_pytest": "python -m pytest -q",
    "python_unittest": "python -m unittest discover -v",
    "node_test": "npm test",
    "node_build": "npm run build",
    "node_lint": "npm run lint",
}


def _queue_agent_runner_resync(workspace, actor, verification_job_id):
    """Queue an authoritative snapshot after an execution may have changed Runner state."""
    key = f"agent-runner-resync:{workspace.id}:{workspace.revision}:{verification_job_id}"
    job, _ = BackgroundJob.objects.get_or_create(
        kind="ai_agent_runner_sync",
        idempotency_key=key,
        defaults={
            "payload": {
                "workspace_id": workspace.id,
                "owner_id": actor.id,
                "requested_revision": workspace.revision,
                "reason": "agent_verification_reconciliation",
            },
            "max_attempts": 8,
        },
    )
    return job



# Fixed diagnostic enums only: never persist filenames, source lines, package
# names, environment values, or arbitrary process output.
_DIAGNOSTIC_PATTERNS = {
    "python_pytest": (
        ("python_import_error", re.compile(r"(?m)^\s*(?:ModuleNotFoundError|ImportError):")),
        ("python_syntax_error", re.compile(r"(?m)^\s*SyntaxError:")),
        ("python_test_failure", re.compile(r"(?m)^FAILED\s+.+::.+|=+\s+\d+\s+failed")),
    ),
    "python_unittest": (
        ("python_import_error", re.compile(r"(?m)^\s*(?:ModuleNotFoundError|ImportError):")),
        ("python_syntax_error", re.compile(r"(?m)^\s*SyntaxError:")),
        ("python_test_failure", re.compile(r"(?m)^\s*(?:FAIL|ERROR):\s")),
    ),
    "node_test": (
        ("node_module_resolution_error", re.compile(r"(?m)(?:ERR_MODULE_NOT_FOUND|Cannot find module)")),
        ("node_test_failure", re.compile(r"(?m)^not ok\s+\d+\b")),
    ),
    "node_build": (
        ("node_module_resolution_error", re.compile(r"(?m)(?:ERR_MODULE_NOT_FOUND|Cannot find module)")),
        ("node_build_error", re.compile(r"(?m)^\s*(?:ERROR in |\[vite\].*error|error TS\d+:)")),
    ),
    "node_lint": (
        ("node_module_resolution_error", re.compile(r"(?m)(?:ERR_MODULE_NOT_FOUND|Cannot find module)")),
        ("node_lint_error", re.compile(r"(?m)\b\d+\s+problems?\s+\(\d+\s+errors?")),
    ),
}


def _safe_diagnostic_signals(profile, stdout, stderr, exit_code):
    """Return bounded, non-content diagnostic enums; never retain process output."""
    if type(exit_code) is not int or exit_code == 0:
        return []
    signals = []
    for source, output in (("stderr", stderr), ("stdout", stdout)):
        for code, pattern in _DIAGNOSTIC_PATTERNS.get(profile, ()):
            if pattern.search(output) and {"code": code, "source": source} not in signals:
                signals.append({"code": code, "source": source})
                if len(signals) == 4:
                    return signals
    return signals or [{"code": "command_failed", "source": "exit_status"}]

def _task_acceptance_criteria(task):
    criteria = task.acceptance_criteria if isinstance(task.acceptance_criteria, list) else []
    if not 1 <= len(criteria) <= 6:
        return None
    seen = set()
    for item in criteria:
        if not isinstance(item, str):
            return None
        value = item.strip()
        if not value or len(value) > 500 or value.casefold() in seen:
            return None
        seen.add(value.casefold())
    return criteria


def _task_status_payload(task, job=None):
    return {
        "run_id": task.run_id,
        "task_id": task.id,
        "status": task.status,
        "acceptance_criteria": task.acceptance_criteria if isinstance(task.acceptance_criteria, list) else [],
        "verification_profile": task.execution_profile,
        "revision": task.execution_revision,
        "attempts": task.execution_attempts,
        "evidence": task.execution_evidence if isinstance(task.execution_evidence, dict) else {},
        "error": task.execution_error,
        "job": ({"id": job.id, "status": job.status, "attempts": job.attempts,
                 "max_attempts": job.max_attempts} if job else None),
    }


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_task_verification_start_api(request, pk, task_pk):
    """Queue verification only after a human-approved AI patch was saved and synced."""
    profile = str(request.data.get("profile") or "").strip()
    if profile not in VERIFICATION_PROFILES:
        return Response({"error": "Choose a supported verification profile."}, status=400)
    try:
        sync_job_id = int(request.data.get("sync_job_id"))
        revision = int(request.data.get("revision"))
        if sync_job_id < 1 or revision < 1:
            raise ValueError
    except (TypeError, ValueError):
        return Response({"error": "A successful Runner sync job and workspace revision are required."}, status=400)

    from .views import _workspace_for_user, _workspace_write_allowed

    with transaction.atomic():
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related("workspace"),
            pk=pk, owner=request.user,
        )
        task = get_object_or_404(AgentTask.objects.select_for_update(), pk=task_pk, run=run)
        # Status is not a substitute for DAG integrity: never execute a task
        # while any declared dependency is incomplete, even if stale state says ready.
        if task.depends_on.exclude(status="succeeded").exists():
            return Response({"error": "Complete every dependent task before Runner verification."}, status=409)
        if run.status not in {"approved", "running", "failed"}:
            return Response({"error": "Approve the Agent plan before verifying task execution."}, status=409)
        if (
            task.status == "running"
            and task.execution_revision == revision
            and task.execution_profile == profile
            and task.execution_job_id
        ):
            existing_job = task.execution_job
            return Response({**_task_status_payload(task, existing_job), "job_id": existing_job.id}, status=200)
        if task.status not in {"ready", "failed"}:
            return Response({"error": "Only a ready or failed task can enter Runner verification."}, status=409)
        if _task_acceptance_criteria(task) is None:
            return Response({"error": "Task-specific acceptance criteria are missing or invalid."}, status=409)
        if run.status == "failed" and task.status != "failed":
            return Response({"error": "A failed run can only resume by retrying one of its failed tasks."}, status=409)
        if task.status == "failed" and task.execution_revision is not None and revision <= task.execution_revision:
            return Response({"error": "A retry requires a newly approved and synchronized workspace revision."}, status=409)
        previous_evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        history = list(previous_evidence.get("history") or [])
        if task.status == "failed":
            if len(history) >= 3:
                return Response({"error": "The task reached its four-iteration verification limit; manual intervention is required."}, status=409)
            history.append({key: value for key, value in previous_evidence.items() if key != "history"})
        iteration = len(history) + 1
        history = history[-3:]
        workspace = _workspace_for_user(run.workspace_id, request.user, for_update=True)
        if not _workspace_write_allowed(workspace, request.user):
            return Response({"error": "Workspace write permission is required."}, status=403)
        if workspace.revision != revision or revision <= run.workspace_revision:
            return Response({"error": "The applied workspace revision is stale or does not follow this plan."}, status=409)

        sync_job = get_object_or_404(
            BackgroundJob.objects.select_for_update(),
            pk=sync_job_id, kind="ai_agent_runner_sync",
            payload__workspace_id=workspace.id, payload__owner_id=request.user.id,
        )
        sync_result = sync_job.result if isinstance(sync_job.result, dict) else {}
        if (
            sync_job.status != "succeeded"
            or sync_result.get("status") != "synced"
            or int(sync_result.get("revision") or 0) != revision
            or not AuditLog.objects.filter(
                user=request.user, action="ai_agent.runner_workspace_synced",
                target_type="CodeWorkspace", target_id=str(workspace.id),
                metadata__job_id=sync_job.id, metadata__revision=revision,
            ).exists()
        ):
            return Response({"error": "The exact workspace revision must finish Runner synchronization first."}, status=409)

        # The existing AI apply endpoint creates this audit record only after
        # its signed, single-use, revision-bound approval has been accepted.
        approved_patch = AuditLog.objects.filter(
            user=request.user, action="ai_agent.patch_applied",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata__previous_revision=revision - 1,
            metadata__revision=revision,
            metadata__approval="signed_single_use_revision_bound",
            metadata__agent_run_id=run.id,
            metadata__agent_task_id=task.id,
            created_at__gte=run.approved_at,
        ).order_by("-created_at", "-id").first()
        if not approved_patch:
            return Response({"error": "No human-approved AI patch is recorded for this workspace revision."}, status=403)
        if AgentTask.objects.filter(run__workspace=workspace, execution_revision=revision).exclude(pk=task.id).exists():
            return Response({"error": "This applied patch revision is already assigned to another Agent task."}, status=409)

        key = f"agent-task-verify:{run.id}:{task.id}:{revision}:{profile}"
        job, created = BackgroundJob.objects.get_or_create(
            kind="ai_agent_task_verify", idempotency_key=key,
            defaults={
                "payload": {
                    "run_id": run.id, "task_id": task.id, "workspace_id": workspace.id,
                    "owner_id": request.user.id, "revision": revision, "profile": profile,
                    "sync_job_id": sync_job.id,
                    "patch_audit_id": approved_patch.id,
                },
                # Runner execution is a POST and may have completed despite a
                # lost response. Do not replay an ambiguous command.
                "max_attempts": 1,
            },
        )
        if not created:
            return Response({"error": "A verification job already exists for this task and revision."}, status=409)

        task.status = "running"
        task.execution_job = job
        task.execution_revision = revision
        task.execution_profile = profile
        task.execution_attempts = 0
        task.execution_evidence = {
            "state": "queued", "sync_job_id": sync_job.id,
            "iteration": iteration, "history": history,
        }
        task.execution_error = ""
        task.save(update_fields=[
            "status", "execution_job", "execution_revision", "execution_profile",
            "execution_attempts", "execution_evidence", "execution_error", "updated_at",
        ])
        run.status = "running"
        run.started_at = run.started_at or timezone.now()
        run.finished_at = None
        run.save(update_fields=["status", "started_at", "finished_at", "updated_at"])
        AuditLog.objects.create(
            user=request.user, action="agent_task.runner_verification_queued",
            target_type="AgentTask", target_id=str(task.id),
            metadata={
                "run_id": run.id, "revision": revision, "profile": profile,
                "job_id": job.id, "sync_job_id": sync_job.id, "iteration": iteration,
            },
        )
    return Response({**_task_status_payload(task, job), "job_id": job.id}, status=202)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_task_verification_status_api(request, pk, task_pk):
    task = get_object_or_404(
        AgentTask.objects.select_related("run", "execution_job"),
        pk=task_pk, run_id=pk, run__owner=request.user,
    )
    return Response(_task_status_payload(task, task.execution_job))


def execute_agent_task_verification(job):
    """Execute one fixed verification command through the existing isolated Runner.

    Database locks protect the immutable revision snapshot and the final state
    transition only. The network call is deliberately outside the transaction
    so a slow Runner cannot pin workspace/task rows or starve worker leases.
    """
    from django.contrib.auth import get_user_model
    from .views import (
        _runner_request, _workspace_for_user, _workspace_payload,
        _workspace_write_allowed,
    )

    payload = job.payload if isinstance(job.payload, dict) else {}
    profile = payload.get("profile")
    command = VERIFICATION_PROFILES.get(profile)
    if not command:
        raise RuntimeError("Unsupported Agent verification profile.")

    actor = get_user_model().objects.filter(pk=payload.get("owner_id")).first()
    if not actor:
        raise RuntimeError("Agent task owner is no longer available.")

    with transaction.atomic():
        current_job = BackgroundJob.objects.select_for_update().filter(pk=job.pk).first()
        run = AgentRun.objects.select_for_update().filter(
            pk=payload.get("run_id"), owner_id=actor.id,
        ).first()
        task = AgentTask.objects.select_for_update().filter(
            pk=payload.get("task_id"), run=run,
        ).first() if run else None
        if (
            not current_job or current_job.status != "running"
            or not run or run.status == "canceled" or not task
        ):
            raise RuntimeError("Agent task verification lease is no longer current.")
        if task.status != "running" or task.execution_job_id != current_job.id:
            raise RuntimeError("Agent task verification lease is no longer current.")
        # Re-check the dependency graph at execution time, not only when the
        # API queues the job. A prerequisite can be canceled or otherwise
        # leave the succeeded state while this job waits in the worker queue.
        if task.depends_on.exclude(status="succeeded").exists():
            raise RuntimeError("Agent task dependencies are no longer satisfied.")
        if _task_acceptance_criteria(task) is None:
            raise RuntimeError("Task-specific acceptance criteria are missing or invalid.")

        workspace = _workspace_for_user(run.workspace_id, actor, for_update=True)
        if not _workspace_write_allowed(workspace, actor):
            raise RuntimeError("Workspace write permission was revoked before Runner verification.")
        if workspace.id != payload.get("workspace_id") or workspace.revision != payload.get("revision"):
            raise RuntimeError("Workspace changed before Runner verification.")

        sync_job = BackgroundJob.objects.select_for_update().filter(
            pk=payload.get("sync_job_id"), kind="ai_agent_runner_sync", status="succeeded",
            payload__workspace_id=workspace.id, payload__owner_id=actor.id,
        ).first()
        sync_result = sync_job.result if sync_job and isinstance(sync_job.result, dict) else {}
        sync_audited = bool(sync_job) and AuditLog.objects.filter(
            user=actor, action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata__job_id=sync_job.id, metadata__revision=workspace.revision,
        ).exists()
        if (
            not sync_job or sync_result.get("status") != "synced"
            or int(sync_result.get("revision") or 0) != workspace.revision
            or not sync_audited
        ):
            raise RuntimeError("The verified workspace revision is not synchronized to Runner.")

        # The signed-approval audit is checked again at execution time. Queueing
        # is not a durable authorization grant: if the approval record is
        # revoked/removed while the job waits, fail closed before contacting Runner.
        approved_patch = AuditLog.objects.filter(
            pk=payload.get("patch_audit_id"),
            user=actor, action="ai_agent.patch_applied",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata__previous_revision=workspace.revision - 1,
            metadata__revision=workspace.revision,
            metadata__approval="signed_single_use_revision_bound",
            metadata__agent_run_id=run.id,
            metadata__agent_task_id=task.id,
            created_at__gte=run.approved_at,
        ).exists()
        if not approved_patch:
            raise RuntimeError("The signed approval for this workspace revision is no longer valid.")

        revision = workspace.revision
        workspace_snapshot = _workspace_payload(workspace)
        # Reserve the Runner identifier durably before the outbound POST so a
        # concurrent user cancellation can terminate the exact process group.
        runner_execution_id = f"agent-{current_job.id}"
        current_job.payload = {**payload, "runner_execution_id": runner_execution_id}
        current_job.save(update_fields=["payload"])
        task.execution_attempts = current_job.attempts
        task.save(update_fields=["execution_attempts", "updated_at"])

    # This POST is intentionally outside transaction.atomic(). Runner has its
    # own per-workspace lock and per-execution isolation. Never replay an
    # ambiguous POST: this BackgroundJob is configured for one attempt.
    result, error = _runner_request(
        "POST", "/exec", {
            **workspace_snapshot, "command": command, "include_files": False,
            "execution_id": runner_execution_id,
        }, timeout=125
    )
    if error or not isinstance(result, dict):
        raise RuntimeError("Isolated Runner verification request failed.")

    runner_job_id = result.get("job_id")
    runner_status = result.get("status")
    if isinstance(runner_status, str):
        runner_status = runner_status.lower()
    exit_code = result.get("exit_code")
    duration_ms = result.get("duration_ms")
    if (
        not isinstance(runner_job_id, str)
        or not runner_job_id
        or len(runner_job_id) > 80
        or not runner_job_id.isascii()
        or not all(char.isalnum() or char in "-_" for char in runner_job_id)
        or runner_job_id != runner_execution_id
    ):
        raise RuntimeError("Runner returned an invalid execution job reference.")
    if not isinstance(runner_status, str) or runner_status not in {
        "completed", "failed", "timeout", "cancelled",
    }:
        raise RuntimeError("Runner returned an invalid execution status.")
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise RuntimeError("Runner returned malformed execution output.")
    # Reject oversized fields before allocating encoded copies, then enforce
    # the combined UTF-8 response budget at the control plane as well.
    if len(stdout) > 50_000 or len(stderr) > 50_000:
        raise RuntimeError("Runner execution output exceeded the control-plane limit.")
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    if len(stdout_bytes) + len(stderr_bytes) > 50_000:
        raise RuntimeError("Runner execution output exceeded the control-plane limit.")
    # Persist integrity fingerprints, not raw process output: test output can
    # contain user data or secrets. These stable digests support later
    # comparisons without copying that content into long-lived DB evidence.
    output_fingerprints = {
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "stdout_bytes": len(stdout_bytes),
        "stderr_bytes": len(stderr_bytes),
    }
    if runner_status == "cancelled":
        if exit_code is not None and type(exit_code) is not int:
            raise RuntimeError("Runner returned an invalid execution exit code.")
    elif type(exit_code) is not int:
        raise RuntimeError("Runner returned an invalid execution exit code.")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or not 0 <= duration_ms <= 120_000:
        duration_ms = None
    passed = runner_status == "completed" and exit_code == 0
    verified_at = timezone.now().isoformat()

    with transaction.atomic():
        current_job = BackgroundJob.objects.select_for_update().filter(pk=job.pk).first()
        run = AgentRun.objects.select_for_update().filter(
            pk=payload.get("run_id"), owner_id=actor.id,
        ).first()
        task = AgentTask.objects.select_for_update().filter(
            pk=payload.get("task_id"), run=run,
        ).first() if run else None
        if not current_job or current_job.status != "running" or not run or not task:
            return {"status": "verification_superseded", "task_id": payload.get("task_id")}
        if run.status == "canceled" or task.status == "canceled":
            resync_job = None
            try:
                canceled_workspace = _workspace_for_user(run.workspace_id, actor, for_update=True)
            except Http404:
                canceled_workspace = None
            if canceled_workspace and _workspace_write_allowed(canceled_workspace, actor):
                resync_job = _queue_agent_runner_resync(canceled_workspace, actor, current_job.id)
                AuditLog.objects.create(
                    user=actor, action="agent_task.runner_resync_after_cancel",
                    target_type="AgentTask", target_id=str(task.id),
                    metadata={
                        "run_id": run.id, "verification_job_id": current_job.id,
                        "resync_job_id": resync_job.id,
                        "revision": canceled_workspace.revision,
                    },
                )
            return {
                "status": "verification_canceled", "task_id": task.id,
                "resync_job_id": resync_job.id if resync_job else None,
            }
        if task.status != "running" or task.execution_job_id != job.id:
            return {"status": "verification_superseded", "task_id": task.id}

        workspace = _workspace_for_user(run.workspace_id, actor, for_update=True)
        if not _workspace_write_allowed(workspace, actor):
            raise RuntimeError("Workspace write permission was revoked during Runner verification.")

        queued_evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        evidence = {
            "state": "passed" if passed else "failed",
            "profile": profile, "revision": revision,
            "verification_job_id": current_job.id,
            "sync_job_id": payload.get("sync_job_id"),
            "patch_audit_id": payload.get("patch_audit_id"),
            "runner_job_id": runner_job_id,
            "iteration": queued_evidence.get("iteration", 1),
            "history": queued_evidence.get("history", []),
            "runner_status": runner_status or "unknown", "exit_code": exit_code,
            "duration_ms": duration_ms, "verified_at": verified_at,
            **output_fingerprints,
            "output_persisted": False,
            "diagnostic_signals": _safe_diagnostic_signals(
                profile, stdout, stderr, exit_code,
            ) if not passed else [],
        }

        # If the workspace moved while Runner was executing the immutable
        # snapshot, the result is stale and must never unlock dependent tasks.
        if workspace.revision != revision:
            resync_job = _queue_agent_runner_resync(workspace, actor, current_job.id)
            evidence.update({
                "state": "stale_workspace",
                "current_revision": workspace.revision,
                "resync_job_id": resync_job.id,
            })
            task.execution_evidence = evidence
            task.execution_error = "Workspace changed during Runner verification. Runner is being resynchronized to the latest revision; create a new approved patch before retrying."
            task.status = "failed"
            task.save(update_fields=["execution_evidence", "execution_error", "status", "updated_at"])
            run.status = "failed"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            AuditLog.objects.create(
                user=actor, action="agent_task.runner_verification_stale",
                target_type="AgentTask", target_id=str(task.id),
                metadata={
                    "run_id": run.id, "verified_revision": revision,
                    "current_revision": workspace.revision,
                    "verification_job_id": current_job.id,
                    "sync_job_id": payload.get("sync_job_id"),
                    "runner_job_id": runner_job_id,
                    "resync_job_id": resync_job.id,
                },
            )
            return {"status": "verification_stale", "task_id": task.id, "evidence": evidence}

        task.execution_evidence = evidence
        task.execution_error = "" if passed else "Runner verification did not pass. Review the result and create a new approved patch."
        # Passing a generic profile is evidence only. Explicit human acceptance
        # is required before the task can become succeeded.
        task.status = "awaiting_approval" if passed else "failed"
        task.save(update_fields=["execution_evidence", "execution_error", "status", "updated_at"])
        remaining = run.tasks.exclude(status="succeeded").exists()
        any_failed = run.tasks.filter(status="failed").exists()
        run.status = "failed" if any_failed else ("running" if remaining else "succeeded")
        run.finished_at = timezone.now() if run.status in {"failed", "succeeded"} else None
        run.save(update_fields=["status", "finished_at", "updated_at"])
        AuditLog.objects.create(
            user=actor,
            action="agent_task.runner_verification_passed" if passed else "agent_task.runner_verification_failed",
            target_type="AgentTask", target_id=str(task.id),
            metadata={
                "run_id": run.id, "revision": revision, "profile": profile,
                "verification_job_id": current_job.id, "sync_job_id": payload.get("sync_job_id"),
                "runner_job_id": runner_job_id,
                "runner_status": runner_status, "exit_code": exit_code,
                "iteration": evidence["iteration"],
            },
        )
    if not passed:
        return {"status": "verification_failed", "task_id": task.id, "evidence": evidence}
    return {"status": "verification_passed_pending_acceptance", "task_id": task.id, "evidence": evidence}


def fail_agent_task_verification(job):
    """Finalize an ambiguous Runner request and reconcile the authoritative snapshot."""
    from django.contrib.auth import get_user_model
    from .views import _workspace_for_user, _workspace_write_allowed

    payload = job.payload if isinstance(job.payload, dict) else {}
    with transaction.atomic():
        run = AgentRun.objects.select_for_update().filter(
            pk=payload.get("run_id"), owner_id=payload.get("owner_id"),
        ).first()
        if not run:
            return
        task = AgentTask.objects.select_for_update().filter(
            pk=payload.get("task_id"), run=run, execution_job_id=job.id,
        ).first()
        if not task:
            return
        if run.status == "canceled" or task.status == "canceled":
            actor = get_user_model().objects.filter(pk=payload.get("owner_id")).first()
            resync_job = None
            if actor:
                try:
                    workspace = _workspace_for_user(run.workspace_id, actor, for_update=True)
                except Http404:
                    workspace = None
                if workspace and _workspace_write_allowed(workspace, actor):
                    resync_job = _queue_agent_runner_resync(workspace, actor, job.id)
            AuditLog.objects.create(
                user_id=payload.get("owner_id"),
                action="agent_task.runner_resync_after_cancel_failure",
                target_type="AgentTask",
                target_id=str(task.id),
                metadata={
                    "run_id": run.id,
                    "verification_job_id": job.id,
                    "resync_job_id": resync_job.id if resync_job else None,
                    "reason": "runner_request_outcome_ambiguous",
                },
            )
            return
        if task.status not in {"running", "awaiting_approval"}:
            return
        finalization_interrupted = task.status == "awaiting_approval"
        previous_evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        if finalization_interrupted and previous_evidence.get("state") != "passed":
            return

        actor = get_user_model().objects.filter(pk=payload.get("owner_id")).first()
        resync_job = None
        if actor:
            try:
                workspace = _workspace_for_user(run.workspace_id, actor, for_update=True)
            except Http404:
                workspace = None
            if workspace and _workspace_write_allowed(workspace, actor):
                resync_job = _queue_agent_runner_resync(workspace, actor, job.id)

        task.status = "failed"
        task.execution_attempts = job.attempts
        task.execution_error = (
            "Runner completed but the durable job result could not be finalized. "
            "A new approved revision is required."
            if finalization_interrupted
            else "Runner verification could not be completed safely after its single request."
        )
        if finalization_interrupted:
            task.execution_evidence = {
                **previous_evidence,
                "state": "finalization_failed",
                "resync_job_id": resync_job.id if resync_job else None,
                "finalization_error": "background_job_result_not_persisted",
            }
        else:
            task.execution_evidence = {
                "state": "infrastructure_failed", "revision": payload.get("revision"),
                "verification_job_id": job.id, "sync_job_id": payload.get("sync_job_id"),
                "resync_job_id": resync_job.id if resync_job else None,
                "attempts": job.attempts,
                "iteration": previous_evidence.get("iteration", 1),
                "history": previous_evidence.get("history", []),
            }
        task.save(update_fields=[
            "status", "execution_attempts", "execution_error", "execution_evidence", "updated_at",
        ])
        run.status = "failed"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])
        AuditLog.objects.create(
            user_id=payload.get("owner_id"),
            action=(
                "agent_task.runner_job_finalization_failed"
                if finalization_interrupted
                else "agent_task.runner_verification_exhausted"
            ),
            target_type="AgentTask", target_id=str(task.id),
            metadata={
                "run_id": run.id, "verification_job_id": job.id,
                "sync_job_id": payload.get("sync_job_id"),
                "resync_job_id": resync_job.id if resync_job else None,
                "attempts": job.attempts,
                "finalization_interrupted": finalization_interrupted,
            },
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_task_accept_api(request, pk, task_pk):
    """Record explicit human acceptance after Runner evidence and criteria review."""
    from .views import _workspace_for_user, _workspace_write_allowed

    with transaction.atomic():
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related("workspace"),
            pk=pk, owner=request.user,
        )
        task = get_object_or_404(AgentTask.objects.select_for_update(), pk=task_pk, run=run)
        # Cancellation is terminal: a delayed acceptance request must never
        # resurrect a canceled task or run after verification has completed.
        if run.status == "canceled" or task.status == "canceled":
            return Response({"error": "A canceled Agent run or task cannot be accepted."}, status=409)
        if task.depends_on.exclude(status="succeeded").exists():
            return Response({"error": "Complete every dependent task before accepting this task."}, status=409)
        if task.status == "succeeded":
            acceptance = (task.execution_evidence or {}).get("human_acceptance", {})
            if acceptance.get("user_id") == request.user.id:
                return Response(_task_status_payload(task, task.execution_job), status=200)
            return Response({"error": "This task has already been accepted."}, status=409)
        if task.status != "awaiting_approval":
            return Response({"error": "Runner verification must pass before human acceptance."}, status=409)

        criteria = _task_acceptance_criteria(task)
        if criteria is None:
            return Response({"error": "This Agent task has no valid task-specific acceptance criteria; it cannot be accepted automatically."}, status=409)
        accepted = request.data.get("accepted_criteria")
        if (
            not isinstance(accepted, list)
            or len(accepted) != len(criteria)
            or any(not isinstance(item, str) for item in accepted)
            or accepted != criteria
        ):
            return Response({"error": "Explicit confirmation of every task-specific acceptance criterion is required."}, status=400)

        evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        verification_job = task.execution_job
        job_result = verification_job.result if verification_job and isinstance(verification_job.result, dict) else {}
        job_payload = verification_job.payload if verification_job and isinstance(verification_job.payload, dict) else {}
        if (
            not verification_job or verification_job.kind != "ai_agent_task_verify"
            or verification_job.status != "succeeded"
            or task.execution_job_id != verification_job.id
            or job_payload.get("run_id") != run.id
            or job_payload.get("task_id") != task.id
            or job_payload.get("owner_id") != request.user.id
            or job_payload.get("workspace_id") != run.workspace_id
            or job_payload.get("revision") != evidence.get("revision")
            or type(evidence.get("revision")) is not int
            or evidence.get("revision") < 1
            or task.execution_revision != evidence.get("revision")
            or job_payload.get("profile") not in VERIFICATION_PROFILES
            or job_payload.get("profile") != task.execution_profile
            or job_payload.get("profile") != evidence.get("profile")
            or job_payload.get("sync_job_id") != evidence.get("sync_job_id")
            or job_payload.get("patch_audit_id") != evidence.get("patch_audit_id")
            or evidence.get("verification_job_id") != verification_job.id
            or not isinstance(evidence.get("runner_job_id"), str)
            or not evidence.get("runner_job_id")
            or job_result.get("status") != "verification_passed_pending_acceptance"
            or job_result.get("evidence") != evidence
            or evidence.get("state") != "passed"
            or evidence.get("runner_status") != "completed"
            or type(evidence.get("exit_code")) is not int
            or evidence.get("exit_code") != 0
            or evidence.get("output_persisted") is not False
        ):
            return Response({"error": "Runner verification has not been durably completed successfully."}, status=409)

        # Acceptance is a second authorization boundary, not just a status
        # transition. Revalidate the exact sync and signed patch-audit chain
        # that authorized this task's immutable Runner revision.
        sync_job = BackgroundJob.objects.filter(
            pk=job_payload.get("sync_job_id"),
            kind="ai_agent_runner_sync",
            status="succeeded",
            payload__workspace_id=run.workspace_id,
            payload__owner_id=request.user.id,
            payload__requested_revision=evidence.get("revision"),
        ).first()
        sync_result = sync_job.result if sync_job and isinstance(sync_job.result, dict) else {}
        if (
            not sync_job
            or sync_result.get("status") != "synced"
            or type(sync_result.get("revision")) is not int
            or sync_result.get("revision") != evidence.get("revision")
            or not AuditLog.objects.filter(
                user=request.user,
                action="ai_agent.runner_workspace_synced",
                target_type="CodeWorkspace",
                target_id=str(run.workspace_id),
                metadata__job_id=sync_job.id,
                metadata__revision=evidence.get("revision"),
            ).exists()
        ):
            return Response({"error": "The exact Runner sync evidence is no longer valid."}, status=409)

        patch_audit = AuditLog.objects.filter(
            pk=job_payload.get("patch_audit_id"),
            user=request.user,
            action="ai_agent.patch_applied",
            target_type="CodeWorkspace",
            target_id=str(run.workspace_id),
            metadata__previous_revision=evidence.get("revision") - 1,
            metadata__revision=evidence.get("revision"),
            metadata__approval="signed_single_use_revision_bound",
            metadata__agent_run_id=run.id,
            metadata__agent_task_id=task.id,
            created_at__gte=run.approved_at,
        ).exists()
        if not patch_audit:
            return Response({"error": "The exact task-bound human patch approval is no longer valid."}, status=409)

        workspace = _workspace_for_user(run.workspace_id, request.user, for_update=True)
        if not workspace or not _workspace_write_allowed(workspace, request.user):
            return Response({"error": "Workspace access is no longer available."}, status=403)
        if workspace.revision != evidence.get("revision"):
            return Response({"error": "Workspace changed after verification; create and verify a new approved patch."}, status=409)

        evidence["human_acceptance"] = {
            "user_id": request.user.id,
            "accepted_at": timezone.now().isoformat(),
            "accepted_criteria": accepted,
            "criteria_scope": "agent_task",
        }
        task.execution_evidence = evidence
        task.status = "succeeded"
        task.execution_error = ""
        task.save(update_fields=["execution_evidence", "status", "execution_error", "updated_at"])

        for dependent in task.dependents.select_for_update().filter(status="blocked"):
            if all(dependency.status == "succeeded" for dependency in dependent.depends_on.all()):
                dependent.status = "ready"
                dependent.save(update_fields=["status", "updated_at"])

        any_failed = run.tasks.filter(status="failed").exists()
        remaining = run.tasks.exclude(status="succeeded").exists()
        run.status = "failed" if any_failed else ("running" if remaining else "succeeded")
        run.finished_at = timezone.now() if run.status in {"failed", "succeeded"} else None
        run.save(update_fields=["status", "finished_at", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action="agent_task.human_acceptance_recorded",
            target_type="AgentTask",
            target_id=str(task.id),
            metadata={
                "run_id": run.id,
                "revision": evidence["revision"],
                "accepted_criteria": accepted,
                "iteration": evidence.get("iteration", 1),
            },
        )
    return Response(_task_status_payload(task, task.execution_job), status=200)

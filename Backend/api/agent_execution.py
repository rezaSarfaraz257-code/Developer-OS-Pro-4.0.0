"""Approval-linked, durable Runner verification for engineering AgentTasks.

Reuses the existing AI patch-apply approval and Runner synchronization outbox.
It creates no second patch format, approval token, or Runner transport.
"""
from __future__ import annotations

import hashlib

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


def _task_status_payload(task, job=None):
    return {
        "run_id": task.run_id,
        "task_id": task.id,
        "status": task.status,
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
            created_at__gte=run.approved_at,
        ).exists()
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

        revision = workspace.revision
        workspace_snapshot = _workspace_payload(workspace)
        task.execution_attempts = current_job.attempts
        task.save(update_fields=["execution_attempts", "updated_at"])

    # This POST is intentionally outside transaction.atomic(). Runner has its
    # own per-workspace lock and per-execution isolation. Never replay an
    # ambiguous POST: this BackgroundJob is configured for one attempt.
    result, error = _runner_request(
        "POST", "/exec", {**workspace_snapshot, "command": command}, timeout=125
    )
    if error or not isinstance(result, dict):
        raise RuntimeError("Isolated Runner verification request failed.")

    runner_job_id = str(result.get("job_id") or "").strip()[:80]
    runner_status = str(result.get("status") or "").lower()
    exit_code = result.get("exit_code")
    duration_ms = result.get("duration_ms")
    if not runner_job_id:
        raise RuntimeError("Runner did not return an execution job reference.")
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise RuntimeError("Runner returned malformed execution output.")
    # Persist integrity fingerprints, not raw process output: test output can
    # contain user data or secrets. These stable digests support later
    # comparisons without copying that content into long-lived DB evidence.
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    output_fingerprints = {
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "stdout_bytes": len(stdout_bytes),
        "stderr_bytes": len(stderr_bytes),
    }
    if type(exit_code) is not int:
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
            "runner_job_id": runner_job_id,
            "iteration": queued_evidence.get("iteration", 1),
            "history": queued_evidence.get("history", []),
            "runner_status": runner_status or "unknown", "exit_code": exit_code,
            "duration_ms": duration_ms, "verified_at": verified_at,
            **output_fingerprints,
            "output_persisted": False,
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
        if not task or task.status not in {"running", "awaiting_approval"}:
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
        evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        verification_job = task.execution_job
        job_result = verification_job.result if verification_job and isinstance(verification_job.result, dict) else {}
        job_payload = verification_job.payload if verification_job and isinstance(verification_job.payload, dict) else {}
        if (
            not verification_job or verification_job.kind != "ai_agent_task_verify"
            or verification_job.status != "succeeded"
            or job_payload.get("run_id") != run.id
            or job_payload.get("task_id") != task.id
            or job_payload.get("revision") != evidence.get("revision")
            or job_result.get("status") != "verification_passed_pending_acceptance"
            or evidence.get("state") != "passed"
            or not evidence.get("revision")
        ):
            return Response({"error": "Runner verification has not been durably completed successfully."}, status=409)
        criteria = run.plan.get("acceptance_criteria", []) if isinstance(run.plan, dict) else []
        if not isinstance(criteria, list) or not criteria:
            return Response({"error": "The Agent plan has no acceptance criteria; this task cannot be accepted automatically."}, status=409)
        accepted = request.data.get("accepted_criteria")
        if (
            not isinstance(accepted, list)
            or len(accepted) != len(criteria)
            or any(not isinstance(item, str) for item in accepted)
            or accepted != criteria
        ):
            return Response({"error": "Explicit confirmation of every plan acceptance criterion is required."}, status=400)
        workspace = _workspace_for_user(run.workspace_id, request.user, for_update=True)
        if not workspace or not _workspace_write_allowed(workspace, request.user):
            return Response({"error": "Workspace access is no longer available."}, status=403)
        if workspace.revision != evidence.get("revision"):
            return Response({"error": "Workspace changed after verification; create and verify a new approved patch."}, status=409)

        evidence["human_acceptance"] = {
            "user_id": request.user.id,
            "accepted_at": timezone.now().isoformat(),
            "accepted_criteria": accepted,
            "criteria_scope": "agent_run",
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

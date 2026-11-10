"""Approval-linked, durable Runner verification for engineering AgentTasks.

Reuses the existing AI patch-apply approval and Runner synchronization outbox.
It creates no second patch format, approval token, or Runner transport.
"""
from __future__ import annotations

from django.db import transaction
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
        if sync_job.status != "succeeded" or int(sync_result.get("revision") or 0) != revision:
            return Response({"error": "The exact workspace revision must finish Runner synchronization first."}, status=409)

        # The existing AI apply endpoint creates this audit record only after
        # its signed, single-use, revision-bound approval has been accepted.
        approved_patch = AuditLog.objects.filter(
            user=request.user, action="ai_agent.patch_applied",
            target_type="CodeWorkspace", target_id=str(workspace.id),
            metadata__revision=revision, created_at__gte=run.approved_at,
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
            metadata={"run_id": run.id, "revision": revision, "profile": profile, "job_id": job.id},
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
    """Execute one fixed verification command through the existing isolated Runner."""
    from django.contrib.auth import get_user_model
    from .models import CodeWorkspace
    from .views import _runner_request, _workspace_payload, _workspace_write_allowed

    payload = job.payload if isinstance(job.payload, dict) else {}
    profile = payload.get("profile")
    command = VERIFICATION_PROFILES.get(profile)
    if not command:
        raise RuntimeError("Unsupported Agent verification profile.")

    # Hold workspace/task locks through Runner POST so a concurrent save
    # cannot race a different revision into the Runner snapshot.
    with transaction.atomic():
        run = AgentRun.objects.select_for_update().filter(
            pk=payload.get("run_id"), owner_id=payload.get("owner_id"),
        ).first()
        task = AgentTask.objects.select_for_update().filter(
            pk=payload.get("task_id"), run=run,
        ).first() if run else None
        workspace = CodeWorkspace.objects.select_for_update().filter(
            pk=payload.get("workspace_id"), owner_id=payload.get("owner_id"),
        ).first()
        if not run or not task or not workspace:
            raise RuntimeError("Agent task verification target is unavailable.")
        if task.status != "running" or task.execution_job_id != job.id:
            raise RuntimeError("Agent task verification lease is no longer current.")
        if workspace.revision != payload.get("revision"):
            raise RuntimeError("Workspace changed before Runner verification.")
        actor = get_user_model().objects.filter(pk=payload.get("owner_id")).first()
        if not actor or not _workspace_write_allowed(workspace, actor):
            raise RuntimeError("Workspace write permission was revoked before Runner verification.")
        sync_job = BackgroundJob.objects.filter(
            pk=payload.get("sync_job_id"), kind="ai_agent_runner_sync", status="succeeded",
            payload__workspace_id=workspace.id, payload__owner_id=actor.id,
        ).first()
        if not sync_job or int((sync_job.result or {}).get("revision") or 0) != workspace.revision:
            raise RuntimeError("The verified workspace revision is not synchronized to Runner.")

        task.execution_attempts = job.attempts
        task.save(update_fields=["execution_attempts", "updated_at"])
        revision = workspace.revision
        result, error = _runner_request(
            "POST", "/exec", {**_workspace_payload(workspace), "command": command}, timeout=125
        )
        if error or not isinstance(result, dict):
            # This POST may already have reached Runner; max_attempts=1 avoids
            # replaying an ambiguous execution and duplicating side effects.
            raise RuntimeError("Isolated Runner verification request failed.")

        runner_status = str(result.get("status") or "").lower()
        exit_code = result.get("exit_code")
        passed = runner_status in {"completed", "success", "passed"} and exit_code == 0
        queued_evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        evidence = {
            "state": "passed" if passed else "failed",
            "profile": profile, "revision": revision,
            "iteration": queued_evidence.get("iteration", 1),
            "history": queued_evidence.get("history", []),
            "runner_status": runner_status or "unknown", "exit_code": exit_code,
            "duration_ms": result.get("duration_ms") if isinstance(result.get("duration_ms"), int) else None,
            "verified_at": timezone.now().isoformat(), "output_persisted": False,
        }
        task.execution_evidence = evidence
        task.execution_error = "" if passed else "Runner verification did not pass. Review the result and create a new approved patch."
        # A passing generic profile is evidence only; the task remains pending
        # until a human explicitly confirms every run-level acceptance criterion.
        task.status = "awaiting_approval" if passed else "failed"
        task.save(update_fields=["execution_evidence", "execution_error", "status", "updated_at"])
        remaining = run.tasks.exclude(status="succeeded").exists()
        any_failed = run.tasks.filter(status="failed").exists()
        run.status = "failed" if any_failed else ("running" if remaining else "succeeded")
        run.finished_at = timezone.now() if run.status in {"failed", "succeeded"} else None
        run.save(update_fields=["status", "finished_at", "updated_at"])
        AuditLog.objects.create(
            user_id=payload["owner_id"],
            action="agent_task.runner_verification_passed" if passed else "agent_task.runner_verification_failed",
            target_type="AgentTask", target_id=str(task.id),
            metadata={"run_id": run.id, "revision": revision, "profile": profile,
                      "runner_status": evidence["runner_status"], "exit_code": exit_code},
        )
    if not passed:
        return {"status": "verification_failed", "task_id": task.id, "evidence": evidence}
    return {"status": "verification_passed_pending_acceptance", "task_id": task.id, "evidence": evidence}


def fail_agent_task_verification(job):
    """Finalize a task if its single safe Runner request cannot be completed."""
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
        if not task or task.status != "running":
            return
        task.status = "failed"
        task.execution_attempts = job.attempts
        task.execution_error = "Runner verification could not be completed safely after its single request."
        previous_evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        task.execution_evidence = {
            "state": "infrastructure_failed", "revision": payload.get("revision"),
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
            user_id=payload.get("owner_id"), action="agent_task.runner_verification_exhausted",
            target_type="AgentTask", target_id=str(task.id),
            metadata={"run_id": run.id, "job_id": job.id, "attempts": job.attempts},
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
        if task.status == "succeeded":
            acceptance = (task.execution_evidence or {}).get("human_acceptance", {})
            if acceptance.get("user_id") == request.user.id:
                return Response(_task_status_payload(task, task.execution_job), status=200)
            return Response({"error": "This task has already been accepted."}, status=409)
        if task.status != "awaiting_approval":
            return Response({"error": "Runner verification must pass before human acceptance."}, status=409)
        evidence = task.execution_evidence if isinstance(task.execution_evidence, dict) else {}
        if evidence.get("state") != "passed" or not evidence.get("revision"):
            return Response({"error": "No successful Runner evidence is available for this task."}, status=409)
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

import json
import logging
import os
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from api.models import AgentRun, AuditLog, BackgroundJob, DataExportRequest, ObjectStorageFile, WorkerHeartbeat
from api.storage import put_file, presigned_get
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


STALE_AFTER = timedelta(minutes=10)


def recover_stale_jobs():
    now = timezone.now()
    stale = BackgroundJob.objects.filter(status="running").filter(
        Q(locked_at__lt=now - STALE_AFTER) | Q(locked_at__isnull=True)
    )
    for job in stale.iterator(chunk_size=100):
        with transaction.atomic():
            locked = BackgroundJob.objects.select_for_update().get(pk=job.pk)
            if (
                locked.status != "running"
                or (locked.locked_at and locked.locked_at >= now - STALE_AFTER)
            ):
                continue
            if locked.attempts < locked.max_attempts:
                locked.status = "queued"
                locked.available_at = now + timedelta(seconds=min(300, 2 ** max(1, locked.attempts)))
            else:
                locked.status = "failed"
                locked.dead_lettered_at = now
                locked.finished_at = now
            locked.locked_at = None
            locked.locked_by = ""
            locked.error = (locked.error or "") + "\nRecovered stale worker lease."
            locked.last_error_at = now
            locked.save(update_fields=[
                "status", "available_at", "locked_at", "locked_by",
                "dead_lettered_at", "finished_at", "error", "last_error_at",
            ])
            if locked.kind == "ai_agent_plan":
                run = AgentRun.objects.select_for_update().filter(
                    pk=locked.payload.get("run_id")
                ).first()
                if run and run.status in {"queued", "planning"}:
                    if locked.status == "queued":
                        run.status = "queued"
                        run.started_at = None
                        run.error = ""
                        run.save(update_fields=["status", "started_at", "error", "updated_at"])
                    else:
                        run.status = "failed"
                        run.error = "WorkerLeaseExpired: planning could not be recovered."
                        run.finished_at = now
                        run.save(update_fields=["status", "error", "finished_at", "updated_at"])
                        AuditLog.objects.create(
                            user_id=run.owner_id,
                            action="agent_run.failed",
                            target_type="AgentRun",
                            target_id=str(run.id),
                            metadata={"reason": "worker_lease_exhausted", "attempts": locked.attempts},
                        )
            if locked.status == "failed" and locked.kind == "data_export":
                DataExportRequest.objects.filter(
                    pk=locked.payload.get("export_id"),
                    user_id=locked.payload.get("user_id"),
                ).exclude(status__in=["ready", "expired"]).update(status="failed")
            if locked.status == "failed" and locked.kind == "ai_agent_task_verify":
                from api.agent_execution import fail_agent_task_verification
                fail_agent_task_verification(locked)


def refresh_job_lease(job_id, worker):
    """Extend a running job's lease only while this worker still owns it."""
    return BackgroundJob.objects.filter(
        pk=job_id,
        status="running",
        locked_by=worker,
    ).update(locked_at=timezone.now()) == 1


def _job_lease_loop(job_id, worker, stop_event, interval=30):
    """Prevent legitimate long-running tasks from being mistaken for orphaned jobs."""
    while not stop_event.wait(interval):
        try:
            close_old_connections()
            if not refresh_job_lease(job_id, worker):
                return
        except Exception:
            # A temporary database outage is observable; stale recovery remains
            # the fallback if the lease cannot be extended before its deadline.
            logger.exception("Background job lease refresh failed job_id=%s worker_id=%s", job_id, worker)
        finally:
            close_old_connections()


def _heartbeat_loop(worker, stop_event):
    """Keep a durable liveness lease fresh and recover from transient DB errors."""
    while not stop_event.is_set():
        try:
            # Long-running worker activity and database restarts can leave
            # stale connections behind. Refresh them on every heartbeat cycle.
            close_old_connections()
            WorkerHeartbeat.objects.update_or_create(
                name="background-worker",
                defaults={"worker_id": worker, "last_seen_at": timezone.now()},
            )
        except Exception:
            # A transient DB error must not permanently kill this daemon thread.
            # Readiness still fails closed when the persisted lease becomes stale.
            logger.exception("Background worker heartbeat update failed")
        finally:
            close_old_connections()
        if stop_event.wait(15):
            return


def claim_job(worker):
    with transaction.atomic():
        job = (BackgroundJob.objects.select_for_update(skip_locked=True)
               .filter(status="queued", available_at__lte=timezone.now())
               .order_by("created_at").first())
        if not job:
            return None
        job.status = "running"
        job.locked_at = timezone.now()
        job.locked_by = worker
        job.attempts += 1
        job.save(update_fields=["status", "locked_at", "locked_by", "attempts"])
        return job


def export_user(user, export):
    root = Path(os.getenv("EXPORT_ROOT", "/app/media/exports"))
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "account": {"id": user.id, "username": user.username, "email": user.email, "first_name": user.first_name, "last_name": user.last_name, "date_joined": user.date_joined.isoformat()},
        "projects": list(user.projects.values()),
        "tasks": list(user.assigned_tasks.values()),
        "notes": list(user.notes.values()),
        "snippets": list(user.snippets.values()),
        "organizations": list(user.organization_memberships.values("organization_id", "role")),
        "audit_logs": list(user.audit_logs.values()),
    }
    path = root / f"user-{user.id}-export-{export.id}.json"
    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    return str(path)


def _is_deployed_runtime() -> bool:
    return bool(
        getattr(settings, "IS_PRODUCTION", False)
        or os.getenv("RENDER_SERVICE_ID", "").strip()
        or os.getenv("RENDER_GIT_COMMIT", "").strip()
    )


def _require_real_email_transport() -> None:
    """Require a configured HTTPS provider or authenticated encrypted SMTP in deployed runtimes."""
    if not _is_deployed_runtime():
        return

    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "").strip()
    sender = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()

    if backend == "api.email_backends.resend.EmailBackend":
        api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
        if not api_key or not sender:
            raise RuntimeError(
                "Email delivery is not configured: Resend HTTPS API credentials and sender are required."
            )
        return

    host = str(getattr(settings, "EMAIL_HOST", "") or "").strip()
    username = str(getattr(settings, "EMAIL_HOST_USER", "") or "").strip()
    password = str(getattr(settings, "EMAIL_HOST_PASSWORD", "") or "")
    port = getattr(settings, "EMAIL_PORT", 0)
    use_tls = bool(getattr(settings, "EMAIL_USE_TLS", False))
    use_ssl = bool(getattr(settings, "EMAIL_USE_SSL", False))

    valid_port = isinstance(port, int) and 1 <= port <= 65535
    if (
        backend != "django.core.mail.backends.smtp.EmailBackend"
        or not host
        or not username
        or not password
        or not sender
        or not valid_port
        or use_tls == use_ssl
    ):
        raise RuntimeError(
            "Email delivery is not configured: authenticated encrypted SMTP or Resend HTTPS API is required."
        )


def execute(job):
    if job.kind == "ai_agent_runner_sync":
        # Serialize the snapshot read and Runner write against workspace edits.
        # If several revisions queued, an older job coalesces to the newest
        # authoritative database snapshot instead of overwriting Runner backwards.
        from api.views import (
            _runner_request, _workspace_for_user, _workspace_payload,
            _workspace_write_allowed,
        )

        payload = job.payload if isinstance(job.payload, dict) else {}
        actor = User.objects.filter(pk=payload.get("owner_id")).first()
        if actor is None:
            raise RuntimeError("Runner sync requester is no longer available.")
        with transaction.atomic():
            workspace = _workspace_for_user(
                payload.get("workspace_id"), actor, for_update=True,
            )
            if not _workspace_write_allowed(workspace, actor):
                raise RuntimeError("Workspace write access was revoked before Runner synchronization.")
            synced_revision = int(workspace.revision)
            runner_data, runner_error = _runner_request(
                "POST", "/sync", _workspace_payload(workspace), timeout=30
            )
            if runner_error:
                # Keep provider/Runner diagnostics out of durable job errors.
                raise RuntimeError("Runner workspace synchronization failed.")
            AuditLog.objects.create(
                user=actor,
                action="ai_agent.runner_workspace_synced",
                target_type="CodeWorkspace",
                target_id=str(workspace.id),
                metadata={
                    "revision": synced_revision,
                    "requested_revision": payload.get("requested_revision"),
                    "coalesced": synced_revision != payload.get("requested_revision"),
                    "job_id": job.id,
                },
            )
        return {
            "status": "synced",
            "workspace_id": workspace.id,
            "revision": synced_revision,
            "coalesced": synced_revision != payload.get("requested_revision"),
            "runner": runner_data if isinstance(runner_data, dict) else {},
        }
    if job.kind == "ai_agent_plan":
        from api.agent_runtime import build_agent_plan
        return build_agent_plan(job.payload["run_id"])
    if job.kind == "ai_agent_task_verify":
        from api.agent_execution import execute_agent_task_verification
        return execute_agent_task_verification(job)
    if job.kind == "email":
        _require_real_email_transport()
        payload = job.payload
        message = EmailMultiAlternatives(
            payload["subject"],
            payload["text"],
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@developer-os.local"),
            [payload["to"]],
        )
        if payload.get("html"):
            message.attach_alternative(payload["html"], "text/html")
        message.send(fail_silently=False)
        return {"sent": True, "to": payload["to"]}
    if job.kind == "data_export":
        export = DataExportRequest.objects.get(pk=job.payload["export_id"], user_id=job.payload["user_id"])
        user = User.objects.get(pk=job.payload["user_id"])
        export.status = "running"
        export.save(update_fields=["status"])
        path = export_user(user, export)
        export.status = "ready"
        export.file_path = path
        export.completed_at = timezone.now()
        export.save(update_fields=["status", "file_path", "completed_at"])
        return {"export_id": export.id, "path": path}
    raise ValueError(f"Unsupported job kind: {job.kind}")


class Command(BaseCommand):
    help = "Run the durable Developer OS background-job worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--sleep", type=float, default=1.0)

    def handle(self, *args, **options):
        worker = f"{socket.gethostname()}:{os.getpid()}"
        heartbeat_stop = threading.Event()
        if not options["once"]:
            # A separate heartbeat keeps readiness accurate even during a
            # long-running export or HTTPS email request.
            threading.Thread(
                target=_heartbeat_loop,
                args=(worker, heartbeat_stop),
                name="developer-os-worker-heartbeat",
                daemon=True,
            ).start()
        while True:
            try:
                recover_stale_jobs()
                job = claim_job(worker)
            except Exception:
                # Database outages must not terminate the long-lived worker.
                # Readiness will go stale if the heartbeat cannot be persisted;
                # retry when the database recovers rather than faking health.
                logger.exception("Worker queue poll failed; retrying worker_id=%s", worker)
                if options["once"]:
                    raise
                time.sleep(max(1.0, options["sleep"]))
                continue

            if not job:
                if options["once"]:
                    WorkerHeartbeat.objects.filter(name="background-worker", worker_id=worker).delete()
                    return
                time.sleep(max(0.1, options["sleep"]))
                continue

            lease_stop = threading.Event()
            lease_thread = threading.Thread(
                target=_job_lease_loop,
                args=(job.pk, worker, lease_stop),
                name=f"developer-os-job-lease-{job.pk}",
                daemon=True,
            )
            lease_thread.start()
            try:
                result = execute(job)
                job.status = "succeeded"
                job.result = result
                job.finished_at = timezone.now()
                job.error = ""
            except Exception as exc:
                now = timezone.now()
                # Keep diagnostics useful without logging message bodies,
                # recipients, verification URLs, or provider credentials.
                logger.error(
                    "Background job execution failed job_id=%s kind=%s attempt=%s error_type=%s",
                    job.pk,
                    job.kind,
                    job.attempts,
                    type(exc).__name__,
                )
                # SMTP/provider exceptions may contain server diagnostics or
                # addresses. Persist only a safe classification for email jobs.
                job.error = (
                    f"{type(exc).__name__}: email delivery failed"
                    if job.kind == "email"
                    else str(exc)[:10000]
                )
                job.last_error_at = now
                if job.attempts < job.max_attempts:
                    job.status = "queued"
                    job.available_at = now + timedelta(seconds=min(300, 2 ** max(1, job.attempts)))
                else:
                    job.status = "failed"
                    job.dead_lettered_at = now
                    job.finished_at = now
                if job.status == "failed" and job.kind == "ai_agent_task_verify":
                    from api.agent_execution import fail_agent_task_verification
                    fail_agent_task_verification(job)
            finally:
                lease_stop.set()
                lease_thread.join(timeout=2)

            # Release the durable lease and persist related terminal state atomically.
            # A permanently failed export must not remain visibly "running" forever;
            # retryable failures intentionally keep the export in its running state.
            job.locked_at = None
            job.locked_by = ""
            try:
                with transaction.atomic():
                    job.save(update_fields=[
                        "status", "result", "error", "available_at", "finished_at",
                        "last_error_at", "dead_lettered_at", "locked_at", "locked_by",
                    ])
                    if job.kind == "ai_agent_plan" and job.status in {"queued", "failed"}:
                        run = AgentRun.objects.select_for_update().filter(
                            pk=job.payload.get("run_id")
                        ).first()
                        if run and run.status in {"queued", "planning"}:
                            if job.status == "queued":
                                run.status = "queued"
                                run.started_at = None
                                run.error = ""
                                run.save(update_fields=["status", "started_at", "error", "updated_at"])
                            else:
                                run.status = "failed"
                                run.error = f"{type(exc).__name__}: planning failed"[:500]
                                run.finished_at = now
                                run.save(update_fields=["status", "error", "finished_at", "updated_at"])
                                AuditLog.objects.create(
                                    user_id=run.owner_id,
                                    action="agent_run.failed",
                                    target_type="AgentRun",
                                    target_id=str(run.id),
                                    metadata={"reason": "planning_failed", "attempts": job.attempts},
                                )
                    if job.kind == "data_export" and job.status == "failed":
                        DataExportRequest.objects.filter(
                            pk=job.payload.get("export_id"),
                            user_id=job.payload.get("user_id"),
                        ).exclude(status__in=["ready", "expired"]).update(status="failed")
            except Exception:
                # An external side effect may already have succeeded. Keep the
                # long-lived worker alive; stale-lease recovery reconciles the
                # running job later. This queue is at-least-once, so handlers
                # must remain idempotent where possible.
                logger.exception(
                    "Worker job outcome persistence failed; stale recovery will reconcile job_id=%s worker_id=%s",
                    job.pk,
                    worker,
                )
                if options["once"]:
                    raise
                time.sleep(max(1.0, options["sleep"]))
                continue

            if options["once"]:
                WorkerHeartbeat.objects.filter(name="background-worker", worker_id=worker).delete()
                return

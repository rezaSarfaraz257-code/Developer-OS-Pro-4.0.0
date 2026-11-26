import json
import logging
import os
import socket
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from api.agent_leases import JOB_LEASE_STALE_AFTER as STALE_AFTER, is_current_job_lease
from api.models import AgentRun, AuditLog, BackgroundJob, DataExportRequest, ObjectStorageFile, WorkerHeartbeat
from api.storage import put_file, presigned_get
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


def recover_stale_jobs():
    now = timezone.now()
    stale = BackgroundJob.objects.filter(status="running").filter(
        Q(locked_at__lt=now - STALE_AFTER)
        | Q(locked_at__isnull=True)
        | Q(locked_at__gt=now)
    )
    for job in stale.iterator(chunk_size=100):
        with transaction.atomic():
            locked = (
                BackgroundJob.objects.select_for_update()
                .filter(pk=job.pk)
                .first()
            )
            # A job can be deleted after the candidate scan (for example, by
            # administrative cleanup). Treat that as a vanished candidate,
            # not a fatal queue-poll error.
            if locked is None:
                continue
            # JSONField payloads can be corrupted or manually edited into
            # non-object JSON. Recovery must still release the lease instead
            # of rolling back the whole queue poll on an unexpected .get().
            payload = locked.payload if isinstance(locked.payload, dict) else {}
            # Re-evaluate the lease after acquiring the row lock. The initial
            # queryset is only a candidate scan; its timestamp may be stale by
            # the time this worker reaches and locks the row.
            recovery_now = timezone.now()
            if (
                locked.status != "running"
                or (
                    locked.locked_at
                    and recovery_now - STALE_AFTER <= locked.locked_at <= recovery_now
                )
            ):
                continue
            if locked.kind == "email":
                # Resend deduplicates requests for 24 hours. Use a conservative
                # 23-hour enqueue-age bound so a recovered request keeps that
                # provider guarantee even when its first send time is unknown.
                resend_idempotency_window_open = bool(
                    settings.EMAIL_BACKEND == "api.email_backends.resend.EmailBackend"
                    and locked.created_at
                    and locked.created_at >= recovery_now - timedelta(hours=23)
                )
                if locked.attempts < locked.max_attempts and resend_idempotency_window_open:
                    locked.status = "queued"
                    locked.available_at = recovery_now + timedelta(
                        seconds=min(300, 2 ** max(1, locked.attempts))
                    )
                else:
                    # SMTP and expired Resend keys cannot prove that a send was
                    # not accepted before the worker stopped. Never replay an
                    # ambiguous delivery without provider-side deduplication.
                    locked.status = "failed"
                    locked.dead_lettered_at = recovery_now
                    locked.finished_at = recovery_now
                    locked.error = (
                        "Email delivery outcome is unknown after worker interruption; "
                        "automatic retry suppressed to prevent duplicate delivery. "
                        "Reconcile provider delivery logs before manual retry."
                    )
            elif locked.attempts < locked.max_attempts:
                locked.status = "queued"
                locked.available_at = recovery_now + timedelta(seconds=min(300, 2 ** max(1, locked.attempts)))
            else:
                locked.status = "failed"
                locked.dead_lettered_at = recovery_now
                locked.finished_at = recovery_now
            locked.locked_at = None
            locked.locked_by = ""
            locked.error = (locked.error or "") + "\nRecovered stale worker lease."
            locked.last_error_at = recovery_now
            locked.save(update_fields=[
                "status", "available_at", "locked_at", "locked_by",
                "dead_lettered_at", "finished_at", "error", "last_error_at",
            ])
            if locked.kind == "ai_agent_plan":
                run_id = payload.get("run_id")
                run = (
                    AgentRun.objects.select_for_update().filter(pk=run_id).first()
                    if type(run_id) is int and run_id > 0
                    else None
                )
                if run and run.status in {"queued", "planning"}:
                    if locked.status == "queued":
                        run.status = "queued"
                        run.started_at = None
                        run.error = ""
                        run.save(update_fields=["status", "started_at", "error", "updated_at"])
                    else:
                        run.status = "failed"
                        run.error = "WorkerLeaseExpired: planning could not be recovered."
                        run.finished_at = recovery_now
                        run.save(update_fields=["status", "error", "finished_at", "updated_at"])
                        AuditLog.objects.create(
                            user_id=run.owner_id,
                            action="agent_run.failed",
                            target_type="AgentRun",
                            target_id=str(run.id),
                            metadata={"reason": "worker_lease_exhausted", "attempts": locked.attempts},
                        )
            if locked.status == "failed" and locked.kind == "data_export":
                export_id = payload.get("export_id")
                user_id = payload.get("user_id")
                if (
                    type(export_id) is int and export_id > 0
                    and type(user_id) is int and user_id > 0
                ):
                    DataExportRequest.objects.filter(
                        pk=export_id,
                        user_id=user_id,
                    ).exclude(status__in=["ready", "expired"]).update(status="failed")
            if locked.status == "failed" and locked.kind == "ai_agent_task_verify":
                # Verification POSTs are deliberately single-attempt: after a
                # worker lease expires, the Runner may already have executed.
                # Never replay that POST; reconcile the authoritative workspace
                # and terminalize the task through the same failure path.
                from api.agent_execution import fail_agent_task_verification
                fail_agent_task_verification(locked)


def refresh_job_lease(job_id, worker):
    """Extend only a still-live lease; an expired worker cannot resurrect itself."""
    now = timezone.now()
    return BackgroundJob.objects.filter(
        pk=job_id,
        status="running",
        locked_by=worker,
        locked_at__gte=now - STALE_AFTER,
        locked_at__lte=now,
    ).update(locked_at=now) == 1


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
               .order_by("created_at", "pk").first())
        if not job:
            return None
        job.status = "running"
        job.locked_at = timezone.now()
        # A worker identity can be reused after a process restart/PID reuse.
        # Give every claim a fresh fencing token so an old execution can never
        # regain ownership merely because the same worker name appears again.
        job.locked_by = f"{worker[:80]}:{uuid.uuid4().hex}"
        job.attempts += 1
        job.save(update_fields=["status", "locked_at", "locked_by", "attempts"])
        return job


def export_user(user, export, *, output_path=None):
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
    path = Path(output_path) if output_path is not None else root / f"user-{user.id}-export-{export.id}.json"
    if path.parent.resolve() != root.resolve():
        raise ValueError("Export staging path must remain inside the configured export directory.")
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
            sync_payload = _workspace_payload(workspace)
            expected_files = sync_payload["files"]
            # Workspace locking serializes snapshots, but it does not prove this
            # worker still owns the durable queue lease. Renew/fence immediately
            # before the external side effect so an expired worker cannot write
            # to Runner after another worker has reclaimed the job.
            if not refresh_job_lease(job.pk, getattr(job, "locked_by", None)):
                raise RuntimeError("Runner sync worker lease is no longer current.")
            runner_data, runner_error = _runner_request(
                "POST", "/sync", sync_payload, timeout=30
            )
            if runner_error:
                # Keep provider/Runner diagnostics out of durable job errors.
                raise RuntimeError("Runner workspace synchronization failed.")
            # HTTP success alone is not proof that the Runner applied the
            # snapshot. Accept only the documented /sync receipt shape.
            if (
                not isinstance(runner_data, dict)
                or runner_data.get("status") != "synced"
                or not isinstance(runner_data.get("files"), dict)
                or runner_data.get("files") != expected_files
            ):
                raise RuntimeError("Runner workspace synchronization returned an invalid receipt.")
            # The HTTPS call may outlive the durable lease. Fence again before
            # recording success so an expired worker cannot publish a stale
            # synchronization receipt after another worker has reclaimed it.
            if not refresh_job_lease(job.pk, getattr(job, "locked_by", None)):
                raise RuntimeError("Runner sync worker lease expired during synchronization.")
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
        return build_agent_plan(
            job.payload["run_id"],
            job_id=job.pk,
            lease_token=job.locked_by,
        )
    if job.kind == "ai_agent_task_verify":
        from api.agent_execution import execute_agent_task_verification
        return execute_agent_task_verification(job)
    if job.kind == "email":
        _require_real_email_transport()
        payload = job.payload
        if not job.pk:
            raise RuntimeError("Email delivery requires a persisted background job.")
        message = EmailMultiAlternatives(
            payload["subject"],
            payload["text"],
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@developer-os.local"),
            [payload["to"]],
        )
        # Keep the stable key as an in-memory transport attribute so it is
        # never exposed as a custom header to SMTP recipients.
        message._developer_os_idempotency_key = f"developer-os-email-job-{job.pk}"
        if payload.get("html"):
            message.attach_alternative(payload["html"], "text/html")
        message.send(fail_silently=False)
        return {"sent": True, "to": payload["to"]}
    if job.kind == "data_export":
        export_id = job.payload.get("export_id")
        user_id = job.payload.get("user_id")
        if not job.pk or type(export_id) is not int or export_id <= 0 or type(user_id) is not int or user_id <= 0:
            raise RuntimeError("Data export job identity is malformed.")
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise RuntimeError("Data export owner is no longer available.")
        root = Path(os.getenv("EXPORT_ROOT", "/app/media/exports"))
        root.mkdir(parents=True, exist_ok=True)
        final_path = root / f"user-{user.id}-export-{export_id}.json"

        # A previous attempt may have committed the file and export row but
        # crashed before finalizing BackgroundJob. Treat that state as an
        # idempotent completion, but only for the worker that still owns the
        # current, unexpired lease.
        with transaction.atomic():
            current_job = BackgroundJob.objects.select_for_update().filter(pk=job.pk).first()
            current_export = DataExportRequest.objects.select_for_update().filter(
                pk=export_id, user_id=user.id,
            ).first()
            if not is_current_job_lease(current_job, getattr(job, "locked_by", None)):
                raise RuntimeError("Data export worker lease is no longer current.")
            if current_export is None or current_export.status not in {"queued", "running", "ready"}:
                raise RuntimeError("Data export request is no longer available.")
            if current_export.status == "ready" and current_export.file_path == str(final_path) and final_path.is_file():
                return {"export_id": current_export.id, "path": str(final_path)}
            if current_export.status in {"queued", "ready"}:
                current_export.status = "running"
                update_fields = ["status"]
                if current_export.file_path and not final_path.is_file():
                    current_export.file_path = ""
                    current_export.completed_at = None
                    update_fields.extend(["file_path", "completed_at"])
                current_export.save(update_fields=update_fields)

        staging_path = root / f".user-{user.id}-export-{export_id}-{uuid.uuid4().hex}.tmp"
        try:
            export_user(user, current_export, output_path=staging_path)
            # Publish atomically only after reacquiring and validating the
            # exact durable lease. A delayed worker can never overwrite the
            # file produced by a replacement worker.
            with transaction.atomic():
                current_job = BackgroundJob.objects.select_for_update().filter(pk=job.pk).first()
                locked_export = DataExportRequest.objects.select_for_update().filter(
                    pk=export_id, user_id=user.id,
                ).first()
                if not is_current_job_lease(current_job, getattr(job, "locked_by", None)):
                    raise RuntimeError("Data export worker lease expired before publication.")
                if locked_export is None or locked_export.status not in {"queued", "running", "ready"}:
                    raise RuntimeError("Data export request is no longer available.")
                os.replace(staging_path, final_path)
                locked_export.status = "ready"
                locked_export.file_path = str(final_path)
                locked_export.completed_at = timezone.now()
                locked_export.save(update_fields=["status", "file_path", "completed_at"])
            return {"export_id": export_id, "path": str(final_path)}
        finally:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary export file export_id=%s", export_id)
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

            claimed_lease = job.locked_by
            lease_stop = threading.Event()
            lease_thread = threading.Thread(
                target=_job_lease_loop,
                args=(job.pk, claimed_lease, lease_stop),
                name=f"developer-os-job-lease-{job.pk}",
                daemon=True,
            )
            lease_thread.start()
            try:
                result = execute(job)
                job.result = result
                job.finished_at = timezone.now()
                job.error = ""
                # A verification handler can finish its reconciliation without
                # completing the requested execution: cancellation and supersession
                # are terminal non-success outcomes, not successful Job execution.
                if (
                    job.kind == "ai_agent_task_verify"
                    and isinstance(result, dict)
                    and result.get("status") in {"verification_canceled", "verification_superseded"}
                ):
                    job.status = "canceled"
                else:
                    job.status = "succeeded"
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
                # Resend supports provider-side idempotency and receives a
                # stable per-job key above. SMTP has no equivalent guarantee:
                # a timeout can happen after the server accepted the message.
                # Do not automatically replay an ambiguous SMTP delivery.
                resend_idempotency_window_open = bool(
                    settings.EMAIL_BACKEND == "api.email_backends.resend.EmailBackend"
                    and job.created_at
                    and job.created_at >= now - timedelta(hours=23)
                )
                email_retry_is_safe = (
                    job.kind != "email" or resend_idempotency_window_open
                )
                if job.attempts < job.max_attempts and email_retry_is_safe:
                    job.status = "queued"
                    job.available_at = now + timedelta(seconds=min(300, 2 ** max(1, job.attempts)))
                else:
                    job.status = "failed"
                    job.dead_lettered_at = now
                    job.finished_at = now
                    if job.kind == "email" and not email_retry_is_safe:
                        job.error = (
                            f"{type(exc).__name__}: email delivery outcome may be unknown; "
                            "automatic retry suppressed because the configured transport "
                            "does not support provider idempotency."
                        )
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
                    # A lease can expire while execute() is blocked in an external
                    # service. Stale recovery may then fail/requeue the job and a
                    # different worker may claim it. Never let this worker's stale
                    # in-memory outcome overwrite that newer durable state.
                    persisted_job = (
                        BackgroundJob.objects.select_for_update()
                        .filter(pk=job.pk).first()
                    )
                    if not is_current_job_lease(persisted_job, claimed_lease):
                        logger.warning(
                            "Discarding worker outcome after lease expired or ownership changed job_id=%s worker_id=%s",
                            job.pk,
                            worker,
                        )
                        continue

                    persisted_job.status = job.status
                    persisted_job.result = job.result
                    persisted_job.error = job.error
                    persisted_job.available_at = job.available_at
                    persisted_job.finished_at = job.finished_at
                    persisted_job.last_error_at = job.last_error_at
                    persisted_job.dead_lettered_at = job.dead_lettered_at
                    persisted_job.locked_at = None
                    persisted_job.locked_by = ""
                    persisted_job.save(update_fields=[
                        "status", "result", "error", "available_at", "finished_at",
                        "last_error_at", "dead_lettered_at", "locked_at", "locked_by",
                    ])
                    if persisted_job.status == "failed" and persisted_job.kind == "ai_agent_task_verify":
                        # Finalize task state only after this worker has proved
                        # it still owns the durable job lease.
                        from api.agent_execution import fail_agent_task_verification
                        fail_agent_task_verification(persisted_job)
                    persisted_payload = (
                        persisted_job.payload if isinstance(persisted_job.payload, dict) else {}
                    )
                    if persisted_job.kind == "ai_agent_plan" and persisted_job.status in {"queued", "failed"}:
                        run_id = persisted_payload.get("run_id")
                        run = AgentRun.objects.select_for_update().filter(
                            pk=run_id if type(run_id) is int and run_id > 0 else None
                        ).first()
                        if run and run.status in {"queued", "planning"}:
                            if persisted_job.status == "queued":
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
                                    metadata={"reason": "planning_failed", "attempts": persisted_job.attempts},
                                )
                    if persisted_job.kind == "data_export" and persisted_job.status == "failed":
                        export_id = persisted_payload.get("export_id")
                        export_user_id = persisted_payload.get("user_id")
                        if (
                            type(export_id) is int and export_id > 0
                            and type(export_user_id) is int and export_user_id > 0
                        ):
                            DataExportRequest.objects.filter(
                                pk=export_id,
                                user_id=export_user_id,
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

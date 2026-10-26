import json
import logging
import os
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path
from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from api.models import BackgroundJob, DataExportRequest, ObjectStorageFile, WorkerHeartbeat
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


def execute(job):
    if job.kind == "email":
        payload = job.payload
        message = EmailMultiAlternatives(payload["subject"], payload["text"], os.getenv("DEFAULT_FROM_EMAIL", "Developer OS <no-reply@localhost>"), [payload["to"]])
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
            # long-running export or SMTP request.
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
                job.error = str(exc)[:10000]
                job.last_error_at = now
                if job.attempts < job.max_attempts:
                    job.status = "queued"
                    job.available_at = now + timedelta(seconds=min(300, 2 ** max(1, job.attempts)))
                else:
                    job.status = "failed"
                    job.dead_lettered_at = now
                    job.finished_at = now
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

import json
import os
import socket
import time
from datetime import timedelta
from pathlib import Path
from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from api.models import BackgroundJob, DataExportRequest, ObjectStorageFile
from api.storage import put_file, presigned_get
from django.contrib.auth import get_user_model

User = get_user_model()


STALE_AFTER = timedelta(minutes=10)

def recover_stale_jobs():
    now = timezone.now()
    stale = BackgroundJob.objects.filter(status="running", locked_at__lt=now - STALE_AFTER)
    for job in stale.iterator(chunk_size=100):
        with transaction.atomic():
            locked = BackgroundJob.objects.select_for_update().get(pk=job.pk)
            if locked.status != "running" or not locked.locked_at or locked.locked_at >= now - STALE_AFTER:
                continue
            if locked.attempts < locked.max_attempts:
                locked.status = "queued"
                locked.available_at = now + timedelta(seconds=min(300, 2 ** max(1, locked.attempts)))
            else:
                locked.status = "failed"
                locked.dead_lettered_at = now
                locked.finished_at = now
            locked.error = (locked.error or "") + "\nRecovered stale worker lease."
            locked.last_error_at = now
            locked.save(update_fields=["status", "available_at", "dead_lettered_at", "finished_at", "error", "last_error_at"])

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
        while True:
            recover_stale_jobs()
            job = claim_job(worker)
            if not job:
                if options["once"]:
                    return
                time.sleep(options["sleep"])
                continue
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
            job.save(update_fields=["status", "result", "error", "available_at", "finished_at", "last_error_at", "dead_lettered_at"])
            if options["once"]:
                return

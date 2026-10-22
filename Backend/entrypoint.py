#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def migrate_with_postgres_lock() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url or not database_url.startswith(("postgres://", "postgresql://")):
        run(sys.executable, "manage.py", "migrate", "--noinput")
        if os.getenv("SEED_CATALOG_ON_STARTUP", "false").lower() in {"1", "true", "yes", "on"}:
            run(sys.executable, "manage.py", "seed_catalog")
        return

    import psycopg

    lock_key = int(os.getenv("MIGRATION_ADVISORY_LOCK", "4173026401"))
    deadline = time.monotonic() + float(os.getenv("MIGRATION_LOCK_TIMEOUT", "180"))

    while True:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
                locked = bool(cursor.fetchone()[0])
                if locked:
                    try:
                        run(sys.executable, "manage.py", "migrate", "--noinput")
                        if os.getenv("SEED_CATALOG_ON_STARTUP", "false").lower() in {"1", "true", "yes", "on"}:
                            run(sys.executable, "manage.py", "seed_catalog")
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
                    return
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for the production migration lock.")
        time.sleep(2)


def _worker_enabled() -> bool:
    """Run a supervised queue consumer in the API container on Render by default.

    Set BACKGROUND_WORKER_MODE=external when a dedicated worker service is
    already deployed, or =disabled to deliberately disable embedded processing.
    Local development remains unchanged unless mode=embedded is explicit.
    """
    mode = os.getenv("BACKGROUND_WORKER_MODE", "").strip().lower()
    if mode in {"external", "disabled", "off", "false", "0"}:
        return False
    if mode == "embedded":
        return True
    return bool(os.getenv("RENDER_SERVICE_ID", "").strip() or os.getenv("RENDER_GIT_COMMIT", "").strip())


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    migrate_with_postgres_lock()

    # One-time, explicit admin recovery for deployments without interactive Shell.
    # Disable the flag and remove the password env var immediately after success.
    if os.getenv("DJANGO_SUPERUSER_BOOTSTRAP", "").strip().lower() in {"1", "true", "yes", "on"}:
        run(sys.executable, "manage.py", "bootstrap_admin_from_env")

    worker: subprocess.Popen[bytes] | None = None
    if _worker_enabled():
        # The worker uses the same migrated database and environment as the API.
        # Its durable heartbeat makes readiness fail closed if it cannot stay up.
        worker = subprocess.Popen(
            [sys.executable, "manage.py", "worker", "--sleep", "1"],
        )

    server = subprocess.Popen(
        ["gunicorn", "x.asgi:application", "-c", "gunicorn.conf.py"],
    )

    stopping = False

    def stop_children(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        for child in (server, worker):
            if child is not None and child.poll() is None:
                child.send_signal(signum)

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    restart_delay = 2
    try:
        while not stopping:
            server_code = server.poll()
            if server_code is not None:
                raise SystemExit(server_code)
            if worker is not None and worker.poll() is not None:
                print(
                    f"Background worker exited; restarting in {restart_delay}s.",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(restart_delay)
                if stopping:
                    break
                worker = subprocess.Popen(
                    [sys.executable, "manage.py", "worker", "--sleep", "1"],
                )
                restart_delay = min(restart_delay * 2, 30)
            elif worker is not None:
                restart_delay = 2
            time.sleep(1)
    finally:
        _stop_process(server)
        _stop_process(worker)


if __name__ == "__main__":
    main()

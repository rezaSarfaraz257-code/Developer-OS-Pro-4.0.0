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


def _stop_processes(processes: list[subprocess.Popen], grace_seconds: float = 10) -> None:
    """Stop child processes gracefully, then force-stop any that remain."""
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + grace_seconds
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
    for process in processes:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def run_api_with_optional_worker() -> int:
    """Supervise API and an opt-in colocated durable worker.

    This mode is deliberately opt-in because a dedicated worker service is
    preferable for independent scaling. When enabled, either child exiting
    causes the other to stop and the container to exit, so a dead worker cannot
    leave the API reporting readiness indefinitely.
    """
    enabled = os.getenv("RUN_BACKGROUND_WORKER_IN_WEB", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        os.execvp("gunicorn", ["gunicorn", "x.asgi:application", "-c", "gunicorn.conf.py"])

    worker = subprocess.Popen(
        [sys.executable, "manage.py", "worker", "--sleep", "1"],
        close_fds=True,
    )
    api = None
    children = [worker]
    try:
        # Fail closed if the worker cannot start or immediately exits.
        time.sleep(1)
        if worker.poll() is not None:
            return worker.returncode or 1
        api = subprocess.Popen(
            ["gunicorn", "x.asgi:application", "-c", "gunicorn.conf.py"],
            close_fds=True,
        )
        children.append(api)
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    return code if code != 0 else 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        _stop_processes(children)


def main() -> None:
    migrate_with_postgres_lock()
    raise SystemExit(run_api_with_optional_worker())


if __name__ == "__main__":
    main()

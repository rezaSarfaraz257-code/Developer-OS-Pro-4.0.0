#!/usr/bin/env python3
from __future__ import annotations

import os
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


def main() -> None:
    migrate_with_postgres_lock()
    os.execvp("gunicorn", ["gunicorn", "x.asgi:application", "-c", "gunicorn.conf.py"])


if __name__ == "__main__":
    main()

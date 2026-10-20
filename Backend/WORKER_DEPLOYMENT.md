# Production background worker for Developer OS

## Default Render deployment (no separate worker service required)

Registration, verification resend, password reset, security alerts, and data exports are stored as durable `BackgroundJob` rows. A successful HTTP response means the request was accepted/queued; it does **not** mean an email has been delivered.

The Backend Docker entrypoint now starts and supervises `python manage.py worker --sleep 1` alongside Gunicorn on Render by default. This lets the existing always-on API web service process its durable queue without requiring a separately billed Render Background Worker. The worker uses the same environment and migrated database as the API. If the worker exits, the entrypoint restarts it with bounded backoff; if it cannot keep a fresh database heartbeat, readiness remains unhealthy rather than reporting a false green status.

No new environment variable is required for the default Render setup. The embedded worker is enabled when `RENDER_SERVICE_ID` or `RENDER_GIT_COMMIT` is present.

## If you already run a dedicated worker

To avoid running the embedded consumer as well, set this environment variable on the API web service:

`BACKGROUND_WORKER_MODE=external`

Keep the dedicated worker running the same release and connected to the same PostgreSQL database. Do not set this mode unless the external worker is actually deployed and healthy.

Supported explicit modes:
- `embedded`: start the worker alongside the API (also useful for local verification).
- `external`: use a separately managed worker; the API container does not start one.
- `disabled`: intentionally disable embedded processing. Production readiness will correctly fail if no other worker is active.

## Email delivery still requires real SMTP configuration

The worker can process email jobs only if the API environment contains working provider settings. Do not switch production to the console email backend: it prints email content to process logs rather than delivering messages. Keep secrets in Render environment settings, never in GitHub or logs.

Verify:
- `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS` / `EMAIL_USE_SSL` configured for the selected provider (not both)
- `DEFAULT_FROM_EMAIL` using a sender address permitted by the provider
- `DATABASE_URL` points to the intended PostgreSQL database
- `DEBUG=false`, secure `DJANGO_SECRET_KEY`, and correct production host/origin settings

A live worker heartbeat proves the worker process can reach the database; it does not prove SMTP credentials work or guarantee inbox placement.

## Verification checklist

1. Deploy the API from the intended `master` commit and inspect Render logs for both Gunicorn startup and the worker process.
2. Check `/api/health/ready/?format=json`; `checks.worker` should be true only when a fresh heartbeat exists and there are no stale running jobs.
3. Submit a test email job and confirm it moves from `queued` to `running` and then `succeeded`.
4. Verify the message arrives in a mailbox you control; if it fails, inspect the server-side job error and SMTP provider logs. Never expose verification tokens or SMTP passwords.
5. Confirm a verification link works once and cannot be reused.

The embedded approach shares CPU and memory with the API process, so monitor resource usage. For larger workloads or higher availability, move to a dedicated worker service and set `BACKGROUND_WORKER_MODE=external`.

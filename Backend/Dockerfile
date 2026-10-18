FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONHASHSEED=random

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

ENV DEBUG=False

RUN python -m compileall -q .
RUN DJANGO_SECRET_KEY=build-only-ephemeral-secret python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/media \
    && chown -R appuser:appuser /app/media

USER appuser

# Container health must measure whether this API process is alive. Readiness
# also requires the separately deployed background worker in production, so
# using readiness here would mark the API container unhealthy when that paid
# service is intentionally absent. Route traffic using /api/health/ready/.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health/live/", timeout=4)' || exit 1

# The entrypoint serializes PostgreSQL migrations with an advisory lock so
# horizontally scaled web replicas cannot race schema changes at startup.
CMD ["python", "entrypoint.py"]

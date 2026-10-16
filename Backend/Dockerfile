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

# Render supplies PORT; Docker Compose keeps the existing 8000 default.
# Migrations are intentionally not part of the image command: production
# deployments should run schema migrations as a controlled release step rather
# than racing multiple web replicas during process startup.
CMD ["gunicorn", "x.asgi:application", "-c", "gunicorn.conf.py"]

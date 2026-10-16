import multiprocessing
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", str(max(2, min(4, multiprocessing.cpu_count() * 2 + 1)))))
threads = 1
worker_connections = 1000
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "200"))
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
worker_tmp_dir = "/dev/shm"

# Keep deployments predictable: Gunicorn must terminate workers cleanly so
# in-flight requests receive the graceful timeout instead of being killed.
def when_ready(server):
    server.log.info("Developer OS ASGI server ready", extra={"workers": workers})

def worker_int(worker):
    worker.log.warning("Developer OS worker interrupted", extra={"worker_pid": worker.pid})

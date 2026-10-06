import os, re, subprocess, time, shutil, signal, resource, hmac, threading, json, shlex
import requests
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from package_engine import capabilities as package_capabilities, plan_response as package_plan_response
from build_engine import plan as build_plan, artifact_manifest
from environment_engine import plan as environment_plan
from debug_engine import capability as debug_capability, handle as debug_handle
from symbol_engine import index as symbol_index, references as symbol_references, rename_preview as symbol_rename_preview, definitions as symbol_definitions, hover as symbol_hover, completion as symbol_completion, rename_diff as symbol_rename_diff, code_actions as symbol_code_actions, diagnostics as symbol_diagnostics

from preview_engine import plan as preview_plan
from ai_engine import plan as ai_plan, validate_patch as validate_ai_patch
from observability_engine import record as observability_record, snapshot as observability_snapshot
from performance_engine import start as profiler_start, finish as profiler_finish, report as profiler_report
from recovery_engine import checkpoint as recovery_checkpoint, recover as recovery_recover, status as recovery_status, mark_verified as recovery_verified
from extension_engine import manifest as extension_manifest
from lsp_engine import MANAGER as LSP_MANAGER, LSPError

app = FastAPI(title="Developer OS Secure Workspace Runner")
ROOT = Path("/workspaces")
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("IDE_RUNNER_TOKEN", "")
MAX_FILE = 1_000_000
MAX_FILES = 2_000
MAX_WORKSPACE_BYTES = 50_000_000
MAX_OUTPUT = 50_000
TIMEOUT = min(120, max(5, int(os.environ.get("RUNNER_TIMEOUT_SECONDS", "120"))))
MAX_CONCURRENT = max(1, int(os.environ.get("RUNNER_MAX_CONCURRENT", "4")))
PROCESS_TTL = max(300, int(os.environ.get("RUNNER_PROCESS_TTL_SECONDS", "3600")))
ALLOW_NETWORK = os.environ.get("RUNNER_ALLOW_NETWORK", "false").lower() in {"1", "true", "yes", "on"}
# Managed runtimes such as Render can deny the Linux namespace/capabilities that
# bubblewrap requires. Container-native mode is the production default.
# bwrap remains opt-in for infrastructure that explicitly supports it.
SANDBOX_MODE = os.environ.get("RUNNER_SANDBOX_MODE", "container").strip().lower()
if SANDBOX_MODE not in {"container", "bwrap"}:
    SANDBOX_MODE = "container"
EXEC_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT)
WORKSPACE_LOCKS = {}
WORKSPACE_LOCKS_GUARD = threading.Lock()
MAX_COMMAND = 2_000

# Lightweight per-workspace fairness guard. Kept separate from the global
# semaphore so a single workspace cannot monopolize the runner.
SCHEDULER_LOCK = threading.RLock()
SCHEDULER_ACTIVE = {}
SCHEDULER_MAX_PER_WORKSPACE = max(1, int(os.environ.get("RUNNER_MAX_WORKSPACE_CONCURRENT", "1")))
CANCEL_GRACE_SECONDS = max(1, int(os.environ.get("RUNNER_CANCEL_GRACE_SECONDS", "3")))

class FairScheduler:
    """Deterministic round-robin scheduler with per-workspace concurrency."""
    def __init__(self, max_per_workspace=1):
        self.max_per_workspace = max(1, int(max_per_workspace))
        self._lock = threading.RLock()
        self._active = {}
        self._queues = {}
        self._sequence = 0
        self._turn = []

    def acquire(self, workspace_id):
        key = str(workspace_id)
        with self._lock:
            if key not in self._turn:
                self._turn.append(key)
            active = self._active.get(key, 0)
            if active >= self.max_per_workspace:
                return False
            if any(self._active.get(other, 0) == 0 for other in self._turn if other != key):
                idle = [other for other in self._turn if self._active.get(other, 0) == 0]
                if idle and key != idle[0]:
                    return False
            self._active[key] = active + 1
            self._turn = [x for x in self._turn if x != key] + [key]
            return True

    def release(self, workspace_id):
        key = str(workspace_id)
        with self._lock:
            active = max(0, self._active.get(key, 0) - 1)
            if active:
                self._active[key] = active
            else:
                self._active.pop(key, None)

    def active(self, workspace_id):
        with self._lock:
            return self._active.get(str(workspace_id), 0)

    def snapshot(self):
        with self._lock:
            return {"max_per_workspace": self.max_per_workspace, "active": dict(self._active)}

FAIR_SCHEDULER = FairScheduler(SCHEDULER_MAX_PER_WORKSPACE)
SCHEDULER_METRICS = {"queued": 0, "active": 0, "rejected": 0, "completed": 0, "total_wait_ms": 0}
SCHEDULER_WAITING = {}  # job_id -> {workspace_id, queued_at}

def _scheduler_queue_position(job_id):
    key = str(job_id)
    with SCHEDULER_LOCK:
        if key not in SCHEDULER_WAITING:
            return 0
        return list(SCHEDULER_WAITING).index(key) + 1


def _scheduler_metric(key, value=1):
    with SCHEDULER_LOCK:
        SCHEDULER_METRICS[key] = SCHEDULER_METRICS.get(key, 0) + value



JOB_LOCK = threading.RLock()
JOBS = {}
JOB_RETENTION_SECONDS = max(60, int(os.environ.get("RUNNER_JOB_RETENTION_SECONDS", "3600")))
JOB_OUTPUT_LOCK = threading.RLock()
JOB_OUTPUT = {}
JOB_OUTPUT_MAX_BYTES = max(4096, int(os.environ.get("RUNNER_JOB_OUTPUT_MAX_BYTES", "262144")))
STREAM_WAIT_SECONDS = max(0.1, min(5.0, float(os.environ.get("RUNNER_STREAM_WAIT_SECONDS", "0.5")))



def _capture_process_output(job_id, proc):
    def reader(stream):
        try:
            for line in iter(stream.readline, ""):
                if line:
                    _append_job_output(job_id, line)
        finally:
            try:
                stream.close()
            except Exception:
                pass
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            threading.Thread(target=reader, args=(stream,), daemon=True).start()

def _append_job_output(job_id, chunk):
    if not chunk:
        return
    data = str(chunk)
    with JOB_OUTPUT_LOCK:
        current = JOB_OUTPUT.get(job_id, "")
        JOB_OUTPUT[job_id] = (current + data)[-JOB_OUTPUT_MAX_BYTES:]

def _job_output_slice(job_id, cursor=0):
    with JOB_OUTPUT_LOCK:
        output = JOB_OUTPUT.get(job_id, "")
    cursor = max(0, int(cursor or 0))
    if cursor >= len(output):
        return "", len(output)
    return output[cursor:], len(output)

def _job_output_snapshot(job_id):
    with JOB_OUTPUT_LOCK:
        return JOB_OUTPUT.get(job_id, "")


JOB_MAX_RETRIES = max(0, min(3, int(os.environ.get("RUNNER_JOB_MAX_RETRIES", "2"))))
JOB_RETRY_BASE_SECONDS = max(0.1, float(os.environ.get("RUNNER_JOB_RETRY_BASE_SECONDS", "0.5")))

def _retry_delay(attempt):
    return min(10.0, JOB_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)))



def _new_job(workspace_id):
    job_id = uuid.uuid4().hex
    now = time.time()
    with JOB_LOCK:
        JOBS[job_id] = {"id": job_id, "workspace_id": str(workspace_id), "status": "queued",
                        "created_at": now, "started_at": None, "finished_at": None,
                        "process_id": None, "exit_code": None, "error": None, "attempt": 0, "max_retries": JOB_MAX_RETRIES, "retryable": False}
    return job_id

def _is_retryable_error(exc):
    return isinstance(exc, (ConnectionError, TimeoutError, OSError)) or "temporarily" in str(exc).lower()

def _set_job(job_id, **updates):
    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)

def _job_snapshot(job_id):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None

def _job_is_cancelled(job_id):
    with JOB_LOCK:
        return bool(JOBS.get(job_id, {}).get("status") == "cancelled")


def _cancel_job(job_id):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        status = job["status"]
        if status == "queued":
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            with SCHEDULER_LOCK:
                SCHEDULER_WAITING.pop(job_id, None)
            return dict(job)
        if status != "running":
            return dict(job)
        process_id = job.get("process_id")
        with JOB_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "cancelling"

    if process_id:
        with PROCESS_LOCK:
            item = PROCESSES.get(str(process_id))
        if item:
            proc = item["popen"]
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=CANCEL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            with PROCESS_LOCK:
                PROCESSES.pop(str(process_id), None)

    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "cancelled"
            JOBS[job_id]["finished_at"] = time.time()
            JOBS[job_id]["process_id"] = None
    return _job_snapshot(job_id)


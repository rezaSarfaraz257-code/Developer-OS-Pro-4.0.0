import os, re, subprocess, time, shutil, signal, resource, hmac, threading, json, shlex
import requests
from fastapi.responses import StreamingResponse
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
from observability_engine import record as observability_record, snapshot as observability_snapshot, trace as observability_trace, finish as observability_finish
from event_bus import publish as event_publish, since as event_since, snapshot as event_snapshot
from performance_engine import start as profiler_start, finish as profiler_finish, report as profiler_report
from recovery_engine import checkpoint as recovery_checkpoint, recover as recovery_recover, status as recovery_status, mark_verified as recovery_verified
from extension_engine import manifest as extension_manifest
from lsp_engine import MANAGER as LSP_MANAGER, LSPError

app = FastAPI(title="Developer OS Secure Workspace Runner")
ROOT = Path("/workspaces")
TOKEN = os.environ.get("IDE_RUNNER_TOKEN", "")
MAX_FILE = 1_000_000
MAX_FILES = 2_000
MAX_WORKSPACE_BYTES = 50_000_000
SNAPSHOT_ROOT = ROOT / ".ide-snapshots"
MAX_SNAPSHOTS_PER_WORKSPACE = max(2, int(os.environ.get("RUNNER_MAX_SNAPSHOTS", "20")))
MAX_OUTPUT = 50_000
TIMEOUT = min(120, max(5, int(os.environ.get("RUNNER_TIMEOUT_SECONDS", "120"))))
MAX_CONCURRENT = max(1, int(os.environ.get("RUNNER_MAX_CONCURRENT", "4")))
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
IDE_STATE_LOCK = threading.RLock()
IDE_STATES = {}
MAX_IDE_STATES = 512

def _ide_state(workspace_id):
    wid = str(workspace_id)
    with IDE_STATE_LOCK:
        state = IDE_STATES.get(wid)
        if state is None:
            if len(IDE_STATES) >= MAX_IDE_STATES:
                IDE_STATES.pop(next(iter(IDE_STATES)), None)
            state = {
                "schema_version": "1",
                "workspace_id": wid,
                "revision": 0,
                "updated_at": time.time(),
                "execution": {"status": "idle", "process_id": None, "trace_id": None, "exit_code": None},
                "diagnostics": {"count": 0},
                "debugger": {"status": "idle", "session_id": None},
                "last_event_sequence": 0,
            }
            IDE_STATES[wid] = state
        return state

def _project_ide_event(event):
    wid = event.get("workspace_id")
    if wid is None:
        return
    state = _ide_state(wid)
    with IDE_STATE_LOCK:
        state["last_event_sequence"] = max(state["last_event_sequence"], int(event.get("sequence", 0)))
        typ = event.get("type", "")
        data = event.get("data") or {}
        if typ == "execution.started":
            state["execution"] = {"status": "running", "process_id": data.get("process_id"), "trace_id": event.get("trace_id"), "exit_code": None}
        elif typ == "execution.finished":
            state["execution"].update({"status": data.get("status", "finished"), "exit_code": data.get("exit_code")})
        elif typ == "execution.timeout":
            state["execution"].update({"status": "timeout", "exit_code": 124})
        elif typ == "workspace.transaction":
            state["active_file"] = data.get("active_file", state.get("active_file", ""))
        elif typ == "debug.started":
            state["debugger"] = {"status": "running", "session_id": data.get("session_id")}
        elif typ in {"debug.stopped", "debug.finished"}:
            state["debugger"]["status"] = "stopped"
        state["revision"] += 1
        state["updated_at"] = time.time()

MAX_COMMAND = 2_000
BLOCKED = [
    r"\b(docker|podman|nsenter|unshare|mount|umount|chroot)\b",
    r"(^|\s)rm\s+-rf\s+/$",
    r":\(\)\s*\{\s*:\|:\s*;\s*\}\s*;",
    r"\b(curl|wget|nc|ncat|socat|telnet|ssh|scp|ftp|iptables|ip6tables|ifconfig|route)\b",
    r"(^|\s)(/proc|/sys|/dev|/etc/shadow|/etc/passwd)(/|\s|$)",
    r"\b(kill|pkill|killall)\b",
]

class Workspace(BaseModel):
    workspace_id: str
    files: dict[str, str] = Field(default_factory=dict)
    active_file: str = ""

class PatchOperation(BaseModel):
    path: str
    content: str | None = None
    action: str = "write"

class PatchRequest(BaseModel):
    workspace_id: str
    expected_revision: int = Field(ge=0)
    patch_id: str = Field(min_length=1, max_length=120)
    operations: list[PatchOperation] = Field(default_factory=list, max_length=200)

class PatchValidationRequest(PatchRequest):
    strict: bool = False

class WorkspaceTransaction(BaseModel):
    workspace_id: str
    expected_revision: int = Field(ge=0)
    operations: list[dict] = Field(default_factory=list, max_length=200)
    active_file: str | None = None

class ExecRequest(Workspace):
    command: str = Field(min_length=1, max_length=2000)

class InstallRequest(ExecRequest):
    framework: str = ""
    package_manager: str = ""
    action: str = "install"
    package: str = ""

class PackagePlanRequest(Workspace):
    action: str = "install"
    package: str = ""
    package_manager: str = ""

class TestPlanRequest(Workspace):
    framework: str = ""


def auth(value):
    expected = f"Bearer {TOKEN}" if TOKEN else ""
    if not TOKEN or not hmac.compare_digest(str(value or ""), expected):
        raise HTTPException(status_code=401, detail="Unauthorized")

def _workspace_lock(workspace_id):
    key = str(workspace_id)
    with WORKSPACE_LOCKS_GUARD:
        return WORKSPACE_LOCKS.setdefault(key, threading.RLock())

def safe_workspace(workspace_id):
    if not re.fullmatch(r"[0-9]+", str(workspace_id)):
        raise HTTPException(status_code=400, detail="Invalid workspace id")
    p = ROOT / str(workspace_id)
    p.mkdir(parents=True, exist_ok=True)
    return p

def safe_rel(path):
    """Return a normalized workspace-relative path or reject it.

    Absolute paths, traversal components and dot-segments are rejected before
    normalization so they cannot be transformed into an apparently safe path.
    """
    raw = str(path).replace("\\", "/")
    if not raw or len(raw) > 500 or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        raise ValueError("unsafe path")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts) or raw.startswith(".git/") or "/.git/" in raw:
        raise ValueError("unsafe path")
    normalized = "/".join(parts)
    if normalized.startswith(".git/"):
        raise ValueError("unsafe path")
    return normalized

def write_snapshot(root, files):
    if len(files) > MAX_FILES:
        raise ValueError(f"workspace exceeds {MAX_FILES} files")
    total = 0
    for rel, content in files.items():
        rel = safe_rel(rel)
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE:
            raise ValueError("file too large")
        total += len(content.encode())
        if total > MAX_WORKSPACE_BYTES:
            raise ValueError("workspace exceeds 50 MB source limit")
        target = root / rel
        root_resolved = root.resolve()
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        # Never follow a pre-existing symlink out of the workspace.
        if any(part.is_symlink() for part in [parent, *parent.parents] if part != root and part.exists()):
            raise ValueError("unsafe path")
        resolved = target.resolve(strict=False)
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise ValueError("unsafe path")
        if target.exists() and target.is_symlink():
            raise ValueError("unsafe path")
        target.write_text(content, encoding="utf-8")

def _snapshot_dir(workspace_id):
    if not re.fullmatch(r"[0-9]+", str(workspace_id)):
        raise HTTPException(status_code=400, detail="Invalid workspace id.")
    path = SNAPSHOT_ROOT / str(workspace_id)
    path.mkdir(parents=True, exist_ok=True)
    return path

def _create_snapshot(workspace_id, root, revision, reason="transaction"):
    folder = _snapshot_dir(workspace_id)
    snapshot_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}"
    target = folder / snapshot_id
    target.mkdir(parents=True, exist_ok=False)
    write_snapshot(target, snapshot(root))
    meta = {"schema_version":"1","snapshot_id":snapshot_id,"workspace_id":str(workspace_id),"revision":int(revision),"reason":str(reason)[:120],"created_at":time.time()}
    (target / ".metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    snapshots = sorted([p for p in folder.iterdir() if p.is_dir()], key=lambda p:p.stat().st_mtime, reverse=True)
    for stale in snapshots[MAX_SNAPSHOTS_PER_WORKSPACE:]:
        shutil.rmtree(stale, ignore_errors=True)
    return meta

def _restore_snapshot(workspace_id, root, snapshot_id):
    folder = _snapshot_dir(workspace_id)
    if not re.fullmatch(r"[0-9]+-[a-f0-9]{10}", str(snapshot_id)):
        raise HTTPException(status_code=400, detail="Invalid snapshot id.")
    target = folder / str(snapshot_id)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Snapshot not found.")
    files = snapshot(target)
    atomic_write_snapshot(root, files)
    return files

def atomic_write_snapshot(root, files):
    staging = root.parent / (root.name + ".staging-" + uuid.uuid4().hex)
    backup = root.parent / (root.name + ".backup-" + uuid.uuid4().hex)
    try:
        staging.mkdir(parents=True, exist_ok=False)
        write_snapshot(staging, files)
        if snapshot(staging) != files:
            raise ValueError("staged workspace verification failed")
        root.rename(backup)
        try:
            staging.rename(root)
        except Exception:
            if not root.exists() and backup.exists():
                backup.rename(root)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)

def snapshot(root):
    out = {}
    total = 0
    skip = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".npm-cache", ".pip-cache", ".home"}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_symlink() or not p.is_file() or any(part in skip for part in rel.parts):
            continue
        try:
            size = p.stat().st_size
            if size > MAX_FILE or total + size > MAX_WORKSPACE_BYTES:
                continue
            data = p.read_bytes()
            if b"\x00" in data:
                continue
            out[str(rel).replace(os.sep, "/")] = data.decode("utf-8")
            total += size
        except Exception:
            continue
    return out

def _limit_process_resources():
    '''Apply per-execution POSIX limits before untrusted code starts.'''
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT + 2))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WORKSPACE_BYTES, MAX_WORKSPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def _sandbox_command(root, command, allow_network=False):
    if allow_network and not ALLOW_NETWORK:
        raise HTTPException(status_code=403, detail="Network access is disabled by runner policy.")
    """Return an execution command for the selected isolation backend.

    Render already provides the process/container boundary. Trying to create
    another Linux namespace with bubblewrap inside that managed container can
    fail with: 'bwrap: Failed to make / slave: Permission denied'.
    """
    if SANDBOX_MODE == "container":
        return ["bash", "-lc", command]

    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise HTTPException(status_code=503, detail="Bubblewrap sandbox is unavailable.")
    args = [
        bwrap, "--die-with-parent", "--new-session",
        "--unshare-pid", "--unshare-uts", "--unshare-ipc",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/usr/local", "/usr/local",
        "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
        "--tmpfs", "/workspaces", "--bind", str(root), "/workspace",
        "--chdir", "/workspace", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--clearenv",
    ]
    if not allow_network and not ALLOW_NETWORK:
        args.append("--unshare-net")
    args.extend([
        "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "HOME", "/workspace/.home",
        "--", "bash", "-lc", command,
    ])
    return args

def _validate_execution_policy(command, *, allow_network=False):
    command = str(command or "").strip()
    if not command or len(command) > MAX_COMMAND or "\x00" in command or any(ord(ch) < 9 for ch in command):
        raise HTTPException(status_code=400, detail="Invalid command.")
    if any(re.search(pattern, command, re.I) for pattern in BLOCKED):
        raise HTTPException(status_code=400, detail="Command blocked by sandbox policy.")
    # Network access is opt-in and must be explicitly enabled by the runner
    # deployment. A client cannot override a disabled network policy.
    if allow_network and not ALLOW_NETWORK:
        raise HTTPException(status_code=403, detail="Network access is disabled by runner policy.")
    return command

def run_command(root, command, *, allow_network=False):
    trace_state = observability_trace("exec", workspace_id=root.name)
    _project_ide_event(event_publish("execution.started", source="runner", workspace_id=root.name, trace_id=trace_state["trace_id"]))
    started = time.monotonic()
    try:
        command = _validate_execution_policy(command, allow_network=allow_network)
        if re.match(r"^python(?:\\s|$)", command) and not shutil.which("python") and shutil.which("python3"):
            command = re.sub(r"^python(?=\\s|$)", "python3", command, count=1)
        env = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": str(root / ".home"),
            "npm_config_cache": str(root / ".npm-cache"),
            "PIP_CACHE_DIR": str(root / ".pip-cache"),
            "PYTHONUNBUFFERED": "1",
            "BASH_ENV": "/dev/null",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "npm_config_update_notifier": "false",
        }
        (root / ".home").mkdir(exist_ok=True)
        os.umask(0o077)
        acquired = EXEC_SEMAPHORE.acquire(timeout=5)
        if not acquired:
            raise HTTPException(status_code=429, detail="Runner concurrency limit reached.")
        try:
            with _workspace_lock(root.name):
                proc = subprocess.run(
                    _sandbox_command(root, command, allow_network=allow_network), cwd=root, env=env,
                    capture_output=True, text=True, timeout=TIMEOUT,
                    start_new_session=True, preexec_fn=_limit_process_resources,
                )
                result = {
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-MAX_OUTPUT:],
                    "stderr": proc.stderr[-MAX_OUTPUT:],
                    "duration_ms": int((time.monotonic()-started)*1000),
                    "files": snapshot(root),
                    "trace_id": trace_state["trace_id"],
                }
                observability_finish(trace_state, "ok" if proc.returncode == 0 else "failed",
                                     workspace_id=root.name, exit_code=proc.returncode)
                return result
        except subprocess.TimeoutExpired as exc:
            try:
                if "proc" in locals() and proc.pid:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            result = {
                "exit_code": 124,
                "stdout": (exc.stdout or "")[-MAX_OUTPUT:] if isinstance(exc.stdout, str) else "",
                "stderr": f"Execution timed out after {TIMEOUT} seconds.",
                "duration_ms": int((time.monotonic()-started)*1000),
                "files": snapshot(root),
                "trace_id": trace_state["trace_id"],
            }
            observability_finish(trace_state, "timeout", workspace_id=root.name, exit_code=124)\n            _project_ide_event(event_publish("execution.timeout", source="runner", workspace_id=root.name, trace_id=trace_state["trace_id"], exit_code=124))
            return result
        finally:
            EXEC_SEMAPHORE.release()
    except Exception as exc:
        observability_finish(trace_state, "error", workspace_id=root.name, error_type=exc.__class__.__name__)
        raise

def _runtime_info():
    """Expose deterministic runtime capabilities to the IDE without exposing host details."""
    candidates = {
        "python": ["python", "python3"],
        "node": ["node"],
        "npm": ["npm"],
        "git": ["git"],
        "bash": ["bash"],
    }
    runtimes = {}
    for name, commands in candidates.items():
        executable = next((shutil.which(c) for c in commands if shutil.which(c)), None)
        version = ""
        if executable:
            try:
                probe = subprocess.run(
                    [executable, "--version"], capture_output=True, text=True, timeout=2,
                    env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
                )
                version = (probe.stdout or probe.stderr).strip().splitlines()[0][:120]
            except Exception:
                version = "available"
        runtimes[name] = {"available": bool(executable), "version": version}
    return runtimes

def _capability_manifest():
    """Stable control-plane contract consumed by the Django IDE."""
    container_native = SANDBOX_MODE == "container"
    return {
        "service": "developer-os-runner",
        "api_version": "1",
        "version": os.environ.get("RELEASE_VERSION", "3.2.0"),
        "sandbox": {
            "backend": SANDBOX_MODE,
            "mode": "container-native" if container_native else "bubblewrap",
            "process_boundary": True,
            "network_enforcement": ("delegated-to-container-runtime" if container_native else ("isolated" if not ALLOW_NETWORK else "provisioning-network")),
        },
        "runtimes": _runtime_info(),
        "limits": {
            "timeout_seconds": TIMEOUT,
            "max_concurrent": MAX_CONCURRENT,
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE,
            "max_workspace_bytes": MAX_WORKSPACE_BYTES,
            "max_command_bytes": MAX_COMMAND,
            "max_processes_per_workspace": MAX_PROCESSES_PER_WORKSPACE,
            "max_process_output_bytes": MAX_PROCESS_OUTPUT,
            "max_process_lifetime_seconds": MAX_PROCESS_LIFETIME,
        },
        "operations": {"sync": True, "snapshot": True, "execute": True, "process": True, "git": True, "preview": True, "install": True, "debug": debug_capability()["available"], "lsp": True},
        "lsp": LSP_MANAGER.capability(),
    }



class LSPRequest(Workspace):
    language: str = "python"
    method: str = ""
    uri: str = ""
    path: str = ""
    name: str = ""
    query: str = ""
    new: str = ""
    action: str = ""
    text: str = ""
    version: int = 1
    line: int = 0
    column: int = 0
    params: dict = Field(default_factory=dict)

@app.post("/lsp/document/open")
def lsp_document_open(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    root=safe_workspace(payload.workspace_id)
    session=LSP_MANAGER.session(payload.workspace_id,root,payload.language)
    session.did_open(payload.uri, payload.text or "", payload.version or 1, payload.language or "")
    return {"status":"opened","uri":payload.uri,"version":session.document_versions[payload.uri]}

@app.post("/lsp/document/change")
def lsp_document_change(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    root=safe_workspace(payload.workspace_id)
    session=LSP_MANAGER.session(payload.workspace_id,root,payload.language)
    session.did_change(payload.uri, payload.text or "", payload.version)
    return {"status":"changed","uri":payload.uri,"version":session.document_versions[payload.uri]}

@app.post("/lsp/document/close")
def lsp_document_close(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    root=safe_workspace(payload.workspace_id)
    session=LSP_MANAGER.session(payload.workspace_id,root,payload.language)
    session.did_close(payload.uri)
    return {"status":"closed","uri":payload.uri}

@app.post("/lsp/code-action/preview")
def lsp_code_action_preview(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.action != "remove-debug-statement":
        raise HTTPException(status_code=400, detail="Unsupported code action.")
    path = safe_rel(payload.path)
    target = root / path
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    lines = target.read_text(encoding="utf-8").splitlines(True)
    if payload.line < 1 or payload.line > len(lines):
        raise HTTPException(status_code=400, detail="Invalid line.")
    line = lines[payload.line - 1]
    if not re.search(r"console\.log|debugger", line):
        raise HTTPException(status_code=409, detail="Code action no longer matches the document.")
    updated = "".join(lines[:payload.line - 1] + lines[payload.line:])
    revision = _workspace_revision(payload.workspace_id)
    patch = {"workspace_id": payload.workspace_id, "expected_revision": revision, "patch_id": "code-action-" + str(int(time.time() * 1000)), "operations": [{"path": path, "action": "write", "content": updated}]}
    return {"schema_version": "1", "action": payload.action, "patch": patch, "preview": {"path": path, "line": payload.line, "removed": line.rstrip()}}

@app.post("/lsp/action")
def lsp_action(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    action = str(getattr(payload, "action", "") or "").strip()
    if action not in {"hover","completion","definition","references","rename","code_actions"}:
        raise HTTPException(status_code=400, detail="Unsupported LSP action.")
    args = {
        "root": root,
        "name": getattr(payload, "name", "") or "",
        "query": getattr(payload, "query", "") or "",
        "path": getattr(payload, "path", "") or "",
        "line": int(getattr(payload, "line", 0) or 0),
        "new": getattr(payload, "new", "") or "",
    }
    handlers = {
        "hover": symbol_hover,
        "completion": symbol_completion,
        "definition": symbol_definitions,
        "references": symbol_references,
        "rename": symbol_rename_diff,
        "code_actions": symbol_code_actions,
    }
    try:
        if action == "hover":
            result = handlers[action](root, args["name"], args["path"], args["line"])
        elif action == "completion":
            result = handlers[action](root, args["query"], args["path"])
        elif action == "definition":
            result = handlers[action](root, args["name"], args["path"])
        elif action == "references":
            result = handlers[action](root, args["name"], args["path"])
        elif action == "rename":
            result = handlers[action](root, args["name"], args["new"], args["path"])
        else:
            result = handlers[action](root, args["path"], args["line"])
        return {"schema_version":"1","action":action,"workspace_id":payload.workspace_id,"result":result}
    except (ValueError, LSPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300])

@app.get("/lsp/capabilities")
def lsp_capabilities(authorization: str = Header(default="")):
    auth(authorization)
    return {"schema_version":"1","protocol":"LSP/stdio","languages":LSP_MANAGER.capability()["available"],"features":{"diagnostics":True,"hover":True,"completion":True,"definition":True,"references":True,"rename":True,"code_actions":True}}

@app.post("/lsp/document/diagnostics")
def lsp_document_diagnostics(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    if not payload.uri:
        raise HTTPException(status_code=400, detail="uri is required")
    root=safe_workspace(payload.workspace_id)
    try:
        session=LSP_MANAGER.session(payload.workspace_id,root,payload.language)
        params={"textDocument":{"uri":payload.uri}}
        result=session.request("textDocument/diagnostic",params,timeout=8)
        notifications=session.drain_notifications()
        return {"schema_version":"1","workspace_id":payload.workspace_id,"uri":payload.uri,"result":result,"notifications":notifications}
    except LSPError as exc:
        raise HTTPException(status_code=503,detail=str(exc)[:300])

@app.post("/lsp/request")
def lsp_request(payload: LSPRequest, authorization: str = Header(default="")):
    auth(authorization)
    root=safe_workspace(payload.workspace_id)
    try:
        session=LSP_MANAGER.session(payload.workspace_id,root,payload.language)
        params=dict(payload.params or {})
        if payload.uri and "textDocument" not in params:
            params["textDocument"]={"uri":payload.uri}
        if payload.method in {"textDocument/didOpen","textDocument/didChange","textDocument/didClose","initialized","exit"}:
            session.notify(payload.method, params)
            return {"jsonrpc":"2.0","method":payload.method,"status":"notified"}
        result = session.request(payload.method,params)
        notifications = session.drain_notifications()
        if notifications:
            result["notifications"] = notifications
        return result
    except LSPError as exc:
        raise HTTPException(status_code=503,detail=str(exc)[:300])
@app.post("/lsp/notifications")
def lsp_notifications(payload: LSPRequest, authorization: str = Header(default="")):
    """Drain queued LSP notifications without issuing an LSP request."""
    auth(authorization)
    root=safe_workspace(payload.workspace_id)
    try:
        session=LSP_MANAGER.session(payload.workspace_id,root,payload.language)
        return {"notifications": session.drain_notifications()}
    except LSPError as exc:
        raise HTTPException(status_code=503,detail=str(exc)[:300])

def _health_payload(status="ok"):
    with PROCESS_LOCK:
        active_processes = sum(1 for item in PROCESSES.values() if item["popen"].poll() is None)
        total_processes = len(PROCESSES)
    active_slots = MAX_CONCURRENT - getattr(EXEC_SEMAPHORE, "_value", MAX_CONCURRENT)
    obs = observability_snapshot()
    pressure = {
        "active_processes": active_processes,
        "tracked_processes": total_processes,
        "execution_slots_in_use": max(0, active_slots),
        "execution_slots_available": max(0, MAX_CONCURRENT - active_slots),
        "process_capacity": MAX_PROCESSES_PER_WORKSPACE,
    }
    return {
        "status": status,
        "service": "developer-os-runner",
        "sandbox": "container-native" if SANDBOX_MODE == "container" else "bubblewrap",
        "sandbox_backend": SANDBOX_MODE,
        "bubblewrap_available": bool(shutil.which("bwrap")),
        "network_policy": ("container-runtime-policy" if SANDBOX_MODE == "container" else ("isolated-by-default" if not ALLOW_NETWORK else "provisioning-network")),
        "concurrency": {"max": MAX_CONCURRENT, "timeout_seconds": TIMEOUT},
        "pressure": pressure,
        "observability": {
            "status": obs.get("status", "unknown"),
            "events": obs.get("event_buffer", {}).get("size", 0),
            "counters": obs.get("counters", {}),
        },
        "runtimes": _runtime_info(),
        "debugger": debug_capability(),
        "capabilities_version": "2",
        "version": os.environ.get("RELEASE_VERSION", "3.2.0"),
    }

@app.get("/health")
def health():
    return _health_payload()

@app.get("/live")
def live():
    # Liveness must stay dependency-light: the process is alive if FastAPI
    # can answer the request. It intentionally does not require a runner token.
    return {"status": "alive", "service": "developer-os-runner"}

@app.get("/ready")
def ready():
    # Readiness is stricter than liveness but exposes no secret configuration.
    with PROCESS_LOCK:
        active_processes = sum(1 for item in PROCESSES.values() if item["popen"].poll() is None)
    obs = observability_snapshot()
    checks = {
        "workspace_root": ROOT.exists() and os.access(ROOT, os.W_OK),
        "runner_token": bool(TOKEN),
        "debugger": bool(debug_capability().get("available")),
        "observability": obs.get("status") == "healthy",
        "execution_capacity": active_processes < MAX_CONCURRENT + MAX_PROCESSES_PER_WORKSPACE,
    }
    status = "ready" if all(checks.values()) else "not_ready"
    return Response(
        content=json.dumps({"status": status, "service": "developer-os-runner", "checks": checks}),
        media_type="application/json",
        status_code=200 if status == "ready" else 503,
    )

@app.get("/metrics")
def metrics(authorization: str = Header(default="")):
    """Authenticated machine-readable control-plane metrics."""
    auth(authorization)
    with PROCESS_LOCK:
        processes = list(PROCESSES.values())
        active_processes = sum(1 for item in processes if item["popen"].poll() is None)
        failed_processes = sum(1 for item in processes if item["popen"].poll() not in (None, 0))
    obs = observability_snapshot()
    counters = obs.get("counters", {})
    return {
        "schema_version": "1",
        "service": "developer-os-runner",
        "execution": {
            "max_concurrent": MAX_CONCURRENT,
            "timeout_seconds": TIMEOUT,
            "active_processes": active_processes,
            "failed_processes": failed_processes,
        },
        "process": {
            "tracked": len(processes),
            "active": active_processes,
            "max_per_workspace": MAX_PROCESSES_PER_WORKSPACE,
            "max_lifetime_seconds": MAX_PROCESS_LIFETIME,
        },
        "events": {
            "buffer_size": obs.get("event_buffer", {}).get("size", 0),
            "capacity": obs.get("event_buffer", {}).get("capacity", 0),
            "counters": counters,
        },
    }

def _post_apply_verify(files, workspace_id):
    result = _validate_staged_files(files, strict=True)
    result["workspace_id"] = str(workspace_id)
    result["verification"] = "post-apply-gate"
    return result

def _snapshot_id(meta):
    return str(meta.get("snapshot_id", ""))

def _diagnostic(severity, path, message, line=1, column=1, source="runner"):
    return {"severity": severity, "path": path, "line": int(line or 1), "column": int(column or 1), "message": str(message)[:500], "source": source}

def _collect_diagnostics(files):
    diagnostics = []
    for path, content in files.items():
        try:
            if path.endswith(".py"):
                import ast
                ast.parse(content, filename=path)
            elif path.endswith((".json", ".jsonc")):
                json.loads(content)
            elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
                stripped = re.sub(r"//.*?$|/\*.*?\*/", "", content, flags=re.M | re.S)
                if stripped.count("{") != stripped.count("}"):
                    diagnostics.append(_diagnostic("error", path, "Unbalanced braces.", source="syntax"))
        except SyntaxError as exc:
            diagnostics.append(_diagnostic("error", path, exc.msg, exc.lineno, exc.offset, "syntax"))
        except Exception as exc:
            diagnostics.append(_diagnostic("error", path, str(exc), source="syntax"))
    return diagnostics

def _diagnostics_summary(diagnostics):
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in diagnostics:
        level = item.get("severity", "info")
        counts[level] = counts.get(level, 0) + 1
    return {"count": len(diagnostics), "errors": counts.get("error", 0), "warnings": counts.get("warning", 0), "infos": counts.get("info", 0)}

def _validate_staged_files(files, strict=False):
    diagnostics = []
    for path, content in files.items():
        try:
            if path.endswith(".py"):
                import ast
                ast.parse(content, filename=path)
            elif path.endswith(".json"):
                json.loads(content)
        except Exception as exc:
            diagnostics.append({"severity":"error","path":path,"line":getattr(exc,"lineno",1) or 1,"column":getattr(exc,"offset",1) or 1,"message":str(exc)[:500],"source":"patch-validator"})
    return {"ok":not diagnostics,"diagnostics":diagnostics,"checked_files":len(files),"strict":bool(strict)}

def _workspace_revision(workspace_id):
    state = _ide_state(workspace_id)
    return int(state["revision"])

def _state_snapshot(workspace_id):
    with IDE_STATE_LOCK:
        state = _ide_state(workspace_id)
        return {
            "schema_version": state["schema_version"],
            "workspace_id": state["workspace_id"],
            "revision": state["revision"],
            "updated_at": state["updated_at"],
            "execution": dict(state["execution"]),
            "diagnostics": dict(state["diagnostics"]),
            "debugger": dict(state["debugger"]),
            "last_event_sequence": state["last_event_sequence"],
        }

@app.get("/state/{workspace_id}")
def ide_state(workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    if not re.fullmatch(r"[0-9]+", str(workspace_id)):
        raise HTTPException(status_code=400, detail="Invalid workspace id.")
    return _state_snapshot(workspace_id)

@app.get("/state/{workspace_id}/sync")
def ide_state_sync(workspace_id: str, since_sequence: int = 0, authorization: str = Header(default="")):
    auth(authorization)
    if not re.fullmatch(r"[0-9]+", str(workspace_id)):
        raise HTTPException(status_code=400, detail="Invalid workspace id.")
    state = _state_snapshot(workspace_id)
    events = [
        event for event in event_since(since_sequence, 500)
        if str(event.get("workspace_id")) == str(workspace_id)
    ]
    return {
        "schema_version": "1",
        "state": state,
        "events": events,
        "latest_sequence": event_snapshot().get("latest_sequence", 0),
        "resync_required": bool(events and events[0].get("sequence", 0) > int(since_sequence) + 1),
    }

@app.get("/events")
def events(since_sequence: int = 0, limit: int = 100, authorization: str = Header(default="")):
    auth(authorization)
    return event_snapshot() | {"events": event_since(since_sequence, limit)}

@app.get("/capabilities")
def capabilities(authorization: str = Header(default="")):
    """IDE capability handshake used before execution/install/preview operations."""
    auth(authorization)
    return _capability_manifest()

@app.post("/state/{workspace_id}/assert")
def assert_ide_revision(workspace_id: str, payload: dict, authorization: str = Header(default="")):
    auth(authorization)
    if not re.fullmatch(r"[0-9]+", str(workspace_id)):
        raise HTTPException(status_code=400, detail="Invalid workspace id.")
    expected = payload.get("revision")
    if expected is None:
        raise HTTPException(status_code=400, detail="revision is required.")
    current = _state_snapshot(workspace_id)
    if int(expected) != int(current["revision"]):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STATE_REVISION_CONFLICT",
                "expected_revision": int(expected),
                "current_revision": int(current["revision"]),
                "last_event_sequence": current["last_event_sequence"],
            },
        )
    return {"ok": True, "state": current}

@app.get("/workspace/{workspace_id}/snapshots")
def list_workspace_snapshots(workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    folder = _snapshot_dir(workspace_id)
    items = []
    for p in sorted([p for p in folder.iterdir() if p.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            items.append(json.loads((p / ".metadata.json").read_text(encoding="utf-8")))
        except Exception:
            continue
    return {"schema_version":"1","snapshots":items}

@app.post("/workspace/{workspace_id}/snapshots/{snapshot_id}/restore")
def restore_workspace_snapshot(workspace_id: str, snapshot_id: str, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(workspace_id)
    with _workspace_lock(workspace_id):
        current = _workspace_revision(workspace_id)
        _create_snapshot(workspace_id, root, current, "before-restore")
        files = _restore_snapshot(workspace_id, root, snapshot_id)
        event = event_publish("workspace.snapshot.restored", source="workspace", workspace_id=workspace_id, snapshot_id=snapshot_id)
        return {"status":"restored","workspace_id":workspace_id,"snapshot_id":snapshot_id,"files":files,"event":event}

@app.post("/workspace/diagnostics")
def workspace_diagnostics(workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(workspace_id)
    diagnostics = _collect_diagnostics(snapshot(root))
    summary = _diagnostics_summary(diagnostics)
    with IDE_STATE_LOCK:
        state = _ide_state(workspace_id)
        state["diagnostics"] = {"count": summary["count"], "items": diagnostics}
        state["updated_at"] = time.time()
    return {"schema_version": "1", "workspace_id": workspace_id, "diagnostics": diagnostics, "summary": summary}

@app.post("/workspace/patch/validate")
def validate_workspace_patch(payload: PatchValidationRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    current = _workspace_revision(payload.workspace_id)
    if current != payload.expected_revision:
        raise HTTPException(status_code=409, detail={"code":"WORKSPACE_REVISION_CONFLICT","expected_revision":payload.expected_revision,"current_revision":current})
    current_files = snapshot(root)
    staged = dict(current_files)
    for op in payload.operations:
        path = safe_rel(op.path)
        if op.action in {"write","create"}:
            if op.content is None:
                raise HTTPException(status_code=400, detail="Patch content is required.")
            staged[path] = op.content
        elif op.action == "delete":
            staged.pop(path, None)
        else:
            raise HTTPException(status_code=400, detail="Unsupported patch operation.")
    result = _validate_staged_files(staged, payload.strict)
    return {"schema_version":"1","patch_id":payload.patch_id,"workspace_id":payload.workspace_id,"revision":current,"validation":result}

@app.post("/workspace/patch/preview")
def preview_workspace_patch(payload: PatchRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    current = _workspace_revision(payload.workspace_id)
    if current != payload.expected_revision:
        raise HTTPException(status_code=409, detail={"code":"WORKSPACE_REVISION_CONFLICT","expected_revision":payload.expected_revision,"current_revision":current})
    current_files = snapshot(root)
    diffs = []
    for op in payload.operations:
        path = safe_rel(op.path)
        before = current_files.get(path, "")
        after = op.content or ""
        if op.action == "delete":
            after = ""
        elif op.action not in {"write", "create"}:
            raise HTTPException(status_code=400, detail="Unsupported patch operation.")
        if before != after:
            import difflib
            diffs.append({"path":path,"action":op.action,"diff":"".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=path, tofile=path))})
    return {"schema_version":"1","patch_id":payload.patch_id,"workspace_id":payload.workspace_id,"revision":current,"files_changed":len(diffs),"diffs":diffs}

@app.post("/workspace/patch/apply")
def apply_workspace_patch(payload: PatchRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    with _workspace_lock(payload.workspace_id):
        current = _workspace_revision(payload.workspace_id)
        if current != payload.expected_revision:
            raise HTTPException(status_code=409, detail={"code":"WORKSPACE_REVISION_CONFLICT","expected_revision":payload.expected_revision,"current_revision":current})
        staged = snapshot(root)
        for op in payload.operations:
            path = safe_rel(op.path)
            if op.action in {"write","create"}:
                if op.content is None:
                    raise HTTPException(status_code=400, detail="Patch content is required.")
                staged[path] = op.content
            elif op.action == "delete":
                staged.pop(path, None)
            else:
                raise HTTPException(status_code=400, detail="Unsupported patch operation.")
        validation = _validate_staged_files(staged, strict=True)
        if not validation["ok"]:
            event_publish("ai.patch.rejected", source="ai-patch", workspace_id=payload.workspace_id, patch_id=payload.patch_id, diagnostics=validation["diagnostics"])
            raise HTTPException(status_code=422, detail={"code":"PATCH_VALIDATION_FAILED","validation":validation})
        checkpoint = _create_snapshot(payload.workspace_id, root, current, "before-ai-patch")
        atomic_write_snapshot(root, staged)
        verified = _post_apply_verify(snapshot(root), payload.workspace_id)
        if not verified["ok"]:
            _restore_snapshot(payload.workspace_id, root, _snapshot_id(checkpoint))
            event_publish("ai.patch.rollback", source="ai-patch", workspace_id=payload.workspace_id, patch_id=payload.patch_id, diagnostics=verified["diagnostics"])
            raise HTTPException(status_code=422, detail={"code":"PATCH_POST_APPLY_FAILED","rolled_back":True,"validation":verified})
        event = event_publish("ai.patch.applied", source="ai-patch", workspace_id=payload.workspace_id, patch_id=payload.patch_id, files_changed=len(payload.operations))
        return {"status":"applied","patch_id":payload.patch_id,"workspace_id":payload.workspace_id,"revision":current + 1,"files":snapshot(root),"event":event}

@app.post("/workspace/transaction")
def workspace_transaction(payload: WorkspaceTransaction, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    lock = _workspace_lock(payload.workspace_id)
    with lock:
        current = _workspace_revision(payload.workspace_id)
        if current != payload.expected_revision:
            raise HTTPException(status_code=409, detail={
                "code": "WORKSPACE_REVISION_CONFLICT",
                "expected_revision": payload.expected_revision,
                "current_revision": current,
            })
        current_files = snapshot(root)
        staged = dict(current_files)
        try:
            for op in payload.operations:
                action = str(op.get("action", "")).lower()
                path = safe_rel(op.get("path", ""))
                if action in {"write", "create"}:
                    content = op.get("content", "")
                    if not isinstance(content, str):
                        raise ValueError("content must be text")
                    staged[path] = content
                elif action == "delete":
                    staged.pop(path, None)
                elif action == "rename":
                    target = safe_rel(op.get("target", ""))
                    if path not in staged:
                        raise ValueError("source file not found")
                    staged[target] = staged.pop(path)
                else:
                    raise ValueError("unsupported workspace operation")
            _create_snapshot(payload.workspace_id, root, current, "before-transaction")
            atomic_write_snapshot(root, staged)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        new_revision = current + 1
        event = event_publish(
            "workspace.transaction",
            source="workspace",
            workspace_id=payload.workspace_id,
            revision=new_revision,
            operation_count=len(payload.operations),
            active_file=payload.active_file or "",
        )
        return {
            "status": "committed",
            "workspace_id": payload.workspace_id,
            "revision": new_revision,
            "files": snapshot(root),
            "event": event,
        }

@app.post("/sync")
def sync(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    # Keep dependency directories, but replace source tree so the database is the source of truth.
    for child in root.iterdir():
        if child.name not in {".npm-cache", ".pip-cache", ".home", "node_modules", ".venv"}:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    write_snapshot(root, payload.files)
    return {"status": "synced", "files": snapshot(root)}

@app.post("/snapshot")
def get_snapshot(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    return {"files": snapshot(safe_workspace(payload.workspace_id))}

@app.post("/build/plan")
def build_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    try:
        return build_plan(payload.files or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])

@app.post("/build")
def build_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files or {})
    try:
        plan = build_plan(payload.files or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])
    started = time.monotonic()
    result = run_command(root, plan["strategy"]["command"])
    artifacts = artifact_manifest(payload.files or {}, str(root))
    return {"status":"success" if result.get("exit_code")==0 else "failed","plan":plan,"result":result,"artifacts":artifacts,"duration_ms":int((time.monotonic()-started)*1000)}


def build_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    try:
        return build_plan(payload.files or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])

@app.get("/packages/capabilities")
def package_capabilities_api(authorization: str = Header(default="")):
    auth(authorization)
    return package_capabilities()

@app.post("/packages/plan")
def package_plan_api(payload: PackagePlanRequest, authorization: str = Header(default="")):
    auth(authorization)
    try:
        return package_plan_response(payload.files or {}, payload.action, payload.package, payload.package_manager)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])

@app.post("/install")
def install(payload: InstallRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    # Package/framework provisioning is the only runner operation allowed to
    # use egress. Normal user execution remains network-isolated by default.
    result = run_command(root, payload.command, allow_network=True)
    return result

@app.post("/exec")
def execute(payload: ExecRequest, authorization: str = Header(default="")):
    """Execute one isolated command with API-safe error responses."""
    auth(authorization)
    try:
        root = safe_workspace(payload.workspace_id)
        files = payload.files or {}
        if not isinstance(files, dict):
            raise HTTPException(status_code=400, detail="Workspace files must be an object.")
        write_snapshot(root, files)
        return run_command(root, payload.command)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Runner execution timed out.")
    except Exception as exc:
        # Never leak stack traces or internal paths to the API consumer.
        raise HTTPException(
            status_code=500,
            detail=f"Runner execution failed: {exc.__class__.__name__}",
        )

# ---------------------------------------------------------------------------
# CLOUD IDE RUNTIME: process lifecycle, Git operations and preview services
# ---------------------------------------------------------------------------
PROCESS_LOCK = threading.RLock()
PROCESSES = {}
PROCESS_SEQ = 0
PREVIEW_PORT_BASE = int(os.environ.get("IDE_PREVIEW_PORT_BASE", "10000"))
PREVIEW_PORT_SPAN = int(os.environ.get("IDE_PREVIEW_PORT_SPAN", "1000"))
MAX_PROCESSES_PER_WORKSPACE = max(1, int(os.environ.get("RUNNER_MAX_PROCESSES_PER_WORKSPACE", "4")))
MAX_PROCESS_OUTPUT = max(10_000, int(os.environ.get("RUNNER_MAX_PROCESS_OUTPUT_BYTES", "200000")))
MAX_PROCESS_LIFETIME = min(
    3600,
    max(30, int(os.environ.get("RUNNER_MAX_PROCESS_LIFETIME_SECONDS", "1800"))),
)
PROCESS_REAPER_INTERVAL = min(60, max(5, int(os.environ.get("RUNNER_PROCESS_REAPER_INTERVAL_SECONDS", "15")))
STREAM_POLL_INTERVAL = min(2.0, max(0.1, float(os.environ.get("RUNNER_STREAM_POLL_INTERVAL_SECONDS", "0.25"))))
STREAM_MAX_SECONDS = min(3600, max(10, int(os.environ.get("RUNNER_STREAM_MAX_SECONDS", "1800"))))

def _process_output_reader(pid, stream_name, stream):
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            with PROCESS_LOCK:
                item = PROCESSES.get(pid)
                if not item:
                    break
                item[stream_name] = (item.get(stream_name, "") + line)[-MAX_PROCESS_OUTPUT:]
                event_publish("process.output", source="process", workspace_id=item["workspace_id"], process_id=pid, stream=stream_name, data=line)
                item["last_activity_at"] = time.time()
    finally:
        try:
            stream.close()
        except Exception:
            pass

def _start_process(root, command, *, allow_network=False, env_extra=None, kind="process"):
    global PROCESS_SEQ
    command = _validate_execution_policy(command, allow_network=allow_network)
    with PROCESS_LOCK:
        # Opportunistically reap exited records before enforcing the quota.
        _reap_processes_locked()
        active = [
            p for p in PROCESSES.values()
            if p["workspace_id"] == root.name and p["popen"].poll() is None
        ]
        if len(active) >= MAX_PROCESSES_PER_WORKSPACE:
            raise HTTPException(status_code=429, detail="Workspace process limit reached.")
        PROCESS_SEQ += 1
        process_id = str(PROCESS_SEQ)
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(root / ".home"),
        "npm_config_cache": str(root / ".npm-cache"),
        "PIP_CACHE_DIR": str(root / ".pip-cache"),
        "PYTHONUNBUFFERED": "1",
        "BASH_ENV": "/dev/null",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "npm_config_update_notifier": "false",
    }
    if env_extra:
        for key, value in dict(env_extra).items():
            if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", str(key)) and len(str(value)) <= 4000:
                env[str(key)] = str(value)
    (root / ".home").mkdir(exist_ok=True)
    argv = _sandbox_command(root, command, allow_network=allow_network)
    try:
        proc = subprocess.Popen(
            argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, start_new_session=True, preexec_fn=_limit_process_resources,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Process start failed: {exc.__class__.__name__}")
    with PROCESS_LOCK:
        PROCESSES[process_id] = {
            "id": process_id, "workspace_id": root.name, "command": command,
            "popen": proc, "stdout": "", "stderr": "", "started_at": time.time(),
            "kind": kind,
            "last_activity_at": time.time(),
        }
    threading.Thread(target=_process_output_reader, args=(process_id, "stdout", proc.stdout), daemon=True).start()
    threading.Thread(target=_process_output_reader, args=(process_id, "stderr", proc.stderr), daemon=True).start()
    return process_id

def _reap_processes_locked():
    """Remove completed process records while preserving recent state briefly."""
    now = time.time()
    stale = []
    for pid, item in PROCESSES.items():
        proc = item["popen"]
        if proc.poll() is not None and now - float(item.get("started_at", now)) > 300:
            stale.append(pid)
    for pid in stale:
        PROCESSES.pop(pid, None)

def _terminate_process_group(item, *, force=False):
    proc = item["popen"]
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

def _process_reaper_loop():
    """Enforce a wall-clock lifetime for long-running IDE processes."""
    while True:
        time.sleep(PROCESS_REAPER_INTERVAL)
        now = time.time()
        with PROCESS_LOCK:
            _reap_processes_locked()
            for item in list(PROCESSES.values()):
                proc = item["popen"]
                if proc.poll() is not None:
                    continue
                age = now - float(item.get("started_at", now))
                if age >= MAX_PROCESS_LIFETIME:
                    _terminate_process_group(item, force=True)
                    item["stderr"] = (
                        item.get("stderr", "")[-MAX_PROCESS_OUTPUT:]
                        + "\n[runner] process terminated: maximum lifetime exceeded."
                    )[-MAX_PROCESS_OUTPUT:]

threading.Thread(target=_process_reaper_loop, name="process-reaper", daemon=True).start()

def _stream_process_events(pid):
    started = time.monotonic()
    offsets = {"stdout": 0, "stderr": 0}
    last_status = None
    while time.monotonic() - started < STREAM_MAX_SECONDS:
        with PROCESS_LOCK:
            item = PROCESSES.get(str(pid))
            if not item:
                yield json.dumps({"type": "error", "process_id": str(pid), "detail": "Process not found."}) + "\n"
                return
            proc = item["popen"]
            status = "running" if proc.poll() is None else ("success" if proc.returncode == 0 else "failed")
            events = []
            for stream_name in ("stdout", "stderr"):
                data = item.get(stream_name, "")
                offset = offsets[stream_name]
                if len(data) < offset:
                    offset = 0
                if data[offset:]:
                    events.append({
                        "type": "output",
                        "stream": stream_name,
                        "data": data[offset:],
                    })
                    offsets[stream_name] = len(data)
            if status != last_status:
                events.append({
                    "type": "status",
                    "status": status,
                    "exit_code": proc.poll(),
                    "duration_ms": int((time.time() - item["started_at"]) * 1000),
                })
                last_status = status
        for event in events:
            yield json.dumps({
                "schema_version": "1",
                "timestamp": time.time(),
                "process_id": str(pid),
                **event,
            }, separators=(",", ":")) + "\n"
        if status != "running":
            return
        time.sleep(STREAM_POLL_INTERVAL)
    yield json.dumps({"type": "timeout", "process_id": str(pid), "max_stream_seconds": STREAM_MAX_SECONDS}, separators=(",", ":")) + "\n"

@app.get("/process/{pid}/stream")
def process_stream(pid: str, authorization: str = Header(default="")):
    auth(authorization)
    if not re.fullmatch(r"[0-9]+", str(pid)):
        raise HTTPException(status_code=400, detail="Invalid process id.")
    return StreamingResponse(
        _stream_process_events(pid),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

def _diagnostic_from_process(item):
    stderr = item.get("stderr", "")
    stdout = item.get("stdout", "")
    text = stderr if stderr.strip() else stdout
    diagnostics = []
    pattern = re.compile(r"(?P<path>[A-Za-z0-9_./\\-]+)?:(?P<line>\\d+)(?::(?P<column>\\d+))?:?\\s*(?P<message>.+)")
    for raw in text.splitlines()[-100:]:
        match = pattern.search(raw)
        if not match:
            continue
        diagnostics.append({
            "severity": "error" if item["popen"].poll() not in (None, 0) else "info",
            "path": match.group("path") or "",
            "line": int(match.group("line")),
            "column": int(match.group("column") or 1),
            "message": match.group("message")[:500],
            "source": "runner",
        })
    return diagnostics

@app.get("/process/{pid}/diagnostics")
def process_diagnostics(pid: str, authorization: str = Header(default="")):
    auth(authorization)
    with PROCESS_LOCK:
        item = PROCESSES.get(str(pid))
        if not item:
            raise HTTPException(status_code=404, detail="Process not found.")
        return {
            "schema_version": "1",
            "process_id": str(pid),
            "status": "running" if item["popen"].poll() is None else ("success" if item["popen"].returncode == 0 else "failed"),
            "diagnostics": _diagnostic_from_process(item),
        }

def _process_state(pid):
    with PROCESS_LOCK:
        item = PROCESSES.get(str(pid))
        if not item:
            raise HTTPException(status_code=404, detail="Process not found.")
        proc = item["popen"]
        code = proc.poll()
        state = "running" if code is None else ("success" if code == 0 else "failed")
        return {
            "id": item["id"], "workspace_id": item["workspace_id"], "command": item["command"],
            "status": state, "exit_code": code,
            "stdout": item.get("stdout", "")[-MAX_PROCESS_OUTPUT:],
            "stderr": item.get("stderr", "")[-MAX_PROCESS_OUTPUT:],
            "duration_ms": int((time.time() - item["started_at"]) * 1000),
            "started_at": item["started_at"],
            "max_lifetime_seconds": MAX_PROCESS_LIFETIME,
            "kind": item.get("kind", "process"),
        }

def _stop_process(pid):
    with PROCESS_LOCK:
        item = PROCESSES.get(str(pid))
        if not item:
            raise HTTPException(status_code=404, detail="Process not found.")
        proc = item["popen"]
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        return _process_state(pid)

def _workspace_preview_port(workspace_id):
    return PREVIEW_PORT_BASE + (int(workspace_id) % PREVIEW_PORT_SPAN)

def _git_run(root, args):
    if not isinstance(args, list) or not args:
        raise HTTPException(status_code=400, detail="Invalid Git request.")
    allowed = {"status", "diff", "branch", "log", "add", "reset", "commit", "rev-parse", "init", "checkout", "restore"}
    if args[0] not in allowed:
        raise HTTPException(status_code=400, detail="Git operation is not allowed.")
    if any("\x00" in str(x) or len(str(x)) > 500 for x in args):
        raise HTTPException(status_code=400, detail="Invalid Git argument.")
    if args[0] == "commit" and "-m" in args:
        idx = args.index("-m")
        if idx + 1 >= len(args) or not str(args[idx + 1]).strip():
            raise HTTPException(status_code=400, detail="Commit message is required.")
    command = "git " + " ".join(shlex.quote(str(x)) for x in args)
    result = run_command(root, command, allow_network=False)
    return result

@app.post("/environment/plan")
def environment_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return environment_plan(payload.files)

@app.get("/debug/capability")
def debug_capability_api(authorization: str = Header(default="")):
    auth(authorization)
    return debug_capability()

class DebugRequest(Workspace):
    action: str = "status"
    session_id: str = ""
    path: str = ""
    line: int = 0
    column: int = 1
    condition: str = ""
    expression: str = ""
    breakpoints: list = Field(default_factory=list)

@app.post("/debug")
def debug_api(payload: DebugRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.files:
        write_snapshot(root, payload.files)
    try:
        return debug_handle(
            payload.action, root=root, session_id=payload.session_id, path=payload.path,
            line=payload.line, column=payload.column, condition=payload.condition,
            expression=payload.expression, breakpoints=payload.breakpoints
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

class SymbolRequest(Workspace):
    action: str = "symbols"
    query: str = ""
    path: str = ""
    name: str = ""
    old: str = ""
    new: str = ""
    line: int = 0

def _format_source(path, source):
    """Safe best-effort formatter using installed toolchains when available."""
    ext=str(path).rsplit(".",1)[-1].lower() if "." in str(path) else ""
    if ext=="py":
        try:
            import black
            return black.format_file_contents(source, fast=False, mode=black.Mode())
        except Exception:
            return source
    if ext in {"js","jsx","ts","tsx","json","css","scss","html"}:
        try:
            proc=subprocess.run(["npx","--no-install","prettier","--stdin-filepath",str(path)],input=source,text=True,capture_output=True,cwd=str(safe_workspace("format")) if False else None,timeout=8)
            if proc.returncode==0:return proc.stdout
        except Exception: pass
    return source

@app.post("/replace/preview")
def replace_preview_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    files=payload.files or {}
    path=str(files.get("__path__") or "").strip()
    old=str(files.get("__old__") or "")
    new=str(files.get("__new__") or "")
    content=str(files.get("__content__") or "")
    if not path or not old or len(content)>2_000_000:
        raise HTTPException(status_code=400,detail="Invalid replace preview payload.")
    occurrences=content.count(old)
    if occurrences>0:
        updated=content.replace(old,new)
    else:
        updated=content
    return {"path":path,"occurrences":occurrences,"changed":updated!=content,"content":updated}

@app.post("/format")
def format_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    path=str((payload.files or {}).get("__path__") or "").strip()
    source=str((payload.files or {}).get("__content__") or "")
    if not path or len(source)>1_000_000:
        raise HTTPException(status_code=400,detail="Invalid formatting payload.")
    return {"path":path,"content":_format_source(path,source),"changed":_format_source(path,source)!=source}

@app.post("/symbols")
def symbols_api(payload: SymbolRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.files:
        write_snapshot(root, payload.files)
    try:
        if payload.action == "symbols":
            return {"symbols": symbol_index(root, payload.query, payload.path)}
        if payload.action == "references":
            return {"references": symbol_references(root, payload.name, payload.path)}
        if payload.action == "rename_preview":
            return {"preview": symbol_rename_preview(root, payload.old, payload.new, payload.path)}
        if payload.action == "rename_diff":
            return {"preview": symbol_rename_diff(root, payload.old, payload.new, payload.path)}
        if payload.action == "code_actions":
            return {"actions": symbol_code_actions(root, payload.path, payload.line)}
        if payload.action == "diagnostics":
            return {"diagnostics": symbol_diagnostics(root, payload.path)}
        if payload.action == "definitions":
            return {"definitions": symbol_definitions(root, payload.name, payload.path)}
        if payload.action == "hover":
            return {"hover": symbol_hover(root, payload.name, payload.path, payload.line)}
        if payload.action == "completion":
            return {"completions": symbol_completion(root, payload.query, payload.path)}
        raise HTTPException(status_code=400, detail="Unsupported symbol action.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/process/start")
def process_start(payload: ExecRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    pid = _start_process(root, payload.command)
    return _process_state(pid)

@app.get("/process/{pid}")
def process_get(pid: str, workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    state = _process_state(pid)
    if state["workspace_id"] != str(workspace_id):
        raise HTTPException(status_code=403, detail="Process/workspace mismatch.")
    return state

@app.post("/process/{pid}/stop")
def process_stop(pid: str, workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    state = _process_state(pid)
    if state["workspace_id"] != str(workspace_id):
        raise HTTPException(status_code=403, detail="Process/workspace mismatch.")
    return _stop_process(pid)

@app.get("/processes")
def process_list(workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    with PROCESS_LOCK:
        ids = [p["id"] for p in PROCESSES.values() if p["workspace_id"] == str(workspace_id)]
    return {"processes": [_process_state(pid) for pid in ids], "limits": {
        "max_per_workspace": MAX_PROCESSES_PER_WORKSPACE,
        "max_lifetime_seconds": MAX_PROCESS_LIFETIME,
        "max_output_bytes": MAX_PROCESS_OUTPUT,
    }}

@app.post("/git")
def git_api(payload: ExecRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    try:
        args = json.loads(payload.command)
    except Exception:
        raise HTTPException(status_code=400, detail="Git payload command must be a JSON array.")
    if not args or not isinstance(args, list):
        raise HTTPException(status_code=400, detail="Git arguments required.")
    return _git_run(root, args)

class AIRequest(Workspace):
    action: str = "fix"
    goal: str = ""
    active_file: str = ""
    paths: list = Field(default_factory=list)

class RecoveryRequest(Workspace):
    session_id: str = ""
    reason: str = "unknown"
    state: dict = Field(default_factory=dict)

@app.post("/recovery/checkpoint")
def recovery_checkpoint_api(payload: RecoveryRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return recovery_checkpoint(payload.session_id, payload.workspace_id, payload.files, payload.state)

@app.post("/recovery/restore")
def recovery_restore_api(payload: RecoveryRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return recovery_recover(payload.session_id, payload.reason)

@app.post("/recovery/verify")
def recovery_verify_api(payload: RecoveryRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return recovery_verified(payload.session_id)

@app.get("/extensions")
def extensions_api(authorization: str = Header(default="")):
    auth(authorization)
    return extension_manifest()

@app.get("/recovery/status")
def recovery_status_api(authorization: str = Header(default="")):
    auth(authorization)
    return recovery_status()

@app.get("/performance")
def performance_api(authorization: str = Header(default="")):
    auth(authorization)
    return profiler_report()

@app.get("/observability")
def observability_api(authorization: str = Header(default="")):
    auth(authorization)
    return observability_snapshot()

@app.post("/ai/engineering/plan")
def ai_engineering_plan_api(payload: AIRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.files:
        write_snapshot(root, payload.files)
    return ai_plan(payload.model_dump(), payload.files)

class AIPatchRequest(Workspace):
    patch: list = Field(default_factory=list)

@app.post("/ai/engineering/validate-patch")
def ai_engineering_validate_patch_api(payload: AIPatchRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    try:
        return {"status":"valid","patch":validate_ai_patch(payload.patch, payload.files)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/preview/plan")
def preview_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return preview_plan(payload.files)

@app.post("/preview/start")
def preview_start(payload: ExecRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    port = _workspace_preview_port(payload.workspace_id)
    command = str(payload.command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="Preview command is required.")
    # The command is supplied by a trusted framework preset on the API side.
    if any(re.search(pattern, command, re.I) for pattern in BLOCKED):
        raise HTTPException(status_code=400, detail="Preview command blocked.")
    pid = _start_process(
        root,
        command + f" --port {port}" if "--port" not in command and "runserver" not in command else command,
        allow_network=False,
        env_extra={"PORT": str(port)},
        kind="preview",
    )
    return {**_process_state(pid), "port": port, "preview_path": f"/api/ide/workspaces/{payload.workspace_id}/preview/"}

@app.get("/preview/{workspace_id}/{path:path}")
def preview_proxy(workspace_id: str, path: str, authorization: str = Header(default="")):
    auth(authorization)
    port = _workspace_preview_port(workspace_id)
    # This endpoint is intentionally internal; Django owns the public proxy.
    try:
        response = requests.get(f"http://127.0.0.1:{port}/{path}", timeout=10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Preview unavailable: {exc.__class__.__name__}")
    return Response(content=response.content, status_code=response.status_code, headers={"Content-Type": response.headers.get("content-type", "text/plain")})


def ide_state(workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    if not re.fullmatch(r"[0-9]+", str(workspace_id)):
        raise HTTPException(status_code=400, detail="Invalid workspace id.")
    with IDE_STATE_LOCK:
        state = dict(_ide_state(workspace_id))
        state["execution"] = dict(state["execution"])
        state["diagnostics"] = dict(state["diagnostics"])
        state["debugger"] = dict(state["debugger"])
    return state

@app.get("/events")
def events(since_sequence: int = 0, limit: int = 100, authorization: str = Header(default="")):
    auth(authorization)
    return event_snapshot() | {"events": event_since(since_sequence, limit)}

@app.get("/capabilities")
def capabilities(authorization: str = Header(default="")):
    """IDE capability handshake used before execution/install/preview operations."""
    auth(authorization)
    return _capability_manifest()

@app.post("/sync")
def sync(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    # Keep dependency directories, but replace source tree so the database is the source of truth.
    for child in root.iterdir():
        if child.name not in {".npm-cache", ".pip-cache", ".home", "node_modules", ".venv"}:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    write_snapshot(root, payload.files)
    return {"status": "synced", "files": snapshot(root)}

@app.post("/snapshot")
def get_snapshot(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    return {"files": snapshot(safe_workspace(payload.workspace_id))}

@app.post("/build/plan")
def build_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    try:
        return build_plan(payload.files or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])

@app.post("/build")
def build_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files or {})
    try:
        plan = build_plan(payload.files or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])
    started = time.monotonic()
    result = run_command(root, plan["strategy"]["command"])
    artifacts = artifact_manifest(payload.files or {}, str(root))
    return {"status":"success" if result.get("exit_code")==0 else "failed","plan":plan,"result":result,"artifacts":artifacts,"duration_ms":int((time.monotonic()-started)*1000)}


def build_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    try:
        return build_plan(payload.files or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])

@app.get("/packages/capabilities")
def package_capabilities_api(authorization: str = Header(default="")):
    auth(authorization)
    return package_capabilities()

@app.post("/packages/plan")
def package_plan_api(payload: PackagePlanRequest, authorization: str = Header(default="")):
    auth(authorization)
    try:
        return package_plan_response(payload.files or {}, payload.action, payload.package, payload.package_manager)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])

@app.post("/install")
def install(payload: InstallRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    # Package/framework provisioning is the only runner operation allowed to
    # use egress. Normal user execution remains network-isolated by default.
    result = run_command(root, payload.command, allow_network=True)
    return result

@app.post("/exec")
def execute(payload: ExecRequest, authorization: str = Header(default="")):
    """Execute one isolated command with API-safe error responses."""
    auth(authorization)
    try:
        root = safe_workspace(payload.workspace_id)
        files = payload.files or {}
        if not isinstance(files, dict):
            raise HTTPException(status_code=400, detail="Workspace files must be an object.")
        write_snapshot(root, files)
        return run_command(root, payload.command)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Runner execution timed out.")
    except Exception as exc:
        # Never leak stack traces or internal paths to the API consumer.
        raise HTTPException(
            status_code=500,
            detail=f"Runner execution failed: {exc.__class__.__name__}",
        )

# ---------------------------------------------------------------------------
# CLOUD IDE RUNTIME: process lifecycle, Git operations and preview services
# ---------------------------------------------------------------------------
PROCESS_LOCK = threading.RLock()
PROCESSES = {}
PROCESS_SEQ = 0
PREVIEW_PORT_BASE = int(os.environ.get("IDE_PREVIEW_PORT_BASE", "10000"))
PREVIEW_PORT_SPAN = int(os.environ.get("IDE_PREVIEW_PORT_SPAN", "1000"))
MAX_PROCESSES_PER_WORKSPACE = max(1, int(os.environ.get("RUNNER_MAX_PROCESSES_PER_WORKSPACE", "4")))
MAX_PROCESS_OUTPUT = max(10_000, int(os.environ.get("RUNNER_MAX_PROCESS_OUTPUT_BYTES", "200000")))
MAX_PROCESS_LIFETIME = min(
    3600,
    max(30, int(os.environ.get("RUNNER_MAX_PROCESS_LIFETIME_SECONDS", "1800"))),
)
PROCESS_REAPER_INTERVAL = min(60, max(5, int(os.environ.get("RUNNER_PROCESS_REAPER_INTERVAL_SECONDS", "15")))
STREAM_POLL_INTERVAL = min(2.0, max(0.1, float(os.environ.get("RUNNER_STREAM_POLL_INTERVAL_SECONDS", "0.25"))))
STREAM_MAX_SECONDS = min(3600, max(10, int(os.environ.get("RUNNER_STREAM_MAX_SECONDS", "1800"))))

def _process_output_reader(pid, stream_name, stream):
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            with PROCESS_LOCK:
                item = PROCESSES.get(pid)
                if not item:
                    break
                item[stream_name] = (item.get(stream_name, "") + line)[-MAX_PROCESS_OUTPUT:]
                event_publish("process.output", source="process", workspace_id=item["workspace_id"], process_id=pid, stream=stream_name, data=line)
                item["last_activity_at"] = time.time()
    finally:
        try:
            stream.close()
        except Exception:
            pass

def _start_process(root, command, *, allow_network=False, env_extra=None, kind="process"):
    global PROCESS_SEQ
    command = _validate_execution_policy(command, allow_network=allow_network)
    with PROCESS_LOCK:
        # Opportunistically reap exited records before enforcing the quota.
        _reap_processes_locked()
        active = [
            p for p in PROCESSES.values()
            if p["workspace_id"] == root.name and p["popen"].poll() is None
        ]
        if len(active) >= MAX_PROCESSES_PER_WORKSPACE:
            raise HTTPException(status_code=429, detail="Workspace process limit reached.")
        PROCESS_SEQ += 1
        process_id = str(PROCESS_SEQ)
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(root / ".home"),
        "npm_config_cache": str(root / ".npm-cache"),
        "PIP_CACHE_DIR": str(root / ".pip-cache"),
        "PYTHONUNBUFFERED": "1",
        "BASH_ENV": "/dev/null",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "npm_config_update_notifier": "false",
    }
    if env_extra:
        for key, value in dict(env_extra).items():
            if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", str(key)) and len(str(value)) <= 4000:
                env[str(key)] = str(value)
    (root / ".home").mkdir(exist_ok=True)
    argv = _sandbox_command(root, command, allow_network=allow_network)
    try:
        proc = subprocess.Popen(
            argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, start_new_session=True, preexec_fn=_limit_process_resources,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Process start failed: {exc.__class__.__name__}")
    with PROCESS_LOCK:
        PROCESSES[process_id] = {
            "id": process_id, "workspace_id": root.name, "command": command,
            "popen": proc, "stdout": "", "stderr": "", "started_at": time.time(),
            "kind": kind,
            "last_activity_at": time.time(),
        }
    threading.Thread(target=_process_output_reader, args=(process_id, "stdout", proc.stdout), daemon=True).start()
    threading.Thread(target=_process_output_reader, args=(process_id, "stderr", proc.stderr), daemon=True).start()
    return process_id

def _reap_processes_locked():
    """Remove completed process records while preserving recent state briefly."""
    now = time.time()
    stale = []
    for pid, item in PROCESSES.items():
        proc = item["popen"]
        if proc.poll() is not None and now - float(item.get("started_at", now)) > 300:
            stale.append(pid)
    for pid in stale:
        PROCESSES.pop(pid, None)

def _terminate_process_group(item, *, force=False):
    proc = item["popen"]
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

def _process_reaper_loop():
    """Enforce a wall-clock lifetime for long-running IDE processes."""
    while True:
        time.sleep(PROCESS_REAPER_INTERVAL)
        now = time.time()
        with PROCESS_LOCK:
            _reap_processes_locked()
            for item in list(PROCESSES.values()):
                proc = item["popen"]
                if proc.poll() is not None:
                    continue
                age = now - float(item.get("started_at", now))
                if age >= MAX_PROCESS_LIFETIME:
                    _terminate_process_group(item, force=True)
                    item["stderr"] = (
                        item.get("stderr", "")[-MAX_PROCESS_OUTPUT:]
                        + "\n[runner] process terminated: maximum lifetime exceeded."
                    )[-MAX_PROCESS_OUTPUT:]

threading.Thread(target=_process_reaper_loop, name="process-reaper", daemon=True).start()

def _stream_process_events(pid):
    started = time.monotonic()
    offsets = {"stdout": 0, "stderr": 0}
    last_status = None
    while time.monotonic() - started < STREAM_MAX_SECONDS:
        with PROCESS_LOCK:
            item = PROCESSES.get(str(pid))
            if not item:
                yield json.dumps({"type": "error", "process_id": str(pid), "detail": "Process not found."}) + "\n"
                return
            proc = item["popen"]
            status = "running" if proc.poll() is None else ("success" if proc.returncode == 0 else "failed")
            events = []
            for stream_name in ("stdout", "stderr"):
                data = item.get(stream_name, "")
                offset = offsets[stream_name]
                if len(data) < offset:
                    offset = 0
                if data[offset:]:
                    events.append({
                        "type": "output",
                        "stream": stream_name,
                        "data": data[offset:],
                    })
                    offsets[stream_name] = len(data)
            if status != last_status:
                events.append({
                    "type": "status",
                    "status": status,
                    "exit_code": proc.poll(),
                    "duration_ms": int((time.time() - item["started_at"]) * 1000),
                })
                last_status = status
        for event in events:
            yield json.dumps({
                "schema_version": "1",
                "timestamp": time.time(),
                "process_id": str(pid),
                **event,
            }, separators=(",", ":")) + "\n"
        if status != "running":
            return
        time.sleep(STREAM_POLL_INTERVAL)
    yield json.dumps({"type": "timeout", "process_id": str(pid), "max_stream_seconds": STREAM_MAX_SECONDS}, separators=(",", ":")) + "\n"

@app.get("/process/{pid}/stream")
def process_stream(pid: str, authorization: str = Header(default="")):
    auth(authorization)
    if not re.fullmatch(r"[0-9]+", str(pid)):
        raise HTTPException(status_code=400, detail="Invalid process id.")
    return StreamingResponse(
        _stream_process_events(pid),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

def _diagnostic_from_process(item):
    stderr = item.get("stderr", "")
    stdout = item.get("stdout", "")
    text = stderr if stderr.strip() else stdout
    diagnostics = []
    pattern = re.compile(r"(?P<path>[A-Za-z0-9_./\\-]+)?:(?P<line>\\d+)(?::(?P<column>\\d+))?:?\\s*(?P<message>.+)")
    for raw in text.splitlines()[-100:]:
        match = pattern.search(raw)
        if not match:
            continue
        diagnostics.append({
            "severity": "error" if item["popen"].poll() not in (None, 0) else "info",
            "path": match.group("path") or "",
            "line": int(match.group("line")),
            "column": int(match.group("column") or 1),
            "message": match.group("message")[:500],
            "source": "runner",
        })
    return diagnostics

@app.get("/process/{pid}/diagnostics")
def process_diagnostics(pid: str, authorization: str = Header(default="")):
    auth(authorization)
    with PROCESS_LOCK:
        item = PROCESSES.get(str(pid))
        if not item:
            raise HTTPException(status_code=404, detail="Process not found.")
        return {
            "schema_version": "1",
            "process_id": str(pid),
            "status": "running" if item["popen"].poll() is None else ("success" if item["popen"].returncode == 0 else "failed"),
            "diagnostics": _diagnostic_from_process(item),
        }

def _process_state(pid):
    with PROCESS_LOCK:
        item = PROCESSES.get(str(pid))
        if not item:
            raise HTTPException(status_code=404, detail="Process not found.")
        proc = item["popen"]
        code = proc.poll()
        state = "running" if code is None else ("success" if code == 0 else "failed")
        return {
            "id": item["id"], "workspace_id": item["workspace_id"], "command": item["command"],
            "status": state, "exit_code": code,
            "stdout": item.get("stdout", "")[-MAX_PROCESS_OUTPUT:],
            "stderr": item.get("stderr", "")[-MAX_PROCESS_OUTPUT:],
            "duration_ms": int((time.time() - item["started_at"]) * 1000),
            "started_at": item["started_at"],
            "max_lifetime_seconds": MAX_PROCESS_LIFETIME,
            "kind": item.get("kind", "process"),
        }

def _stop_process(pid):
    with PROCESS_LOCK:
        item = PROCESSES.get(str(pid))
        if not item:
            raise HTTPException(status_code=404, detail="Process not found.")
        proc = item["popen"]
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        return _process_state(pid)

def _workspace_preview_port(workspace_id):
    return PREVIEW_PORT_BASE + (int(workspace_id) % PREVIEW_PORT_SPAN)

def _git_run(root, args):
    if not isinstance(args, list) or not args:
        raise HTTPException(status_code=400, detail="Invalid Git request.")
    allowed = {"status", "diff", "branch", "log", "add", "reset", "commit", "rev-parse", "init", "checkout", "restore"}
    if args[0] not in allowed:
        raise HTTPException(status_code=400, detail="Git operation is not allowed.")
    if any("\x00" in str(x) or len(str(x)) > 500 for x in args):
        raise HTTPException(status_code=400, detail="Invalid Git argument.")
    if args[0] == "commit" and "-m" in args:
        idx = args.index("-m")
        if idx + 1 >= len(args) or not str(args[idx + 1]).strip():
            raise HTTPException(status_code=400, detail="Commit message is required.")
    command = "git " + " ".join(shlex.quote(str(x)) for x in args)
    result = run_command(root, command, allow_network=False)
    return result

@app.post("/environment/plan")
def environment_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return environment_plan(payload.files)

@app.get("/debug/capability")
def debug_capability_api(authorization: str = Header(default="")):
    auth(authorization)
    return debug_capability()

class DebugRequest(Workspace):
    action: str = "status"
    session_id: str = ""
    path: str = ""
    line: int = 0
    column: int = 1
    condition: str = ""
    expression: str = ""
    breakpoints: list = Field(default_factory=list)

@app.post("/debug")
def debug_api(payload: DebugRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.files:
        write_snapshot(root, payload.files)
    try:
        return debug_handle(
            payload.action, root=root, session_id=payload.session_id, path=payload.path,
            line=payload.line, column=payload.column, condition=payload.condition,
            expression=payload.expression, breakpoints=payload.breakpoints
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

class SymbolRequest(Workspace):
    action: str = "symbols"
    query: str = ""
    path: str = ""
    name: str = ""
    old: str = ""
    new: str = ""
    line: int = 0

def _format_source(path, source):
    """Safe best-effort formatter using installed toolchains when available."""
    ext=str(path).rsplit(".",1)[-1].lower() if "." in str(path) else ""
    if ext=="py":
        try:
            import black
            return black.format_file_contents(source, fast=False, mode=black.Mode())
        except Exception:
            return source
    if ext in {"js","jsx","ts","tsx","json","css","scss","html"}:
        try:
            proc=subprocess.run(["npx","--no-install","prettier","--stdin-filepath",str(path)],input=source,text=True,capture_output=True,cwd=str(safe_workspace("format")) if False else None,timeout=8)
            if proc.returncode==0:return proc.stdout
        except Exception: pass
    return source

@app.post("/replace/preview")
def replace_preview_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    files=payload.files or {}
    path=str(files.get("__path__") or "").strip()
    old=str(files.get("__old__") or "")
    new=str(files.get("__new__") or "")
    content=str(files.get("__content__") or "")
    if not path or not old or len(content)>2_000_000:
        raise HTTPException(status_code=400,detail="Invalid replace preview payload.")
    occurrences=content.count(old)
    if occurrences>0:
        updated=content.replace(old,new)
    else:
        updated=content
    return {"path":path,"occurrences":occurrences,"changed":updated!=content,"content":updated}

@app.post("/format")
def format_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    path=str((payload.files or {}).get("__path__") or "").strip()
    source=str((payload.files or {}).get("__content__") or "")
    if not path or len(source)>1_000_000:
        raise HTTPException(status_code=400,detail="Invalid formatting payload.")
    return {"path":path,"content":_format_source(path,source),"changed":_format_source(path,source)!=source}

@app.post("/symbols")
def symbols_api(payload: SymbolRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.files:
        write_snapshot(root, payload.files)
    try:
        if payload.action == "symbols":
            return {"symbols": symbol_index(root, payload.query, payload.path)}
        if payload.action == "references":
            return {"references": symbol_references(root, payload.name, payload.path)}
        if payload.action == "rename_preview":
            return {"preview": symbol_rename_preview(root, payload.old, payload.new, payload.path)}
        if payload.action == "rename_diff":
            return {"preview": symbol_rename_diff(root, payload.old, payload.new, payload.path)}
        if payload.action == "code_actions":
            return {"actions": symbol_code_actions(root, payload.path, payload.line)}
        if payload.action == "diagnostics":
            return {"diagnostics": symbol_diagnostics(root, payload.path)}
        if payload.action == "definitions":
            return {"definitions": symbol_definitions(root, payload.name, payload.path)}
        if payload.action == "hover":
            return {"hover": symbol_hover(root, payload.name, payload.path, payload.line)}
        if payload.action == "completion":
            return {"completions": symbol_completion(root, payload.query, payload.path)}
        raise HTTPException(status_code=400, detail="Unsupported symbol action.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/process/start")
def process_start(payload: ExecRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    pid = _start_process(root, payload.command)
    return _process_state(pid)

@app.get("/process/{pid}")
def process_get(pid: str, workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    state = _process_state(pid)
    if state["workspace_id"] != str(workspace_id):
        raise HTTPException(status_code=403, detail="Process/workspace mismatch.")
    return state

@app.post("/process/{pid}/stop")
def process_stop(pid: str, workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    state = _process_state(pid)
    if state["workspace_id"] != str(workspace_id):
        raise HTTPException(status_code=403, detail="Process/workspace mismatch.")
    return _stop_process(pid)

@app.get("/processes")
def process_list(workspace_id: str, authorization: str = Header(default="")):
    auth(authorization)
    with PROCESS_LOCK:
        ids = [p["id"] for p in PROCESSES.values() if p["workspace_id"] == str(workspace_id)]
    return {"processes": [_process_state(pid) for pid in ids], "limits": {
        "max_per_workspace": MAX_PROCESSES_PER_WORKSPACE,
        "max_lifetime_seconds": MAX_PROCESS_LIFETIME,
        "max_output_bytes": MAX_PROCESS_OUTPUT,
    }}

@app.post("/git")
def git_api(payload: ExecRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    try:
        args = json.loads(payload.command)
    except Exception:
        raise HTTPException(status_code=400, detail="Git payload command must be a JSON array.")
    if not args or not isinstance(args, list):
        raise HTTPException(status_code=400, detail="Git arguments required.")
    return _git_run(root, args)

class AIRequest(Workspace):
    action: str = "fix"
    goal: str = ""
    active_file: str = ""
    paths: list = Field(default_factory=list)

class RecoveryRequest(Workspace):
    session_id: str = ""
    reason: str = "unknown"
    state: dict = Field(default_factory=dict)

@app.post("/recovery/checkpoint")
def recovery_checkpoint_api(payload: RecoveryRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return recovery_checkpoint(payload.session_id, payload.workspace_id, payload.files, payload.state)

@app.post("/recovery/restore")
def recovery_restore_api(payload: RecoveryRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return recovery_recover(payload.session_id, payload.reason)

@app.post("/recovery/verify")
def recovery_verify_api(payload: RecoveryRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return recovery_verified(payload.session_id)

@app.get("/extensions")
def extensions_api(authorization: str = Header(default="")):
    auth(authorization)
    return extension_manifest()

@app.get("/recovery/status")
def recovery_status_api(authorization: str = Header(default="")):
    auth(authorization)
    return recovery_status()

@app.get("/performance")
def performance_api(authorization: str = Header(default="")):
    auth(authorization)
    return profiler_report()

@app.get("/observability")
def observability_api(authorization: str = Header(default="")):
    auth(authorization)
    return observability_snapshot()

@app.post("/ai/engineering/plan")
def ai_engineering_plan_api(payload: AIRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    if payload.files:
        write_snapshot(root, payload.files)
    return ai_plan(payload.model_dump(), payload.files)

class AIPatchRequest(Workspace):
    patch: list = Field(default_factory=list)

@app.post("/ai/engineering/validate-patch")
def ai_engineering_validate_patch_api(payload: AIPatchRequest, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    try:
        return {"status":"valid","patch":validate_ai_patch(payload.patch, payload.files)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/preview/plan")
def preview_plan_api(payload: Workspace, authorization: str = Header(default="")):
    auth(authorization)
    safe_workspace(payload.workspace_id)
    return preview_plan(payload.files)

@app.post("/preview/start")
def preview_start(payload: ExecRequest, authorization: str = Header(default="")):
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    port = _workspace_preview_port(payload.workspace_id)
    command = str(payload.command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="Preview command is required.")
    # The command is supplied by a trusted framework preset on the API side.
    if any(re.search(pattern, command, re.I) for pattern in BLOCKED):
        raise HTTPException(status_code=400, detail="Preview command blocked.")
    pid = _start_process(
        root,
        command + f" --port {port}" if "--port" not in command and "runserver" not in command else command,
        allow_network=False,
        env_extra={"PORT": str(port)},
        kind="preview",
    )
    return {**_process_state(pid), "port": port, "preview_path": f"/api/ide/workspaces/{payload.workspace_id}/preview/"}

@app.get("/preview/{workspace_id}/{path:path}")
def preview_proxy(workspace_id: str, path: str, authorization: str = Header(default="")):
    auth(authorization)
    port = _workspace_preview_port(workspace_id)
    # This endpoint is intentionally internal; Django owns the public proxy.
    try:
        response = requests.get(f"http://127.0.0.1:{port}/{path}", timeout=10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Preview unavailable: {exc.__class__.__name__}")
    return Response(content=response.content, status_code=response.status_code, headers={"Content-Type": response.headers.get("content-type", "text/plain")})

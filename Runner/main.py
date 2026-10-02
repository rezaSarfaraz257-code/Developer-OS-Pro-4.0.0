import os, re, subprocess, time, shutil, signal, resource, hmac, threading
import requests
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

app = FastAPI(title="Developer OS Secure Workspace Runner")
ROOT = Path("/workspaces")
TOKEN = os.environ.get("IDE_RUNNER_TOKEN", "")
MAX_FILE = 1_000_000
MAX_FILES = 2_000
MAX_WORKSPACE_BYTES = 50_000_000
MAX_OUTPUT = 50_000
TIMEOUT = min(120, max(5, int(os.environ.get("RUNNER_TIMEOUT_SECONDS", "120"))))
MAX_CONCURRENT = max(1, int(os.environ.get("RUNNER_MAX_CONCURRENT", "4")))
ALLOW_NETWORK = os.environ.get("RUNNER_ALLOW_NETWORK", "false").lower() in {"1", "true", "yes", "on"}
EXEC_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT)
WORKSPACE_LOCKS = {}
WORKSPACE_LOCKS_GUARD = threading.Lock()
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

class ExecRequest(Workspace):
    command: str = Field(min_length=1, max_length=2000)

class InstallRequest(ExecRequest):
    framework: str = ""
    package_manager: str = ""

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
    # bwrap is an optional defense-in-depth layer. If unavailable, the Docker
    # container limits still apply; production images should install bwrap.
    bwrap = shutil.which("bwrap")
    if not bwrap:
        if not allow_network and not ALLOW_NETWORK:
            # Never silently downgrade untrusted execution to a networked shell.
            raise HTTPException(status_code=503, detail="Secure sandbox is unavailable.")
        return ["bash", "-lc", command]
    args = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-uts",
        "--unshare-ipc", "--ro-bind", "/usr", "/usr", "--ro-bind", "/usr/local", "/usr/local",
        "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
        "--tmpfs", "/workspaces", "--bind", str(root), "/workspace",
        "--chdir", "/workspace", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--clearenv",
    ]
    if not allow_network and not ALLOW_NETWORK:
        args.append("--unshare-net")
    args.extend(["--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "--setenv", "HOME", "/workspace/.home", "--", "bash", "-lc", command])
    return args

def run_command(root, command, *, allow_network=False):
    command = str(command or "").strip()
    if not command or len(command) > MAX_COMMAND or "\x00" in command or any(ord(ch) < 9 for ch in command):
        raise HTTPException(status_code=400, detail="Invalid command.")
    if any(re.search(pattern, command, re.I) for pattern in BLOCKED):
        raise HTTPException(status_code=400, detail="Command blocked by sandbox policy.")
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
    started = time.monotonic()
    acquired = EXEC_SEMAPHORE.acquire(timeout=5)
    if not acquired:
        raise HTTPException(status_code=429, detail="Runner concurrency limit reached.")
    try:
        with _workspace_lock(root.name):
            proc = subprocess.run(
                _sandbox_command(root, command, allow_network=allow_network), cwd=root, env=env,
                capture_output=True, text=True, timeout=TIMEOUT,
                start_new_session=True,
                preexec_fn=_limit_process_resources,
            )
            return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-MAX_OUTPUT:],
            "stderr": proc.stderr[-MAX_OUTPUT:],
            "duration_ms": int((time.monotonic()-started)*1000),
            "files": snapshot(root),
        }
    except subprocess.TimeoutExpired as exc:
        # Kill the entire process group so timed-out dev servers/child processes
        # cannot survive the request and consume the shared runner.
        try:
            if "proc" in locals() and proc.pid:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return {
            "exit_code": 124,
            "stdout": (exc.stdout or "")[-MAX_OUTPUT:] if isinstance(exc.stdout, str) else "",
            "stderr": f"Execution timed out after {TIMEOUT} seconds.",
            "duration_ms": int((time.monotonic()-started)*1000),
            "files": snapshot(root),
        }
    finally:
        EXEC_SEMAPHORE.release()

@app.get("/health")
def health():
    sandbox_ready = bool(shutil.which("bwrap"))
    if not sandbox_ready:
        raise HTTPException(status_code=503, detail="Secure sandbox is unavailable.")
    return {
        "status": "ok",
        "service": "developer-os-runner",
        "sandbox": "bubblewrap",
        "network_policy": "isolated-by-default",
        "version": os.environ.get("RELEASE_VERSION", "3.0.0"),
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
    auth(authorization)
    root = safe_workspace(payload.workspace_id)
    write_snapshot(root, payload.files)
    return run_command(root, payload.command)

# ---------------------------------------------------------------------------
# CLOUD IDE RUNTIME: process lifecycle, Git operations and preview services
# ---------------------------------------------------------------------------
PROCESS_LOCK = threading.RLock()
PROCESSES = {}
PROCESS_SEQ = 0
PREVIEW_PORT_BASE = int(os.environ.get("IDE_PREVIEW_PORT_BASE", "10000"))
PREVIEW_PORT_SPAN = int(os.environ.get("IDE_PREVIEW_PORT_SPAN", "1000"))
MAX_PROCESSES_PER_WORKSPACE = 4
MAX_PROCESS_OUTPUT = 200_000

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
    finally:
        try:
            stream.close()
        except Exception:
            pass

def _start_process(root, command, *, allow_network=False, env_extra=None):
    global PROCESS_SEQ
    command = str(command or "").strip()
    if not command or len(command) > MAX_COMMAND or "\x00" in command:
        raise HTTPException(status_code=400, detail="Invalid process command.")
    if any(re.search(pattern, command, re.I) for pattern in BLOCKED):
        raise HTTPException(status_code=400, detail="Command blocked by sandbox policy.")
    with PROCESS_LOCK:
        active = [p for p in PROCESSES.values() if p["workspace_id"] == root.name and p["popen"].poll() is None]
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
            "kind": "process",
        }
    threading.Thread(target=_process_output_reader, args=(process_id, "stdout", proc.stdout), daemon=True).start()
    threading.Thread(target=_process_output_reader, args=(process_id, "stderr", proc.stderr), daemon=True).start()
    return process_id

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
    allowed = {"status", "diff", "branch", "log", "add", "reset", "commit", "rev-parse", "init", "checkout"}
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
    return {"processes": [_process_state(pid) for pid in ids]}

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
    pid = _start_process(root, command + f" --port {port}" if "--port" not in command and "runserver" not in command else command, allow_network=False, env_extra={"PORT": str(port)})
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


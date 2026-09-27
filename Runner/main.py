import os, re, subprocess, time, shutil, signal, resource, hmac, threading
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
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

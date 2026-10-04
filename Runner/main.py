import os, re, subprocess, time, shutil, signal, resource, hmac, threading, json, shlex
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
# Resource-aware execution policy. Conservative defaults are configurable for small managed containers.
MAX_MEMORY_MB = max(128, int(os.environ.get("RUNNER_MAX_MEMORY_MB", "768")))
MAX_MEMORY_BYTES = MAX_MEMORY_MB * 1024 * 1024
MAX_CPU_SECONDS = min(TIMEOUT, max(1, int(os.environ.get("RUNNER_MAX_CPU_SECONDS", str(TIMEOUT)))))
MAX_PROCESSES = max(16, int(os.environ.get("RUNNER_MAX_PROCESSES", "128")))
MAX_PROCESS_OUTPUT = max(16_384, int(os.environ.get("RUNNER_MAX_PROCESS_OUTPUT_BYTES", "200000")))
RESOURCE_QUEUE_SECONDS = max(1, int(os.environ.get("RUNNER_RESOURCE_QUEUE_SECONDS", "5")))
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
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS + 2))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WORKSPACE_BYTES, MAX_WORKSPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def _resource_status():
    """Return bounded container resource telemetry without exposing host paths."""
    status = {
        "policy": {
            "memory_mb": MAX_MEMORY_MB,
            "cpu_seconds": MAX_CPU_SECONDS,
            "max_processes": MAX_PROCESSES,
            "max_concurrent": MAX_CONCURRENT,
            "queue_seconds": RESOURCE_QUEUE_SECONDS,
            "output_bytes": MAX_OUTPUT,
        },
        "active_executions": MAX_CONCURRENT - getattr(EXEC_SEMAPHORE, "_value", MAX_CONCURRENT),
        "memory": {"limit_bytes": MAX_MEMORY_BYTES, "current_bytes": None, "available": True},
        "pressure": "normal",
    }
    try:
        current = Path("/sys/fs/cgroup/memory.current")
        limit = Path("/sys/fs/cgroup/memory.max")
        if current.exists() and limit.exists():
            cur = int(current.read_text().strip())
            raw_limit = limit.read_text().strip()
            lim = None if raw_limit == "max" else int(raw_limit)
            status["memory"]["current_bytes"] = cur
            if lim:
                status["memory"]["container_limit_bytes"] = lim
                ratio = cur / lim
                status["pressure"] = "critical" if ratio >= .90 else ("elevated" if ratio >= .75 else "normal")
                status["memory"]["available"] = ratio < .92
    except (OSError, ValueError):
        pass
    return status

def _resource_diagnostic(exit_code, stderr=""):
    text = str(stderr or "").lower()
    if exit_code in (-9, 137) or "out of memory" in text or "std::bad_alloc" in text or "cannot allocate memory" in text:
        return {"code": "RESOURCE_OOM", "severity": "error", "message": "Process exceeded the runner memory budget and was terminated safely.", "action": "Reduce workspace scope, exclude dependency directories, or run a project-aware typecheck."}
    if exit_code in (124, -24) or "timed out" in text:
        return {"code": "RESOURCE_TIMEOUT", "severity": "error", "message": "Process exceeded the execution time budget.", "action": "Run a narrower command or increase the configured runner timeout for trusted workloads."}
    return None

def _sandbox_command(root, command, allow_network=False):
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

def run_command(root, command, *, allow_network=False):
    command = str(command or "").strip()
    if not command or len(command) > MAX_COMMAND or "\x00" in command or any(ord(ch) < 9 for ch in command):
        raise HTTPException(status_code=400, detail="Invalid command.")
    if any(re.search(pattern, command, re.I) for pattern in BLOCKED):
        raise HTTPException(status_code=400, detail="Command blocked by sandbox policy.")
    # Keep the IDE contract stable on images that expose only python3.
    if re.match(r"^python(?:\s|$)", command) and not shutil.which("python") and shutil.which("python3"):
        command = re.sub(r"^python(?=\s|$)", "python3", command, count=1)
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
    acquired = EXEC_SEMAPHORE.acquire(timeout=RESOURCE_QUEUE_SECONDS)
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
            "resources": _resource_status(),
            "resource_diagnostic": {"code": "RESOURCE_TIMEOUT", "severity": "error", "message": f"Execution timed out after {TIMEOUT} seconds.", "action": "Run a narrower command or increase the configured timeout."},
        }
    finally:
        EXEC_SEMAPHORE.release()

def _runtime_info():
    """Expose deterministic runtime capabilities to the IDE without exposing host details."""
    candidates = {
        "python": ["python", "python3"],
        "node": ["node"],
        "kotlin": ["kotlinc", "kotlin"],
        "dotnet": ["dotnet"],
        "lua": ["lua"],
        "r": ["Rscript"],
        "dart": ["dart"],
        "swift": ["swift"],
        "elixir": ["elixir"],
        "erl": ["erl"],
        "npm": ["npm"],
        "go": ["go"],
        "rust": ["rustc"],
        "cargo": ["cargo"],
        "java": ["java"],
        "javac": ["javac"],
        "php": ["php"],
        "ruby": ["ruby"],
        "perl": ["perl"],
        "gcc": ["gcc"],
        "g++": ["g++"],
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

LANGUAGE_PROFILES = {
    "python": {"extensions":["py"],"runtime":"python","runner":"python","package_manager":"pip","run":"python {file}"},
    "javascript": {"extensions":["js","mjs","cjs"],"runtime":"node","runner":"node","package_manager":"npm","run":"node {file}"},
    "typescript": {"extensions":["ts"],"runtime":"node","runner":"npx","package_manager":"npm","run":"npx tsx {file}"},
    "jsx": {"extensions":["jsx"],"runtime":"node","runner":"node","package_manager":"npm","run":"node {file}"},
    "tsx": {"extensions":["tsx"],"runtime":"node","runner":"npx","package_manager":"npm","run":"npx tsx {file}"},
    "go": {"extensions":["go"],"runtime":"go","runner":"go","package_manager":"go","run":"go run {file}"},
    "rust": {"extensions":["rs"],"runtime":"rustc","runner":"rustc","package_manager":"cargo","run":"rustc {file} -o /tmp/dos_bin && /tmp/dos_bin"},
    "java": {"extensions":["java"],"runtime":"java","runner":"javac","package_manager":"maven","run":"javac {file} && java {basename}"},
    "kotlin": {"extensions":["kt","kts"],"runtime":"kotlin","runner":"kotlinc","package_manager":"gradle","run":"kotlinc {file} -include-runtime -d /tmp/dos.jar && java -jar /tmp/dos.jar"},
    "c": {"extensions":["c"],"runtime":"gcc","runner":"gcc","package_manager":None,"run":"gcc {file} -o /tmp/dos_bin && /tmp/dos_bin"},
    "cpp": {"extensions":["cc","cpp","cxx","hpp"],"runtime":"g++","runner":"g++","package_manager":None,"run":"g++ {file} -o /tmp/dos_bin && /tmp/dos_bin"},
    "csharp": {"extensions":["cs"],"runtime":"dotnet","runner":"dotnet","package_manager":"dotnet","run":"dotnet run"},
    "php": {"extensions":["php"],"runtime":"php","runner":"php","package_manager":"composer","run":"php {file}"},
    "ruby": {"extensions":["rb"],"runtime":"ruby","runner":"ruby","package_manager":"gem","run":"ruby {file}"},
    "perl": {"extensions":["pl","pm"],"runtime":"perl","runner":"perl","package_manager":"cpan","run":"perl {file}"},
    "shell": {"extensions":["sh","bash"],"runtime":"bash","runner":"bash","package_manager":None,"run":"bash {file}"},
    "lua": {"extensions":["lua"],"runtime":"lua","runner":"lua","package_manager":"luarocks","run":"lua {file}"},
    "r": {"extensions":["r"],"runtime":"r","runner":"Rscript","package_manager":"cran","run":"Rscript {file}"},
    "dart": {"extensions":["dart"],"runtime":"dart","runner":"dart","package_manager":"pub","run":"dart {file}"},
    "swift": {"extensions":["swift"],"runtime":"swift","runner":"swift","package_manager":"swiftpm","run":"swift {file}"},
    "elixir": {"extensions":["ex","exs"],"runtime":"elixir","runner":"elixir","package_manager":"mix","run":"elixir {file}"},
    "erlang": {"extensions":["erl","hrl"],"runtime":"erl","runner":"escript","package_manager":"rebar3","run":"escript {file}"},
    "fsharp": {"extensions":["fs","fsx"],"runtime":"dotnet","runner":"dotnet","package_manager":"dotnet","run":"dotnet fsi {file}"},
    "html": {"extensions":["html","htm"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "css": {"extensions":["css"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "scss": {"extensions":["scss"],"runtime":"node","runner":"npx","package_manager":"npm","run":"npx sass {file}"},
    "less": {"extensions":["less"],"runtime":"node","runner":"npx","package_manager":"npm","run":"npx lessc {file}"},
    "json": {"extensions":["json","jsonc"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "yaml": {"extensions":["yml","yaml"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "toml": {"extensions":["toml"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "markdown": {"extensions":["md","markdown","mdx"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "sql": {"extensions":["sql"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "graphql": {"extensions":["graphql","gql"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "xml": {"extensions":["xml"],"runtime":None,"runner":None,"package_manager":None,"run":None},
    "dockerfile": {"extensions":["dockerfile"],"runtime":None,"runner":None,"package_manager":None,"run":None},
}

def _capability_manifest():
    """Stable control-plane contract consumed by the Django IDE."""
    container_native = SANDBOX_MODE == "container"
    runtime_info = _runtime_info()
    languages = {}
    for name, profile in LANGUAGE_PROFILES.items():
        runtime = profile.get("runtime")
        languages[name] = {
            **profile,
            "available": bool(runtime is None or runtime_info.get(runtime, {}).get("available")),
            "status": "native" if runtime is None else ("ready" if runtime_info.get(runtime, {}).get("available") else "unavailable"),
        }
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
        "runtimes": runtime_info,
        "languages": languages,
        "limits": {
            "timeout_seconds": TIMEOUT,
            "max_concurrent": MAX_CONCURRENT,
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE,
            "max_workspace_bytes": MAX_WORKSPACE_BYTES,
            "max_command_bytes": MAX_COMMAND,
            "max_processes_per_workspace": MAX_PROCESSES_PER_WORKSPACE,
            "max_process_output_bytes": MAX_PROCESS_OUTPUT,
            "memory_mb": MAX_MEMORY_MB,
            "cpu_seconds": MAX_CPU_SECONDS,
            "max_processes": MAX_PROCESSES,
            "queue_seconds": RESOURCE_QUEUE_SECONDS,
        },
        "resources": _resource_status(),
        "operations": {"sync": True, "snapshot": True, "execute": True, "process": True, "git": True, "preview": True, "install": True, "debug": False},
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "developer-os-runner",
        "sandbox": "container-native" if SANDBOX_MODE == "container" else "bubblewrap",
        "sandbox_backend": SANDBOX_MODE,
        "bubblewrap_available": bool(shutil.which("bwrap")),
        "network_policy": ("container-runtime-policy" if SANDBOX_MODE == "container" else ("isolated-by-default" if not ALLOW_NETWORK else "provisioning-network")),
        "concurrency": {"max": MAX_CONCURRENT, "timeout_seconds": TIMEOUT},
        "runtimes": _runtime_info(),
        "capabilities_version": "1",
        "version": os.environ.get("RELEASE_VERSION", "3.2.0"),
    }

@app.get("/resources")
def resources(authorization: str = Header(default="")):
    auth(authorization)
    return _resource_status()

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


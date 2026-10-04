"""Project-aware build planner for the Developer OS IDE."""
from __future__ import annotations
import json, re
from typing import Any

def _base(p): return str(p).replace("\\","/").rstrip("/").split("/")[-1]

def _script(files, name="build"):
    raw=files.get("package.json")
    if not isinstance(raw,str): return None
    try: scripts=(json.loads(raw).get("scripts") or {})
    except Exception: return None
    value=scripts.get(name)
    if not isinstance(value,str) or not value.strip(): return None
    # Never execute an arbitrary script string supplied by a client unless it is
    # the project's declared build script. The runner still applies shell policy.
    return value.strip()

def detect_build(files: dict[str,Any]):
    names={_base(p) for p in files}
    script=_script(files)
    if "package.json" in names:
        if "next.config.js" in names or "next.config.mjs" in names or "next.config.ts" in names:
            return {"kind":"next","framework":"Next.js","command":"npm run build" if script else "npx next build","artifact_dirs":[".next"]}
        if any(x in names for x in ("vite.config.js","vite.config.ts","vite.config.mjs")):
            return {"kind":"vite","framework":"Vite","command":"npm run build" if script else "npx vite build","artifact_dirs":["dist"]}
        if script:
            return {"kind":"node","framework":"Node.js","command":"npm run build","artifact_dirs":["dist","build"]}
    if "manage.py" in names:
        return {"kind":"django","framework":"Django","command":"python3 manage.py check","artifact_dirs":["staticfiles"]}
    if "go.mod" in names:
        return {"kind":"go","framework":"Go","command":"go build ./...","artifact_dirs":[]}
    if "Cargo.toml" in names:
        return {"kind":"rust","framework":"Rust","command":"cargo build","artifact_dirs":["target"]}
    if "pom.xml" in names:
        return {"kind":"maven","framework":"Maven","command":"mvn -B package -DskipTests","artifact_dirs":["target"]}
    if "build.gradle" in names or "build.gradle.kts" in names:
        return {"kind":"gradle","framework":"Gradle","command":"gradle build","artifact_dirs":["build"]}
    if "pyproject.toml" in names:
        return {"kind":"python","framework":"Python","command":"python3 -m compileall -q .","artifact_dirs":[]}
    return None

def plan(files):
    result=detect_build(files or {})
    if not result: raise ValueError("No supported build target detected.")
    return {"status":"ready","strategy":result,"steps":["detect","dependency-check","build","diagnostics","artifacts","verify"]}

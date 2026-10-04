"""Project-aware build planner and executor policy for Developer OS."""
from __future__ import annotations
import json, os
from typing import Any

SAFE_BUILDERS = {
    "vite": {"framework":"Vite","kind":"vite","artifact_dirs":["dist"]},
    "next": {"framework":"Next.js","kind":"next","artifact_dirs":[".next"]},
    "node": {"framework":"Node.js","kind":"node","artifact_dirs":["dist","build"]},
    "django": {"framework":"Django","kind":"django","artifact_dirs":["staticfiles"]},
    "go": {"framework":"Go","kind":"go","artifact_dirs":[]},
    "rust": {"framework":"Rust","kind":"rust","artifact_dirs":["target"]},
    "maven": {"framework":"Maven","kind":"maven","artifact_dirs":["target"]},
    "gradle": {"framework":"Gradle","kind":"gradle","artifact_dirs":["build"]},
    "python": {"framework":"Python","kind":"python","artifact_dirs":[]},
}

def _base(p): return str(p).replace("\\","/").rstrip("/").split("/")[-1]

def _package_json(files):
    raw=files.get("package.json")
    if not isinstance(raw,str): return {}
    try:
        data=json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data,dict) else {}

def detect_build(files: dict[str,Any]):
    files=files or {}
    names={_base(p) for p in files}
    pkg=_package_json(files)
    scripts=pkg.get("scripts") if isinstance(pkg.get("scripts"),dict) else {}
    if "package.json" in names:
        if any(x in names for x in ("next.config.js","next.config.mjs","next.config.ts")):
            return {"kind":"next","framework":"Next.js","command":"npm run build" if "build" in scripts else "npx next build","artifact_dirs":[".next"]}
        if any(x in names for x in ("vite.config.js","vite.config.ts","vite.config.mjs")):
            return {"kind":"vite","framework":"Vite","command":"npm run build" if "build" in scripts else "npx vite build","artifact_dirs":["dist"]}
        if "build" in scripts:
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
    result=detect_build(files)
    if not result:
        raise ValueError("No supported build target detected.")
    return {"status":"ready","strategy":result,"steps":["detect","dependency-check","build","diagnostics","artifacts","verify"]}

def artifact_manifest(files, root):
    artifacts=[]
    for directory in (detect_build(files) or {}).get("artifact_dirs",[]):
        path=os.path.join(root,directory)
        if not os.path.isdir(path): continue
        for base, _, names in os.walk(path):
            for name in names:
                full=os.path.join(base,name)
                try: size=os.path.getsize(full)
                except OSError: continue
                artifacts.append({"path":os.path.relpath(full,root).replace(os.sep,"/"),"bytes":size})
                if len(artifacts)>=500: return artifacts
    return artifacts

"""Developer OS package-manager policy engine.

This module compiles high-level dependency operations into a small, audited
command vocabulary. It never accepts a raw shell command from the client.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

MANAGERS = {
    "npm": {"binary":"npm","manifest":"package.json","lockfiles":["package-lock.json"],"install":["install"],"add":["install"],"remove":["uninstall"],"update":["update"]},
    "pnpm": {"binary":"pnpm","manifest":"package.json","lockfiles":["pnpm-lock.yaml"],"install":["install"],"add":["add"],"remove":["remove"],"update":["update"]},
    "yarn": {"binary":"yarn","manifest":"package.json","lockfiles":["yarn.lock"],"install":["install"],"add":["add"],"remove":["remove"],"update":["upgrade"]},
    "pip": {"binary":"pip","manifest":"requirements.txt","lockfiles":[],"install":["install"],"add":["install"],"remove":["uninstall"],"update":["install","--upgrade"]},
    "uv": {"binary":"uv","manifest":"pyproject.toml","lockfiles":["uv.lock"],"install":["sync"],"add":["add"],"remove":["remove"],"update":["lock","--upgrade"]},
    "poetry": {"binary":"poetry","manifest":"pyproject.toml","lockfiles":["poetry.lock"],"install":["install"],"add":["add"],"remove":["remove"],"update":["update"]},
    "cargo": {"binary":"cargo","manifest":"Cargo.toml","lockfiles":["Cargo.lock"],"install":["fetch"],"add":["add"],"remove":["remove"],"update":["update"]},
    "go": {"binary":"go","manifest":"go.mod","lockfiles":["go.sum"],"install":["mod","download"],"add":["get"],"remove":["mod","tidy"],"update":["get","-u"]},
    "maven": {"binary":"mvn","manifest":"pom.xml","lockfiles":[],"install":["dependency:go-offline"],"add":[],"remove":[],"update":["versions:use-latest-releases"]},
    "gradle": {"binary":"gradle","manifest":"build.gradle","lockfiles":[],"install":["dependencies"],"add":[],"remove":[],"update":[]},
    "composer": {"binary":"composer","manifest":"composer.json","lockfiles":["composer.lock"],"install":["install","--no-interaction"],"add":["require","--no-interaction"],"remove":["remove","--no-interaction"],"update":["update","--no-interaction"]},
    "bundler": {"binary":"bundle","manifest":"Gemfile","lockfiles":["Gemfile.lock"],"install":["install"],"add":[],"remove":[],"update":["update"]},
    "pub": {"binary":"dart","manifest":"pubspec.yaml","lockfiles":["pubspec.lock"],"install":["pub","get"],"add":["pub","add"],"remove":["pub","remove"],"update":["pub","upgrade"]},
    "swiftpm": {"binary":"swift","manifest":"Package.swift","lockfiles":["Package.resolved"],"install":["package-resolve"],"add":[],"remove":[],"update":["package-update"]},
    "mix": {"binary":"mix","manifest":"mix.exs","lockfiles":["mix.lock"],"install":["deps.get"],"add":["deps.get"],"remove":[],"update":["deps.update","--all"]},
    "rebar3": {"binary":"rebar3","manifest":"rebar.config","lockfiles":[],"install":["get-deps"],"add":[],"remove":[],"update":["update"]},
    "dotnet": {"binary":"dotnet","manifest":"*.csproj","lockfiles":["packages.lock.json"],"install":["restore"],"add":["add","package"],"remove":["remove","package"],"update":["outdated"]},
}

ACTION_ALIASES = {"install":"install","add":"add","remove":"remove","update":"update","restore":"install"}

@dataclass(frozen=True)
class PackagePlan:
    manager: str
    action: str
    command: str
    network_required: bool
    manifest: str
    lockfiles: tuple[str, ...]

def _base(path: str) -> str:
    return str(path).replace("\\","/").rstrip("/").split("/")[-1]

def detect_manager(files: dict[str, Any]) -> dict[str, Any] | None:
    paths=[str(p).replace("\\","/") for p in files if isinstance(p,str)]
    names={_base(p) for p in paths}
    candidates=[]
    for mid,spec in MANAGERS.items():
        manifest=spec["manifest"]
        matched=any((_base(p).endswith(manifest[1:]) if manifest.startswith("*.") else _base(p)==manifest) for p in paths)
        if not matched:
            continue
        lock=next((x for x in spec["lockfiles"] if x in names), None)
        candidates.append({"id":mid,"manager":mid,"binary":spec["binary"],"manifest":manifest,"lockfile":lock,"confidence":1.0 if lock else 0.75})
    candidates.sort(key=lambda x:(x["lockfile"] is not None,x["confidence"]), reverse=True)
    return candidates[0] if candidates else None

def _package_name(value: str) -> str:
    value=str(value or "").strip()
    if not value or len(value)>214 or value.startswith("-") or any(c in value for c in "\r\n\x00"):
        raise ValueError("Invalid package name.")
    # Supports scoped npm packages and common version/range syntax without
    # allowing shell metacharacters.
    if not re.fullmatch(r"[@A-Za-z0-9_./:+~^<>=*-]+", value):
        raise ValueError("Invalid package name.")
    return value

def build_plan(files: dict[str, Any], action: str, package: str="", manager: str="") -> PackagePlan:
    detected=detect_manager(files)
    mid=(manager or (detected or {}).get("id") or "").lower()
    if mid not in MANAGERS:
        raise ValueError("No supported package manager was detected.")
    action=ACTION_ALIASES.get(str(action or "").lower())
    if not action:
        raise ValueError("Unsupported package action.")
    spec=MANAGERS[mid]
    verb=list(spec.get(action) or [])
    if not verb:
        raise ValueError(f"{mid} does not support '{action}' through the IDE policy.")
    args=[spec["binary"],*verb]
    if package:
        args.append(_package_name(package))
    # Package mutation runs with egress but disables lifecycle scripts where
    # the ecosystem has a safe equivalent.
    if mid in {"npm","pnpm","yarn"} and action in {"install","add","update"}:
        args.append("--ignore-scripts")
    command=" ".join(_quote(x) for x in args)
    return PackagePlan(mid,action,command,True,spec["manifest"],tuple(spec["lockfiles"]))

def _quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:+~^<>=*@,-]+", value):
        return value
    return "'" + value.replace("'","'\\''") + "'"

def capabilities() -> dict[str, Any]:
    return {"managers":[{"id":k,"binary":v["binary"],"manifest":v["manifest"],"lockfiles":v["lockfiles"],"actions":[a for a in ("install","add","remove","update") if v.get(a)]} for k,v in MANAGERS.items()]}

def plan_response(files: dict[str, Any], action: str, package: str="", manager: str="") -> dict[str, Any]:
    p=build_plan(files,action,package,manager)
    detected=detect_manager(files)
    return {"manager":p.manager,"action":p.action,"command":p.command,"network_required":p.network_required,"manifest":p.manifest,"lockfiles":list(p.lockfiles),"detected":detected}

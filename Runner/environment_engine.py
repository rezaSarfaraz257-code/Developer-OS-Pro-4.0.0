"""Developer OS isolated development-environment planner.

This module plans container/runtime requirements from workspace manifests.
It never launches privileged containers; execution remains owned by the
sandbox runner and deployment policy.
"""
from __future__ import annotations
import re

RUNTIMES={
 "node":{"manifests":{"package.json"},"runtime":"node","image_family":"node","default":"22"},
 "python":{"manifests":{"pyproject.toml","requirements.txt","Pipfile","poetry.lock"},"runtime":"python","image_family":"python","default":"3.12"},
 "go":{"manifests":{"go.mod"},"runtime":"go","image_family":"golang","default":"1.24"},
 "rust":{"manifests":{"Cargo.toml"},"runtime":"rust","image_family":"rust","default":"stable"},
 "java":{"manifests":{"pom.xml","build.gradle","build.gradle.kts"},"runtime":"java","image_family":"eclipse-temurin","default":"21"},
}
def plan(files):
    names={p.rsplit("/",1)[-1] for p in (files or {})}
    detected=[]
    for key,spec in RUNTIMES.items():
        if names & spec["manifests"]:
            detected.append({"id":key,**spec,"signals":sorted(names & spec["manifests"])})
    primary=detected[0] if detected else None
    return {
      "status":"ready",
      "mode":"isolated-runtime",
      "primary":primary,
      "runtimes":detected,
      "network":"disabled-by-default",
      "filesystem":"workspace-scoped",
      "privileged":False,
      "steps":["detect","resolve-runtime","apply-limits","mount-workspace","health-check"],
    }
def validate_runtime(runtime):
    value=str(runtime or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{1,40}",value):
        raise ValueError("Invalid runtime identifier.")
    return value

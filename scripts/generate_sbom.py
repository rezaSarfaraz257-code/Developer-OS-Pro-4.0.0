#!/usr/bin/env python3
"""Generate a deterministic, dependency-manifest SBOM.

This is intentionally manifest-based and does not install packages. It creates
an auditable inventory that CI scanners can enrich with vulnerability data.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "sbom.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

components = []

def add_python(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        value=line.strip()
        if not value or value.startswith("#") or value.startswith(("-", "--")):
            continue
        m=re.match(r"^([A-Za-z0-9_.-]+)\s*(==|>=|<=|~=|>|<)\s*([^;\s]+)", value)
        if m:
            name, op, version=m.groups()
            components.append({"type":"library","name":name,"version":version,"scope":"runtime","properties":[{"name":"source","value":str(path.relative_to(ROOT))},{"name":"constraint","value":op}]})

def add_npm(path: Path):
    if not path.exists():
        return
    data=json.loads(path.read_text(encoding="utf-8"))
    packages=data.get("packages",{})
    for key, meta in packages.items():
        if not key or not isinstance(meta,dict) or not meta.get("version"):
            continue
        name=meta.get("name") or key.rsplit("node_modules/",1)[-1]
        components.append({"type":"library","name":name,"version":meta["version"],"scope":"runtime","properties":[{"name":"source","value":str(path.relative_to(ROOT))}]})

add_python(ROOT/"Backend/requirements.txt")
add_python(ROOT/"Runner/requirements.txt")
add_npm(ROOT/"Frontend/package-lock.json")

unique={(x["name"],x["version"],x["properties"][0]["value"]):x for x in components}
components=sorted(unique.values(),key=lambda x:(x["name"].lower(),x["version"]))

doc={
    "bomFormat":"CycloneDX",
    "specVersion":"1.5",
    "version":1,
    "metadata":{"timestamp":datetime.now(timezone.utc).isoformat(),"tools":[{"vendor":"Developer OS","name":"generate_sbom.py","version":"1"}]},
    "components":components,
}
OUT.write_text(json.dumps(doc,indent=2,sort_keys=True)+"\n",encoding="utf-8")
print(f"SBOM written: {OUT} ({len(components)} components)")

"""Advanced preview orchestration for the Developer OS Cloud IDE."""
from __future__ import annotations
import re

def detect_preview(files):
    names={p.rsplit("/",1)[-1] for p in (files or {})}
    if "package.json" in names:
        pkg=files.get("package.json","")
        if re.search(r'"next"\s*:',pkg):
            return {"framework":"next","command":"npm run dev","port_strategy":"env"}
        if re.search(r'"vite"\s*:',pkg):
            return {"framework":"vite","command":"npm run dev -- --host 0.0.0.0","port_strategy":"env"}
        return {"framework":"node","command":"npm run dev","port_strategy":"env"}
    if "manage.py" in names:
        return {"framework":"django","command":"python3 manage.py runserver 0.0.0.0","port_strategy":"django"}
    return None

def health_policy():
    return {"initial_delay_ms":1000,"interval_ms":2000,"timeout_ms":3000,"max_failures":3}

def plan(files):
    target=detect_preview(files)
    if not target:
        return {"status":"unsupported","target":None,"health":health_policy()}
    return {"status":"ready","target":target,"health":health_policy(),
            "lifecycle":["detect","allocate-port","start","health-check","ready","recover","stop"]}

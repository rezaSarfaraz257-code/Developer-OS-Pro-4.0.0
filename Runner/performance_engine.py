"""Developer OS performance profiler primitives.

Collects bounded wall-clock, CPU and memory samples around engineering
operations without profiling arbitrary user code itself.
"""
from __future__ import annotations
import os
import resource
import time
from collections import deque

MAX_SAMPLES = 1000
_SAMPLES = deque(maxlen=MAX_SAMPLES)

def start(operation, workspace_id=None):
    usage=resource.getrusage(resource.RUSAGE_SELF)
    return {
        "operation":str(operation or "unknown")[:80],
        "workspace_id":workspace_id,
        "started_wall":time.perf_counter(),
        "started_cpu":usage.ru_utime + usage.ru_stime,
        "started_rss_kb":usage.ru_maxrss,
    }

def finish(state, status="ok", **fields):
    usage=resource.getrusage(resource.RUSAGE_SELF)
    wall_ms=round((time.perf_counter()-state["started_wall"])*1000,2)
    cpu_ms=round(((usage.ru_utime+usage.ru_stime)-state["started_cpu"])*1000,2)
    sample={
        "operation":state["operation"],"workspace_id":state["workspace_id"],
        "status":status,"wall_ms":wall_ms,"cpu_ms":cpu_ms,
        "max_rss_kb":usage.ru_maxrss,**fields,
    }
    _SAMPLES.append(sample)
    return sample

def report():
    samples=list(_SAMPLES)
    if not samples:
        return {"status":"ready","samples":0,"summary":{},"recent":[]}
    walls=[s["wall_ms"] for s in samples]
    cpus=[s["cpu_ms"] for s in samples]
    return {
        "status":"ready","samples":len(samples),
        "summary":{
            "wall_avg_ms":round(sum(walls)/len(walls),2),
            "wall_max_ms":max(walls),
            "cpu_avg_ms":round(sum(cpus)/len(cpus),2),
            "cpu_max_ms":max(cpus),
            "rss_max_kb":max(s["max_rss_kb"] for s in samples),
        },
        "recent":samples[-50:],
    }

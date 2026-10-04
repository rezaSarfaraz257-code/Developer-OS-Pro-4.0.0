"""Developer OS IDE observability primitives."""
from __future__ import annotations
import time
from collections import Counter, deque

MAX_EVENTS = 2000
_EVENTS = deque(maxlen=MAX_EVENTS)
_COUNTS = Counter()

def record(event, **fields):
    name=str(event or "unknown").strip().lower().replace(" ","_")[:80]
    item={"event":name,"timestamp":time.time(),**{str(k):v for k,v in fields.items()}}
    _EVENTS.append(item)
    _COUNTS[name]+=1
    return item

def trace(operation, workspace_id=None, **fields):
    started=time.perf_counter()
    item=record("trace.start",operation=operation,workspace_id=workspace_id,**fields)
    return {"trace_id":f"{int(item['timestamp']*1000000)}-{len(_EVENTS)}","started":started}

def finish(trace_state, status="ok", **fields):
    duration_ms=round((time.perf_counter()-trace_state["started"])*1000,2)
    return record("trace.finish",trace_id=trace_state["trace_id"],status=status,
                  duration_ms=duration_ms,**fields)

def snapshot():
    return {
        "status":"healthy",
        "event_buffer":{"capacity":MAX_EVENTS,"size":len(_EVENTS)},
        "counters":dict(_COUNTS),
        "recent_events":list(_EVENTS)[-50:],
    }

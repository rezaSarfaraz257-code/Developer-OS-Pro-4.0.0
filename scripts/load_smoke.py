#!/usr/bin/env python3
"""Small dependency-free authenticated load smoke test.

Example: python scripts/load_smoke.py http://localhost TOKEN 50 8
"""
import concurrent.futures, json, sys, time, urllib.request
base=sys.argv[1].rstrip('/'); token=sys.argv[2]; total=int(sys.argv[3]) if len(sys.argv)>3 else 50; workers=int(sys.argv[4]) if len(sys.argv)>4 else 8

def one(_):
    req=urllib.request.Request(base+'/api/workspace/summary/', headers={'Authorization':f'Bearer {token}'})
    started=time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            json.load(r); return True, time.perf_counter()-started, r.status
    except Exception:
        return False, time.perf_counter()-started, 0
with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
    results=list(pool.map(one, range(total)))
ok=[x for x in results if x[0]]; lat=[x[1] for x in results]
print(json.dumps({'requests':total,'workers':workers,'success':len(ok),'failure':total-len(ok),'avg_ms':round(sum(lat)/len(lat)*1000,2),'max_ms':round(max(lat)*1000,2)}))
sys.exit(0 if len(ok)==total else 1)

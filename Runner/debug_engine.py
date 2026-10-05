"""Live Python debugging over debugpy's Debug Adapter Protocol (DAP)."""
from __future__ import annotations
import importlib.util, json, socket, subprocess, threading, time, uuid, os, sys
from pathlib import Path

DEBUGPY_AVAILABLE = importlib.util.find_spec("debugpy") is not None
_LOCK=threading.RLock(); SESSIONS={}

def _port():
    s=socket.socket(); s.bind(("127.0.0.1",0)); p=s.getsockname()[1]; s.close(); return p

class DAP:
    def __init__(self,port):
        self.s=socket.create_connection(("127.0.0.1",port),timeout=10); self.s.settimeout(.5)
        self.seq=0; self.buf=b""; self.pending={}; self.events=[]; self.closed=False
        threading.Thread(target=self._read,daemon=True).start()
    def _read(self):
        while not self.closed:
            try:
                self.buf+=self.s.recv(65536)
                while b"\r\n\r\n" in self.buf:
                    h,b=self.buf.split(b"\r\n\r\n",1); n=None
                    for line in h.decode("ascii","replace").split("\r\n"):
                        if line.lower().startswith("content-length:"): n=int(line.split(":",1)[1])
                    if n is None or len(b)<n: self.buf=h+b"\r\n\r\n"+b; break
                    raw,self.buf=b[:n],b[n:]; m=json.loads(raw)
                    if m.get("type")=="response":
                        w=self.pending.get(m.get("request_seq"))
                        if w is not None: w.append(m)
                    else: self.events.append(m)
            except socket.timeout: continue
            except Exception: return
    def call(self,command,args=None,timeout=10):
        self.seq+=1; q=[]; seq=self.seq; self.pending[seq]=q
        m={"seq":seq,"type":"request","command":command}
        if args is not None:m["arguments"]=args
        raw=json.dumps(m,separators=(",",":")).encode()
        self.s.sendall(f"Content-Length: {len(raw)}\r\n\r\n".encode()+raw)
        end=time.monotonic()+timeout
        try:
            while time.monotonic()<end:
                if q:
                    r=q.pop(0)
                    if not r.get("success"): raise RuntimeError(r.get("message") or command)
                    return r.get("body") or {}
                time.sleep(.01)
            raise RuntimeError("DAP timeout: "+command)
        finally:self.pending.pop(seq,None)
    def drain(self):
        e=self.events[:]; self.events.clear(); return e
    def close(self):
        self.closed=True
        try:self.s.close()
        except OSError:pass

class Session:
    def __init__(self,root,path,line):
        self.id=uuid.uuid4().hex[:16]; self.root=Path(root).resolve()
        self.path=path; self.line=max(1,int(line or 1)); self.ap=_port(); self.dp=_port()
        self.adapter=None; self.debuggee=None; self.dap=None; self.state="starting"; self.stop_event=None; self.breakpoints=[]
    def start(self):
        if not DEBUGPY_AVAILABLE: raise RuntimeError("debugpy is not installed")
        target=(self.root/self.path).resolve()
        if self.root not in target.parents or not target.is_file(): raise RuntimeError("Invalid debug target")
        env={"PATH":"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin","HOME":str(self.root/".home"),"PYTHONUNBUFFERED":"1"}
        (self.root/".home").mkdir(exist_ok=True)
        # Use the same virtualenv interpreter as the runner process so
        # debugpy is imported from /opt/venv rather than system python3.
        self.adapter=subprocess.Popen(
            [sys.executable,"-m","debugpy.adapter","--host","127.0.0.1","--port",str(self.ap)],
            cwd=self.root,env=env,start_new_session=True,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,
        )
        end=time.monotonic()+15
        adapter_error=""
        while time.monotonic()<end:
            if self.adapter.poll() is not None:
                try: adapter_error=(self.adapter.stderr.read() or "").strip()
                except Exception: adapter_error=""
                break
            try:
                self.dap=DAP(self.ap)
                break
            except OSError:
                time.sleep(.05)
        if not self.dap:
            detail=adapter_error[-500:] if adapter_error else "adapter did not open its DAP port"
            raise RuntimeError("DAP adapter failed to start: "+detail)
        self.dap.call("initialize",{"clientID":"developer-os","adapterID":"debugpy","pathFormat":"path","linesStartAt1":True,"columnsStartAt1":True})
        self.debuggee=subprocess.Popen(
            [sys.executable,"-m","debugpy","--listen",f"127.0.0.1:{self.dp}",
             "--wait-for-client",str(target)],
            cwd=self.root,env=env,start_new_session=True,
        )
        self.dap.call("attach",{"name":"Developer OS","type":"python","request":"attach","connect":{"host":"127.0.0.1","port":self.dp},"justMyCode":False},15)
        self.dap.call("setExceptionBreakpoints",{"filters":[]})
        self.set_breakpoint(self.path,self.line)
        self.dap.call("configurationDone")
        self.dap.call("continue",{"threadId":1})
        self.state="running"; return self.snapshot()
    def _events(self):
        for e in self.dap.drain():
            if e.get("event")=="stopped": self.state="paused"; self.stop_event=e.get("body") or {}
            elif e.get("event")=="continued": self.state="running"
            elif e.get("event") in ("terminated","exited"): self.state="stopped"
    def set_breakpoint(self,path,line,column=1,condition=""):
        b=self.dap.call("setBreakpoints",{"source":{"path":str((self.root/path).resolve())},"breakpoints":[{"line":int(line),"column":int(column),**({"condition":condition} if condition else {})}],"sourceModified":False}).get("breakpoints",[])
        self.breakpoints=[{"path":path,"line":x.get("line",line),"column":x.get("column",column),"verified":bool(x.get("verified")),"id":x.get("id")} for x in b]
        return self.snapshot()
    def remove_breakpoint(self,path,line):
        self.dap.call("setBreakpoints",{"source":{"path":str((self.root/path).resolve())},"breakpoints":[]})
        self.breakpoints=[b for b in self.breakpoints if not(b["path"]==path and b["line"]==int(line))]
        return self.snapshot()
    def _thread(self):
        self._events(); return int((self.stop_event or {}).get("threadId") or 1)
    def stack(self):
        self._events(); return self.dap.call("stackTrace",{"threadId":self._thread(),"startFrame":0,"levels":50}).get("stackFrames",[])
    def action(self,a,expression=""):
        self._events()
        if a=="continue":self.dap.call("continue",{"threadId":self._thread()})
        elif a=="pause":self.dap.call("pause",{"threadId":self._thread()})
        elif a=="step_over":self.dap.call("next",{"threadId":self._thread()})
        elif a=="step_into":self.dap.call("stepIn",{"threadId":self._thread()})
        elif a=="step_out":self.dap.call("stepOut",{"threadId":self._thread()})
        elif a=="evaluate":
            frames=self.stack(); fid=frames[0]["id"] if frames else 1
            r=self.dap.call("evaluate",{"expression":expression,"frameId":fid,"context":"repl"})
            return {"result":{"expression":expression,"value":r.get("result"),"type":r.get("type")},"live":True}
        elif a=="stack":return {"stack":self.stack(),"live":True}
        elif a=="scopes":
            frames=self.stack(); return {"scopes":self.dap.call("scopes",{"frameId":frames[0]["id"]}).get("scopes",[]) if frames else [],"live":True}
        elif a=="variables":
            return {"variables":self.dap.call("variables",{"variablesReference":int(expression or 0)}).get("variables",[]),"live":True}
        self._events(); return self.snapshot()
    def snapshot(self):
        self._events(); st=self.stack() if self.state=="paused" else []
        return {"session_id":self.id,"state":self.state,"thread_id":(self.stop_event or {}).get("threadId"),"frame":st[0] if st else None,"stack":st,"breakpoints":self.breakpoints,"scopes":[],"variables":[],"diagnostics":[],"live":True}
    def stop(self):
        try:
            if self.dap:
                try:self.dap.call("disconnect",{"terminateDebuggee":True},3)
                except Exception:pass
                self.dap.close()
        finally:
            for p in (self.debuggee,self.adapter):
                if p and p.poll() is None:
                    try:p.kill()
                    except OSError:pass
            self.state="stopped"
        # Failed startup can leave DAP unset; cleanup must not call snapshot()
        # in that state.
        return {"session_id":self.id,"state":"stopped","breakpoints":self.breakpoints,"stack":[],"live":False}

def capability():
    return {"available":bool(DEBUGPY_AVAILABLE),"adapter":"debugpy" if DEBUGPY_AVAILABLE else None,"protocol":"DAP" if DEBUGPY_AVAILABLE else None,"mode":"live-dap" if DEBUGPY_AVAILABLE else "unavailable","reason":None if DEBUGPY_AVAILABLE else "debugpy is not installed"}

def handle(action,*,root=None,session_id="",path="",line=0,column=1,condition="",expression="",breakpoints=None):
    action=str(action or "status").lower()
    if action=="start":
        if not root:raise ValueError("Workspace root is required")
        s=Session(root,path,line)
        with _LOCK:SESSIONS[s.id]=s
        try:
            result=s.start()
            if not isinstance(result, dict):
                raise RuntimeError("Debugger returned an invalid session payload")
            # Keep the session identity authoritative even if a future
            # snapshot implementation changes its serialization.
            result["session_id"]=s.id
            return result
        except Exception:
            s.stop()
            with _LOCK:SESSIONS.pop(s.id,None)
            raise
    with _LOCK:s=SESSIONS.get(str(session_id))
    if not s:
        if action=="status":return {"session_id":None,"state":"idle","breakpoints":[],"stack":[],"live":False}
        raise ValueError("Debug session not found")
    if action=="set_breakpoint":return s.set_breakpoint(path,line,column,condition)
    if action=="remove_breakpoint":return s.remove_breakpoint(path,line)
    if action=="stop":
        r=s.stop()
        with _LOCK:SESSIONS.pop(s.id,None)
        return r
    if action in {"continue","pause","step_over","step_into","step_out","evaluate","stack","scopes","variables"}:return s.action(action,expression)
    if action=="status":return s.snapshot()
    raise ValueError("Unsupported debugger action")

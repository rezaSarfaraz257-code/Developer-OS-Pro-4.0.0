import asyncio, json, os, subprocess, threading
from pathlib import Path

class LSPError(RuntimeError):
    pass

LANGUAGE_COMMANDS = {
    "python": ["pyright-langserver", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
}

class LSPManager:
    """Workspace-scoped LSP lifecycle manager. Never executes workspace source."""
    def __init__(self):
        self.sessions = {}
        self.lock = threading.RLock()

    def capability(self):
        available = {}
        for language, command in LANGUAGE_COMMANDS.items():
            available[language] = bool(_which(command[0]))
        return {"available": available, "protocol": "LSP/stdio"}

    def session(self, workspace_id, root, language):
        if language not in LANGUAGE_COMMANDS:
            raise LSPError("Unsupported language")
        key=(str(workspace_id),language)
        with self.lock:
            current=self.sessions.get(key)
            if current and current.alive():
                return current
            current=LSPSession(root, LANGUAGE_COMMANDS[language])
            current.start()
            self.sessions[key]=current
            return current

    def stop_workspace(self, workspace_id):
        with self.lock:
            for key, session in list(self.sessions.items()):
                if key[0]==str(workspace_id):
                    session.stop()
                    self.sessions.pop(key,None)

class LSPSession:
    def __init__(self, root, command):
        self.root=Path(root).resolve()
        self.command=command
        self.proc=None
        self.seq=0
        self.lock=threading.RLock()

    def start(self):
        self.proc=subprocess.Popen(self.command,cwd=self.root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=False)
        self.request("initialize",{"processId":os.getpid(),"rootUri":self.root.as_uri(),"capabilities":{},"workspaceFolders":[{"uri":self.root.as_uri(),"name":"workspace"}]})
        self.notify("initialized",{})

    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def _send(self,payload):
        raw=json.dumps(payload,separators=(",",":")).encode()
        self.proc.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode()+raw)
        self.proc.stdin.flush()

    def request(self,method,params,timeout=8):
        with self.lock:
            if not self.alive(): raise LSPError("LSP process unavailable")
            self.seq+=1; ident=self.seq
            self._send({"jsonrpc":"2.0","id":ident,"method":method,"params":params})
            return self._read_response(ident,timeout)

    def notify(self,method,params):
        with self.lock:
            if self.alive(): self._send({"jsonrpc":"2.0","method":method,"params":params})

    def _read_response(self,ident,timeout):
        import select,time
        end=time.time()+timeout
        while time.time()<end:
            if self.proc.poll() is not None: raise LSPError("LSP process exited")
            ready,_,_=select.select([self.proc.stdout],[],[],min(.25,max(.01,end-time.time())))
            if not ready: continue
            headers=b""
            while b"\r\n\r\n" not in headers:
                chunk=self.proc.stdout.read(1)
                if not chunk: raise LSPError("LSP closed stdout")
                headers+=chunk
            length=0
            for line in headers.decode(errors="replace").split("\r\n"):
                if line.lower().startswith("content-length:"): length=int(line.split(":",1)[1].strip())
            body=self.proc.stdout.read(length)
            msg=json.loads(body.decode("utf-8"))
            if msg.get("id")==ident: return msg
        raise LSPError("LSP request timeout")

    def stop(self):
        with self.lock:
            try:
                if self.alive(): self.request("shutdown",{},2)
            except Exception: pass
            try: self.notify("exit",{})
            except Exception: pass
            try: self.proc.kill()
            except Exception: pass

MANAGER=LSPManager()

def _which(command):
    import shutil
    return shutil.which(command)

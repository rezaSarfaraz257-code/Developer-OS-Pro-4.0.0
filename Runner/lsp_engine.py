import asyncio, json, os, subprocess, threading, time
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
        self.notifications=[]
        self._closed=False

    def start(self):
        self.proc=subprocess.Popen(self.command,cwd=self.root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=False,bufsize=0)
        self.request("initialize",{"processId":os.getpid(),"rootUri":self.root.as_uri(),"capabilities":{},"workspaceFolders":[{"uri":self.root.as_uri(),"name":"workspace"}]})
        self.notify("initialized",{})

    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def _send(self,payload):
        raw=json.dumps(payload,separators=(",",":")).encode()
        frame=f"Content-Length: {len(raw)}\r\n\r\n".encode()+raw
        view=memoryview(frame)
        while view:
            written=self.proc.stdin.write(view)
            if not written:
                raise LSPError("LSP process closed stdin while sending a message")
            view=view[written:]
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
    def drain_notifications(self):
        import select
        out=[]
        with self.lock:
            if not self.alive(): return out
            while True:
                ready,_,_=select.select([self.proc.stdout],[],[],0)
                if not ready: break
                msg=self._read_message()
                if msg is None: break
                if msg.get("id") is not None:
                    self.notifications.append(msg)
                else:
                    out.append(msg)
            out.extend(self.notifications)
            self.notifications.clear()
        return out

    def _read_message(self):
        headers = bytearray()
        while not headers.endswith(b"\r\n\r\n"):
            chunk = self.proc.stdout.read(1)
            if not chunk:
                if not headers:
                    return None
                raise LSPError("LSP message headers were truncated")
            headers.extend(chunk)
            if len(headers) > 65536:
                raise LSPError("LSP message headers exceed the size limit")

        content_lengths = []
        try:
            header_lines = bytes(headers[:-4]).decode("ascii", errors="strict").split("\r\n")
        except UnicodeDecodeError as exc:
            raise LSPError("LSP message headers must be ASCII") from exc
        for line in header_lines:
            if not line:
                continue
            name, separator, value = line.partition(":")
            if not separator:
                raise LSPError("Malformed LSP message header")
            if name.strip().lower() == "content-length":
                content_lengths.append(value.strip())

        if len(content_lengths) != 1 or not content_lengths[0].isdigit():
            raise LSPError("LSP message must contain one valid Content-Length header")
        length = int(content_lengths[0])
        if length > 32 * 1024 * 1024:
            raise LSPError("LSP message body exceeds the size limit")

        body = bytearray()
        while len(body) < length:
            chunk = self.proc.stdout.read(length - len(body))
            if not chunk:
                raise LSPError("LSP message body was truncated")
            body.extend(chunk)
        body = bytes(body)
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LSPError("LSP message body is not valid UTF-8 JSON") from exc
        if not isinstance(message, dict):
            raise LSPError("LSP message must be a JSON object")
        return message

    def _read_response(self, ident, timeout):
        import select, time
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise LSPError("LSP process exited")
            ready, _, _ = select.select(
                [self.proc.stdout], [], [], min(.25, max(.01, end - time.time()))
            )
            if not ready:
                continue
            msg = self._read_message()
            if msg is None:
                raise LSPError("LSP closed stdout")
            if msg.get("id") == ident:
                return msg
            if msg.get("method") and msg.get("id") is None:
                self.notifications.append(msg)
            elif msg.get("id") is not None:
                self.notifications.append(msg)
        raise LSPError("LSP request timeout")

    def stop(self):
        with self.lock:
            if self._closed: return
            self._closed=True
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

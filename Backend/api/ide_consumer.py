import asyncio
import json
from urllib.parse import parse_qs

import requests
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth.models import User

from .models import CodeWorkspace
from .views import RUNNER_URL, RUNNER_TOKEN, _workspace_access_queryset

class IDETerminalConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token = parse_qs(self.scope.get("query_string", b"").decode()).get("token", [None])[0]
        if not token:
            await self.close(code=4401); return
        try:
            access = AccessToken(token)
            self.user_id = int(access["user_id"])
            self.user = await sync_to_async(User.objects.get)(pk=self.user_id)
        except Exception:
            await self.close(code=4401); return
        self.workspace_id = self.scope["url_route"]["kwargs"]["workspace_id"]
        allowed = await sync_to_async(lambda: _workspace_access_queryset(self.user).filter(pk=self.workspace_id).exists())()
        if not allowed:
            await self.close(code=4403); return
        await self.accept()
        await self.send_json({"type":"ready","workspace_id":self.workspace_id})

    def _runner(self, method, path, payload=None, timeout=20, workspace_id=None):
        headers={"Authorization":f"Bearer {RUNNER_TOKEN}"}
        kwargs={"headers":headers,"timeout":timeout}
        if method=="GET": kwargs["params"]={"workspace_id":str(workspace_id or self.workspace_id)}
        else: kwargs["json"]=payload
        return requests.request(method, f"{RUNNER_URL}{path}", **kwargs)

    async def receive_json(self, content, **kwargs):
        action=str(content.get("action") or "")
        if action=="start":
            command=str(content.get("command") or "").strip()
            if not command or len(command)>2000:
                await self.send_json({"type":"error","message":"Invalid command."}); return
            files=content.get("files") or {}
            response=await sync_to_async(self._runner)("POST","/process/start",{"workspace_id":str(self.workspace_id),"files":files,"command":command},60)
            data=response.json() if response.content else {}
            if response.status_code>=400:
                await self.send_json({"type":"error","message":data.get("detail") or data.get("error") or "Process start failed."}); return
            self.process_id=data["id"]
            await self.send_json({"type":"process","data":data})
            await self._stream_process()
        elif action=="stop" and getattr(self,"process_id",None):
            response=await sync_to_async(self._runner)("POST",f"/process/{self.process_id}/stop",{"workspace_id":str(self.workspace_id)},20)
            await self.send_json({"type":"process","data":response.json() if response.content else {}})
        elif action=="ping":
            await self.send_json({"type":"pong"})

    async def _stream_process(self):
        last_out=0; last_err=0
        while getattr(self,"process_id",None):
            response=await sync_to_async(self._runner)("GET",f"/process/{self.process_id}",None,10, self.workspace_id)
            if response.status_code>=400: break
            data=response.json()
            out=data.get("stdout",""); err=data.get("stderr","")
            if len(out)>last_out:
                await self.send_json({"type":"stdout","data":out[last_out:]}); last_out=len(out)
            if len(err)>last_err:
                await self.send_json({"type":"stderr","data":err[last_err:]}); last_err=len(err)
            if data.get("status")!="running":
                await self.send_json({"type":"exit","data":data}); break
            await asyncio.sleep(0.25)

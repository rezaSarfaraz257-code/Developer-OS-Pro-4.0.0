from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework_simplejwt.tokens import AccessToken
from urllib.parse import parse_qs
import time
import uuid
from django.utils import timezone
from datetime import timedelta

from .models import CodeWorkspace, WorkspaceCollaborationSession, WorkspaceFileRevision, WorkspaceFileLock

class IDECollaborationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token=parse_qs(self.scope.get("query_string",b"").decode()).get("token",[None])[0]
        if not token: await self.close(code=4401); return
        try:
            access=AccessToken(token); self.user_id=int(access["user_id"])
            self.user=await sync_to_async(User.objects.get)(pk=self.user_id)
            self.workspace=await sync_to_async(CodeWorkspace.objects.get)(pk=self.scope["url_route"]["kwargs"]["workspace_id"])
        except Exception:
            await self.close(code=4401); return
        allowed=await sync_to_async(self._allowed)()
        if not allowed: await self.close(code=4403); return
        self.client_id=parse_qs(self.scope.get("query_string",b"").decode()).get("client",[uuid.uuid4().hex[:16]])[0]
        self.group_name=f"ide.workspace.{self.workspace.id}"
        await self.channel_layer.group_add(self.group_name,self.channel_name)
        await self.accept()
        await sync_to_async(WorkspaceCollaborationSession.objects.update_or_create)(
            workspace=self.workspace,user=self.user,client_id=self.client_id,
            defaults={"cursor":{},"selection":{}}
        )
        await self.send_json({"type":"connected","workspace_id":self.workspace.id,"revision":self.workspace.revision,"client_id":self.client_id})
        await self.send_json(await sync_to_async(self._presence_snapshot)())

    def _allowed(self):
        return self.workspace.owner_id==self.user.id or self.workspace.project_id and self.workspace.project.collaborators.filter(id=self.user.id).exists()

    async def disconnect(self, code):
        if hasattr(self,"group_name"):
            await self.channel_layer.group_discard(self.group_name,self.channel_name)
            await sync_to_async(WorkspaceCollaborationSession.objects.filter(workspace=self.workspace,user=self.user,client_id=self.client_id).delete)()

    async def receive_json(self, content, **kwargs):
        kind=str(content.get("type") or "")
        if kind=="presence":
            await sync_to_async(WorkspaceCollaborationSession.objects.filter(workspace=self.workspace,user=self.user,client_id=self.client_id).update)(
                cursor=content.get("cursor") or {},selection=content.get("selection") or {},last_seen_at=__import__("django").utils.timezone.now()
            )
            await self.channel_layer.group_send(self.group_name,{"type":"presence_event","payload":{"type":"presence","user_id":self.user.id,"username":self.user.username,"client_id":self.client_id,"cursor":content.get("cursor") or {},"selection":content.get("selection") or {}})
        elif kind=="patch":
            result=await sync_to_async(self._apply_patch)(content)
            await self.send_json(result)
            if result.get("type")=="patch-ack":
                await self.channel_layer.group_send(self.group_name,{"type":"file_event","payload":result["event"]})
        elif kind=="lock":
            await self.send_json(await sync_to_async(self._lock_file)(content))
        elif kind=="unlock":
            await self.send_json(await sync_to_async(self._unlock_file)(content))
        elif kind=="resync":
            ws=await sync_to_async(CodeWorkspace.objects.get)(pk=self.workspace.id)
            await self.send_json({"type":"state","revision":ws.revision,"files":ws.files})
        elif kind=="request-state":
            ws=await sync_to_async(CodeWorkspace.objects.get)(pk=self.workspace.id)
            await self.send_json({"type":"state","revision":ws.revision,"files":ws.files})

    def presence_event(self,event):
        if event["payload"].get("client_id")!=getattr(self,"client_id",None):
            return self.send_json(event["payload"])

    def file_event(self,event):
        if event["payload"].get("client_id")!=getattr(self,"client_id",None):
            return self.send_json(event["payload"])

    def _presence_snapshot(self):
        now=timezone.now()
        rows=WorkspaceCollaborationSession.objects.filter(workspace=self.workspace,last_seen_at__gte=now-timedelta(seconds=45)).select_related("user")
        return {"type":"presence-snapshot","users":[{"user_id":r.user_id,"username":r.user.username,"client_id":r.client_id,"cursor":r.cursor,"selection":r.selection} for r in rows]}

    def _lock_file(self, content):
        path=str(content.get("path") or "").strip()
        if not path: return {"type":"lock","ok":False,"reason":"invalid_path"}
        now=timezone.now(); expires=now+timedelta(seconds=30)
        existing=WorkspaceFileLock.objects.filter(workspace=self.workspace,path=path).first()
        if existing and existing.expires_at>now and existing.client_id!=self.client_id:
            return {"type":"lock","ok":False,"reason":"locked","path":path,"username":existing.user.username,"expires_at":existing.expires_at.isoformat()}
        WorkspaceFileLock.objects.update_or_create(workspace=self.workspace,path=path,defaults={"user":self.user,"client_id":self.client_id,"expires_at":expires})
        return {"type":"lock","ok":True,"path":path,"client_id":self.client_id,"expires_at":expires.isoformat()}

    def _unlock_file(self, content):
        path=str(content.get("path") or "").strip()
        WorkspaceFileLock.objects.filter(workspace=self.workspace,path=path,client_id=self.client_id).delete()
        return {"type":"unlock","ok":True,"path":path}

    def _apply_patch(self, content):
        path=str(content.get("path") or "").strip()
        client_revision=int(content.get("base_revision") or 0)
        next_content=str(content.get("content") or "")
        client_id=str(content.get("client_id") or self.client_id)
        if not path or len(path)>500: return {"type":"conflict","reason":"invalid_path"}
        with transaction.atomic():
            ws=CodeWorkspace.objects.select_for_update().get(pk=self.workspace.id)
            lock=WorkspaceFileLock.objects.filter(workspace=ws,path=path,expires_at__gt=timezone.now()).first()
            if lock and lock.client_id!=self.client_id:
                return {"type":"conflict","reason":"file_locked","server_revision":ws.revision,"locked_by":lock.user.username,"server_content":ws.files.get(path,"")}
            if client_revision!=ws.revision:
                latest=WorkspaceFileRevision.objects.filter(workspace=ws,path=path).order_by("-revision").first()
                return {"type":"conflict","reason":"stale_revision","server_revision":ws.revision,"server_content":latest.content if latest else ws.files.get(path,"")}
            files=dict(ws.files or {})
            previous=str(files.get(path,""))
            files[path]=next_content
            ws.files=files
            ws.revision=(ws.revision or 0)+1
            ws.save(update_fields={"files","revision"})
            WorkspaceFileRevision.objects.create(workspace=ws,path=path,revision=ws.revision,content=next_content,author=self.user,client_id=client_id)
            return {"type":"patch-ack","revision":ws.revision,"event":{"type":"file-update","workspace_id":ws.id,"path":path,"content":next_content,"revision":ws.revision,"user_id":self.user.id,"username":self.user.username,"client_id":client_id}}

    async def collaboration_presence(self,event):
        return None

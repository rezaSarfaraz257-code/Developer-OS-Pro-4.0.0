from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework_simplejwt.tokens import AccessToken
from urllib.parse import parse_qs
import time
import uuid

from .models import CodeWorkspace, WorkspaceCollaborationSession, WorkspaceFileRevision

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
        elif kind=="request-state":
            ws=await sync_to_async(CodeWorkspace.objects.get)(pk=self.workspace.id)
            await self.send_json({"type":"state","revision":ws.revision,"files":ws.files})

    def presence_event(self,event):
        if event["payload"].get("client_id")!=getattr(self,"client_id",None):
            return self.send_json(event["payload"])

    def file_event(self,event):
        if event["payload"].get("client_id")!=getattr(self,"client_id",None):
            return self.send_json(event["payload"])

    def _apply_patch(self, content):
        path=str(content.get("path") or "").strip()
        client_revision=int(content.get("base_revision") or 0)
        next_content=str(content.get("content") or "")
        client_id=str(content.get("client_id") or self.client_id)
        if not path or len(path)>500: return {"type":"conflict","reason":"invalid_path"}
        with transaction.atomic():
            ws=CodeWorkspace.objects.select_for_update().get(pk=self.workspace.id)
            if client_revision!=ws.revision:
                latest=WorkspaceFileRevision.objects.filter(workspace=ws,path=path).order_by("-revision").first()
                return {"type":"conflict","reason":"stale_revision","server_revision":ws.revision,"server_content":latest.content if latest else ws.files.get(path,"")}
            files=dict(ws.files or {})
            previous=str(files.get(path,""))
            files[path]=next_content
            ws.files=files
            ws.revision=(ws.revision or 0)+1
            ws.save(update_fields={"files","revision","updated_at"})
            WorkspaceFileRevision.objects.create(workspace=ws,path=path,revision=ws.revision,content=next_content,author=self.user,client_id=client_id)
            return {"type":"patch-ack","revision":ws.revision,"event":{"type":"file-update","workspace_id":ws.id,"path":path,"content":next_content,"revision":ws.revision,"user_id":self.user.id,"username":self.user.username,"client_id":client_id}}

    async def collaboration_presence(self,event):
        return None

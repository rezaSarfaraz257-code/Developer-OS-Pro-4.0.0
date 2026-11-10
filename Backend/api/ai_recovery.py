"""Resilient AI chat entrypoint.

Keeps provider execution independent from optional conversation persistence,
workspace enrichment, and usage metering. This prevents a secondary database,
context, or serialization failure from turning a valid AI request into a 503.
"""
from __future__ import annotations

import logging

from django.db import DatabaseError
from django.http import Http404

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AIConversation, AIMessage
from .serializers import AIConversationSerializer, AIMessageSerializer
from .ai_engine import (
    _call_provider,
    _local_fallback,
    _safe_workspace_context,
    _compact_context,
    _provider_config,
)

logger = logging.getLogger("developer_os.ai.recovery")
MAX_HISTORY_MESSAGES = 16


def _safe_history(conversation):
    if conversation is None:
        return []
    try:
        return list(
            conversation.messages.order_by("-created_at")
            .values("role", "content")[:MAX_HISTORY_MESSAGES]
        )[::-1]
    except Exception:
        logger.exception("AI conversation history unavailable; continuing without history")
        return []


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat_api_recovery(request):
    message = str(request.data.get("message") or "").strip()
    if not message:
        return Response({"error": "message is required"}, status=400)
    if len(message) > 12000:
        return Response({"error": "message too long"}, status=400)

    project_id = request.data.get("project")
    conversation_id = request.data.get("conversation")
    workspace_id = request.data.get("workspace")

    # Usage is advisory here. The normal endpoint can fail during a legacy
    # schema transition; that must not prevent the AI provider from running.
    usage = {"used": None, "limit": None, "plan": "free", "metering": "degraded"}
    try:
        from .views import _consume_usage
        allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 0)
        usage.update({"used": used, "limit": limit, "plan": plan, "metering": "active"})
        if not allowed:
            return Response({"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)
    except Exception:
        logger.exception("AI usage preflight failed; continuing without metering")

    # A scoped request must prove project/conversation access before any
    # workspace context is collected or sent to a provider. Stateless fallback
    # is allowed only when the caller did not name a private resource.
    try:
        from .ai_engine import _conversation
        conversation = _conversation(request, project_id, conversation_id)
    except Http404:
        return Response({"error": "Project or conversation not found or unavailable."}, status=404)
    except DatabaseError:
        logger.warning("AI conversation access check unavailable error_type=DatabaseError")
        if project_id or conversation_id:
            return Response({"error": "Project or conversation access could not be verified."}, status=503)
        conversation = None
    except Exception as exc:
        logger.warning("AI conversation setup failed error_type=%s", type(exc).__name__)
        if project_id or conversation_id:
            return Response({"error": "Project or conversation access could not be verified."}, status=503)
        conversation = None

    if conversation is None and (project_id or conversation_id):
        return Response({"error": "Project or conversation not found or unavailable."}, status=404)

    try:
        context = _compact_context(
            _safe_workspace_context(
                request.user,
                conversation.project_id if conversation is not None else project_id,
                workspace_id,
            )
        )
    except Exception:
        logger.exception("AI context preparation failed; using empty context")
        context = {"projects": [], "tasks": [], "notes": [], "snippets": [], "workspaces": [], "context_warning": "Workspace context unavailable."}

    history = _safe_history(conversation)

    # Provider execution is now isolated from persistence/context failures.
    try:
        answer, provider_error = _call_provider(message, context, history)
    except Exception as exc:
        # Avoid logging provider-controlled exception text, which may echo
        # prompt fragments or request data.
        logger.warning("AI provider execution failed error_type=%s", type(exc).__name__)
        answer = ""
        provider_error = '{"code":"AI_INTERNAL_PROVIDER_ERROR","message":"The AI provider could not complete the request."}'

    if not answer:
        # Do not hide provider failures behind the generic workspace fallback.
        # The frontend needs the real provider diagnosis (config/auth/model/quota/network)
        # so operators can fix the actual production failure.
        if provider_error:
            try:
                error_info = __import__("json").loads(provider_error)
            except Exception:
                error_info = {"code": "AI_PROVIDER_ERROR", "message": str(provider_error)}
            code = str(error_info.get("code") or "AI_PROVIDER_ERROR")
            provider_message = str(error_info.get("message") or "The AI provider could not complete the request.")
            detail = str(error_info.get("detail") or "").strip()
            status_code = error_info.get("status")
            answer = provider_message
            if detail and code not in {"AI_CONFIG_ERROR"}:
                answer = f"{answer} ({detail[:500]})"
        else:
            answer = _local_fallback(context, message)

    # Persistence is best-effort. A serializer/model/schema mismatch must not
    # turn a successful provider response into the preparation error seen by UI.
    assistant_id = None
    conversation_payload = None
    persistence = "stateless"
    try:
        if conversation is None:
            conversation = AIConversation.objects.create(
                owner=request.user,
                project_id=project_id or None,
                title=str(request.data.get("title") or "Developer OS Intelligence")[:200],
            )
        AIMessage.objects.create(conversation=conversation, role="user", content=message, context=context)
        assistant = AIMessage.objects.create(conversation=conversation, role="assistant", content=answer, context=context)
        assistant_id = assistant.id
        conversation_payload = AIConversationSerializer(conversation).data
        persistence = "active"
    except Exception:
        logger.exception("AI persistence failed; returning stateless provider response")
        conversation_payload = {"id": getattr(conversation, "id", None), "title": getattr(conversation, "title", "Developer OS Intelligence")}

    # Commit metering only after a response has been produced; failures remain non-fatal.
    try:
        from .views import _consume_usage
        allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 1)
        if not allowed:
            usage.update({"used": used, "limit": limit, "plan": plan, "metering": "active"})
        else:
            usage.update({"used": used, "limit": limit, "plan": plan, "metering": "active"})
    except Exception:
        logger.exception("AI usage commit failed; response remains valid")

    payload = {
        "conversation": conversation_payload or {"id": None, "title": "Developer OS Intelligence"},
        "message": {"id": assistant_id, "role": "assistant", "content": answer, "context": context},
        "context": context,
        "mode": "provider" if provider_error is None else "fallback",
        "persistence": persistence,
        "usage": usage,
    }
    if provider_error:
        payload["provider_status"] = provider_error
    return Response(payload, status=status.HTTP_200_OK)

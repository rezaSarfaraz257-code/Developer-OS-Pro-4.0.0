"""Developer OS Intelligence Engine.

Provider-agnostic orchestration layer for coding intelligence.  The engine keeps
provider credentials server-side, builds bounded project context, supports the
OpenAI Responses API, and exposes structured coding actions without allowing
the model to mutate a workspace directly.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from django.db import DatabaseError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AIConversation, AIMessage, Project
from .serializers import AIConversationSerializer, AIMessageSerializer

logger = logging.getLogger("developer_os.ai")

MAX_CONTEXT_CHARS = int(os.getenv("AI_MAX_CONTEXT_CHARS", "60000"))
MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY_MESSAGES", "16"))
AI_TIMEOUT = (5, int(os.getenv("AI_TIMEOUT_SECONDS", "45")))


def _provider_config() -> dict[str, str]:
    key = os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY") or ""
    base = (os.getenv("AI_API_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("AI_MODEL") or "gpt-5"
    protocol = (os.getenv("AI_API_PROTOCOL") or "responses").strip().lower()
    return {"key": key, "base": base, "model": model, "protocol": protocol}


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    """Bound context before it becomes an expensive model input."""
    raw = json.dumps(context, default=str, ensure_ascii=False)
    if len(raw) <= MAX_CONTEXT_CHARS:
        return context
    # Preserve high-value metadata and truncate large text-bearing collections.
    result = dict(context)
    for key in ("snippets", "notes", "tasks", "activities", "projects"):
        value = result.get(key)
        if isinstance(value, list):
            result[key] = value[:50]
    raw = json.dumps(result, default=str, ensure_ascii=False)
    if len(raw) > MAX_CONTEXT_CHARS:
        result["context_truncated"] = True
        result["_context_note"] = "Large workspace context was bounded by the server."
        while len(json.dumps(result, default=str, ensure_ascii=False)) > MAX_CONTEXT_CHARS and result:
            for key in ("snippets", "notes", "tasks", "activities"):
                value = result.get(key)
                if isinstance(value, list) and value:
                    result[key] = value[:-max(1, len(value)//5)]
                    break
            else:
                break
    return result


def _instructions(action: str | None = None) -> str:
    mode = f" The requested action is: {action}." if action else ""
    return (
        "You are Developer OS Intelligence, a senior software-engineering copilot "
        "embedded inside an IDE and project operating system."
        " Be technically precise, practical, and honest about uncertainty."
        " Treat workspace context as authoritative only for facts it actually contains."
        " Never invent files, APIs, dependencies, errors, test results, or project state."
        " When code is supplied, reason about the actual code rather than giving generic advice."
        " Prefer a concise diagnosis followed by concrete implementation steps."
        " For debugging: identify symptoms, likely root cause, evidence, fix, and verification."
        " For code review: prioritize correctness, security, data loss, concurrency, performance, and maintainability."
        " For architecture: state trade-offs and migration risk."
        " For generated code: make it directly usable and preserve the project's existing conventions."
        " Do not claim that a change was applied or tests passed unless the platform explicitly reports that."
        " You may propose patches, but the host application must ask the user before applying them."
        + mode
    )


def _build_input(message: str, context: dict[str, Any], history: list[dict[str, str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in history[-MAX_HISTORY_MESSAGES:]:
        if item.get("role") in {"user", "assistant"} and item.get("content"):
            items.append({"role": item["role"], "content": item["content"]})
    items.append({
        "role": "user",
        "content": (
            "PROJECT CONTEXT (JSON):\n"
            + json.dumps(_compact_context(context), default=str, ensure_ascii=False)
            + "\n\nCURRENT REQUEST:\n"
            + message
        ),
    })
    return items


def _extract_responses_text(payload: dict[str, Any]) -> str:
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    chunks: list[str] = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            value = content.get("text")
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks).strip()


def _call_provider(message: str, context: dict[str, Any], history: list[dict[str, str]], action: str | None = None) -> tuple[str, str | None]:
    cfg = _provider_config()
    if not cfg["key"]:
        return "", "AI provider is not configured. Set OPENAI_API_KEY on the backend."

    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
    input_items = _build_input(message, context, history)
    try:
        if cfg["protocol"] == "chat":
            endpoint = cfg["base"] if cfg["base"].endswith("/chat/completions") else f"{cfg['base']}/chat/completions"
            payload = {
                "model": cfg["model"],
                "temperature": 0.15,
                "messages": [{"role": "system", "content": _instructions(action)}] + input_items,
            }
            response = requests.post(endpoint, headers=headers, json=payload, timeout=AI_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            answer = str((data.get("choices") or [{}])[0].get("message", {}).get("content", "")).strip()
        else:
            endpoint = cfg["base"] if cfg["base"].endswith("/responses") else f"{cfg['base']}/responses"
            payload = {
                "model": cfg["model"],
                "instructions": _instructions(action),
                "input": input_items,
                "temperature": 0.15,
            }
            response = requests.post(endpoint, headers=headers, json=payload, timeout=AI_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            answer = _extract_responses_text(data)
        if not answer:
            return "", "AI provider returned an empty response."
        return answer, None
    except requests.Timeout:
        logger.warning("AI provider timeout")
        return "", "AI provider timed out. Try again."
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 502
        logger.warning("AI provider HTTP error status=%s", code)
        return "", f"AI provider rejected the request ({code})."
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        logger.exception("AI provider request failed")
        return "", "AI provider is temporarily unavailable."


def _local_fallback(context: dict[str, Any], message: str) -> str:
    tasks = context.get("tasks") or []
    blocked = [t for t in tasks if t.get("status") == "blocked"]
    urgent = [t for t in tasks if t.get("priority") == "urgent" and t.get("status") != "done"]
    return (
        "AI provider is not connected, so no generative answer was produced. "
        f"Workspace index: {len(context.get('projects') or [])} project(s), "
        f"{len(tasks)} task(s), {len(context.get('notes') or [])} note(s), "
        f"{len(context.get('snippets') or [])} snippet(s); "
        f"{len(blocked)} blocked and {len(urgent)} urgent task(s). "
        "Configure OPENAI_API_KEY to enable Developer OS Intelligence."
    )


def _conversation(request, project_id=None, conversation_id=None):
    from .views import _project_access
    if conversation_id:
        return get_object_or_404(AIConversation, pk=conversation_id, owner=request.user)
    if project_id:
        project = get_object_or_404(Project, pk=project_id)
        if not _project_access(project, request.user):
            return None
    return AIConversation.objects.create(
        owner=request.user,
        project_id=project_id,
        title=str(request.data.get("title") or "Developer OS Intelligence")[:200],
    )


def _run(request, message: str, action: str | None = None):
    project_id = request.data.get("project")
    workspace_id = request.data.get("workspace")
    conversation_id = request.data.get("conversation")

    conversation = _conversation(request, project_id, conversation_id)
    if conversation is None:
        return Response({"error": "Forbidden"}, status=403)

    from .views import _workspace_context, _workspace_for_user
    workspace = None
    if workspace_id:
        workspace = _workspace_for_user(workspace_id, request.user)
        if conversation.project_id and workspace.project_id not in (None, conversation.project_id):
            return Response({"error": "Workspace does not belong to the conversation project."}, status=400)

    context = _compact_context(_workspace_context(request.user, conversation.project_id, workspace_id))
    history = list(
        conversation.messages.order_by("-created_at")
        .values("role", "content")[:MAX_HISTORY_MESSAGES]
    )[::-1]

    answer, provider_error = _call_provider(message, context, history, action)
    if not answer:
        answer = _local_fallback(context, message)

    try:
        AIMessage.objects.create(conversation=conversation, role="user", content=message, context=context)
        assistant = AIMessage.objects.create(conversation=conversation, role="assistant", content=answer, context=context)
        payload = {
            "conversation": AIConversationSerializer(conversation).data,
            "message": AIMessageSerializer(assistant).data,
            "context": context,
            "mode": "provider" if provider_error is None else "fallback",
        }
    except DatabaseError:
        payload = {
            "conversation": {"id": conversation.id, "title": conversation.title},
            "message": {"id": None, "role": "assistant", "content": answer, "context": context},
            "context": context,
            "mode": "provider" if provider_error is None else "fallback",
        }
    if provider_error:
        payload["provider_status"] = provider_error
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat_api(request):
    message = str(request.data.get("message") or "").strip()
    if not message:
        return Response({"error": "message is required"}, status=400)
    if len(message) > 12000:
        return Response({"error": "message too long"}, status=400)

    # Validate project/workspace/conversation before charging quota.
    from .views import _consume_usage
    from .views import _consume_usage
    allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 0)
    if not allowed:
        return Response({"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)

    response = _run(request, message)
    if response.status_code >= 400:
        return response

    # Charge only a validated AI operation. Keep this server-side so clients
    # cannot choose their own usage increment.
    allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 1)
    if not allowed:
        return Response({"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)
    response.data["usage"] = {"used": used, "limit": limit, "plan": plan}
    return response


ACTION_INSTRUCTIONS = {
    "review": "Perform a senior-level code review. Group findings by severity and give exact fixes.",
    "tests": "Design focused automated tests for the supplied code and context. Include edge cases and expected assertions.",
    "debug": "Debug the supplied issue. Separate observed evidence from hypotheses and give the smallest safe fix plus verification.",
    "plan": "Create an implementation plan with dependencies, files likely affected, risks, and a verification checklist.",
    "explain": "Explain the supplied code or architecture from high level to implementation detail, using the actual context.",
}


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([AssistantRateThrottle])
def ai_actions_api(request):
    action = str(request.data.get("action") or "").strip().lower()
    user_input = str(request.data.get("input") or "").strip()
    if action not in ACTION_INSTRUCTIONS:
        return Response({"error": "Unsupported AI action."}, status=400)
    if not user_input:
        user_input = "Use the selected workspace context and perform the requested action."
    if len(user_input) > 12000:
        return Response({"error": "input too long"}, status=400)

    allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 0)
    if not allowed:
        return Response({"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)

    response = _run(request, user_input, ACTION_INSTRUCTIONS[action])
    if response.status_code >= 400:
        return response
    allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 1)
    if not allowed:
        return Response({"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)
    response.data["usage"] = {"used": used, "limit": limit, "plan": plan}
    response.data["action"] = action
    return response

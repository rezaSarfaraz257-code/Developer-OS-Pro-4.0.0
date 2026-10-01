from datetime import timedelta, timezone as dt_timezone
import secrets
from urllib.parse import urlencode, urlparse
import base64
import hashlib
import hmac
import json

from django.contrib.auth.models import User
from django.db import connection, DatabaseError
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes, throttle_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import get_object_or_404

import os
import shlex
import re
import requests

from django.shortcuts import redirect
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    Favorite,
    Resource,
    Tool,
    UserProfile,
    Workflow,
    Project,
    Tag,
    Task,
    Note,
    Activity,
    Snippet,
    GitHubOAuthState,
    GitHubAccount, Organization, OrganizationMembership, Notification, Comment, TaskDependency, ProjectInvite, CodeWorkspace, AIConversation, AIMessage, Subscription, APIKey, BillingEvent, UsageRecord, OrganizationInvite, AuditLog, OrganizationSubscription, BillingInvoice, PaymentAttempt, BillingCredit, ProductEvent, NotificationPreference, SecuritySession,
)

from .serializers import (
    FavoriteSerializer,
    ProjectSerializer,
    ProfileUpdateSerializer,
    ResourceSerializer,
    ToolSerializer,
    WorkflowSerializer,
    TagSerializer,
    TaskSerializer,
    NoteSerializer,
    ActivitySerializer,
    SnippetSerializer,
    GitHubAccountSerializer, OrganizationSerializer, OrganizationMembershipSerializer, NotificationSerializer, CommentSerializer, TaskDependencySerializer, ProjectInviteSerializer, CodeWorkspaceSerializer, AIConversationSerializer, AIMessageSerializer, SubscriptionSerializer, APIKeySerializer,
)

from .mature import queue_email, sha256
from .security import can_access_project, can_manage_project, audit_security_event, require_safe_url


class AuthRateThrottle(AnonRateThrottle):
    scope = "auth"


class AssistantRateThrottle(AnonRateThrottle):
    scope = "assistant"


def github_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
# =========================================================
# CONFIG
# =========================================================

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
GITHUB_OAUTH_SCOPE = os.environ.get("GITHUB_OAUTH_SCOPE", "read:user user:email repo")

GITHUB_OAUTH_REDIRECT = os.environ.get(
    "GITHUB_OAUTH_REDIRECT",
    "http://127.0.0.1:8000/api/github/callback/"
)

FRONTEND_URL = os.environ.get(
    "FRONTEND_URL",
    "http://localhost:5173"
).rstrip("/")

frontend_url_parts = urlparse(FRONTEND_URL)
if frontend_url_parts.scheme not in {"http", "https"} or not frontend_url_parts.netloc:
    raise ValueError("FRONTEND_URL must be an absolute http(s) URL.")

GITHUB_API_URL = "https://api.github.com"
GITHUB_TIMEOUT = (3.05, 15)


def github_service_unavailable():
    return Response(
        {"error": "GitHub is temporarily unavailable. Please try again later."},
        status=status.HTTP_502_BAD_GATEWAY,
    )


# =========================================================
# PROJECTS
# =========================================================

@api_view(["GET"])
@permission_classes([AllowAny])
def health_api(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return Response({
            "status": "ok",
            "database": "ok",
            "service": "developer-os-api",
            "version": os.environ.get("RELEASE_VERSION", "1.0.0"),
        })
    except Exception:
        return Response({"status": "degraded", "database": "unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["GET"])
@permission_classes([AllowAny])
def readiness_api(request):
    """Deployment readiness probe: DB plus mandatory production configuration."""
    checks = {"database": False, "secret_key": False, "allowed_hosts": False, "email": False, "cache": False, "worker": False}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = True
    except Exception:
        pass
    checks["secret_key"] = bool(settings.SECRET_KEY and not settings.SECRET_KEY.startswith("django-insecure-"))
    checks["allowed_hosts"] = bool(settings.ALLOWED_HOSTS and settings.ALLOWED_HOSTS != ["*"])
    checks["email"] = bool(getattr(settings, "EMAIL_HOST", "") or getattr(settings, "EMAIL_BACKEND", "").endswith("console.EmailBackend"))
    try:
        from django.core.cache import cache
        cache.set("readiness", "ok", timeout=15)
        checks["cache"] = cache.get("readiness") == "ok"
    except Exception:
        checks["cache"] = False
    try:
        from .models import BackgroundJob
        checks["worker"] = not BackgroundJob.objects.filter(status="running", locked_at__lt=timezone.now()-timedelta(minutes=10)).exists()
    except Exception:
        checks["worker"] = False
    mandatory = ["database", "secret_key", "allowed_hosts", "email", "cache"]
    ready = all(checks[k] for k in mandatory) if not settings.DEBUG else checks["database"]
    return Response({"status": "ready" if ready else "not_ready", "checks": checks, "version": os.environ.get("RELEASE_VERSION", "1.0.0")}, status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def projects_api(request):

    if request.method == "GET":
        projects = Project.objects.filter(
            Q(owner=request.user) | Q(collaborators=request.user)
        ).distinct().annotate(
            task_count=Count("tasks"),
            completed_task_count=Count("tasks", filter=Q(tasks__status="done")),
        ).order_by("-created_at")

        serializer = ProjectSerializer(projects, many=True)
        return Response(serializer.data)

    if request.method == "POST":
        serializer = ProjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)
            plan, _ = _plan_for(locked_user)
            project_limit = PLAN_LIMITS[plan]["projects"]
            if Project.objects.filter(owner=locked_user).count() >= project_limit:
                return Response({"error": f"Your {plan} plan allows {project_limit} project(s).", "plan": plan, "limit": project_limit}, status=status.HTTP_403_FORBIDDEN)
            project = serializer.save(owner=locked_user)
            Activity.objects.create(actor=locked_user, verb="created a project", message=project.title, related_type="project", related_id=project.id, metadata={"category": project.category})
            ProductEvent.objects.create(user=locked_user, name="project_created", properties={"project_id": project.id, "category": project.category})
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)

@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail_api(request, pk):

    project_obj = get_object_or_404(Project.objects.filter(Q(owner=request.user) | Q(collaborators=request.user)).distinct(), pk=pk)

    if request.method == "GET":
        project_obj = Project.objects.annotate(
            task_count=Count("tasks"),
            completed_task_count=Count("tasks", filter=Q(tasks__status="done")),
        ).get(pk=project_obj.pk)
        serializer = ProjectSerializer(project_obj)
        return Response(serializer.data)

    if request.method in ["PUT", "PATCH"]:
        if project_obj.owner_id != request.user.id:
            return Response({"error": "Only the project owner can edit this project."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ProjectSerializer(
            project_obj,
            data=request.data,
            partial=request.method == "PATCH"
        )

        if serializer.is_valid():
            updated = serializer.save(owner=request.user)
            Activity.objects.create(actor=request.user, verb="updated a project", message=updated.title, related_type="project", related_id=updated.id, metadata={"status": updated.status, "progress": ProjectSerializer(updated).data.get("progress", 0)})
            return Response(ProjectSerializer(updated).data)

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    if request.method == "DELETE":
        if project_obj.owner_id != request.user.id:
            return Response({"error": "Only the project owner can delete this project."}, status=status.HTTP_403_FORBIDDEN)
        title = project_obj.title
        project_obj.delete()
        Activity.objects.create(actor=request.user, verb="deleted a project", message=title, related_type="project", related_id=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


# =========================================================
# SESSION
# =========================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_api(request):
    """Blacklist the presented refresh token so logout also invalidates it."""
    refresh_token = request.data.get("refresh")
    if not isinstance(refresh_token, str) or not refresh_token:
        return Response({"error": "refresh is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        token = RefreshToken(refresh_token)
        if str(token.get("user_id")) != str(request.user.id):
            return Response({"error": "Invalid refresh token."}, status=status.HTTP_400_BAD_REQUEST)
        token.blacklist()
        try:
            SecuritySession.objects.filter(user=request.user, jti=str(token.get("jti")), revoked_at__isnull=True).update(revoked_at=timezone.now())
        except Exception:
            pass
    except TokenError:
        return Response({"error": "Invalid refresh token."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(status=status.HTTP_204_NO_CONTENT)

# =========================================================
# PROFILE
# =========================================================

@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, FormParser, MultiPartParser])
def profile_api(request):
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    def profile_response():
        full_name = profile.full_name or " ".join(
            filter(None, [user.first_name, user.last_name])
        ).strip()
        return Response({
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "email_verified": profile.email_verified,
            "full_name": full_name,
            "avatar_url": (
                request.build_absolute_uri(profile.avatar.url)
                if profile.avatar
                else profile.avatar_url
            ),
            "has_uploaded_avatar": bool(profile.avatar),
            "bio": profile.bio,
            "github": profile.github,
            "linkedin": profile.linkedin,
            "x": profile.x,
            "website": profile.website,
        })

    if request.method == "GET":
        return profile_response()

    serializer = ProfileUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data.copy()
    uploaded_avatar = payload.pop("avatar", None)
    remove_avatar = payload.pop("remove_avatar", False)

    if "email" in payload and payload["email"] and User.objects.exclude(pk=user.pk).filter(
        email__iexact=payload["email"]
    ).exists():
        return Response({"error": "Unable to use this email address."}, status=status.HTTP_400_BAD_REQUEST)

    email_changed = "email" in payload and payload.get("email", "").lower() != (user.email or "").lower()
    if email_changed and payload.get("email"):
        profile.email_verified = False

    user_fields = {"first_name", "last_name", "email"}
    changed_user_fields = [field for field in user_fields if field in payload]
    for field in changed_user_fields:
        setattr(user, field, payload[field])
    if changed_user_fields:
        user.save(update_fields=changed_user_fields)

    profile_fields = {"full_name", "avatar_url", "bio", "github", "linkedin", "x", "website"}
    changed_profile_fields = [field for field in profile_fields if field in payload]
    if email_changed:
        changed_profile_fields.append("email_verified")
    for field in changed_profile_fields:
        setattr(profile, field, payload[field])

    old_avatar_name = profile.avatar.name if profile.avatar else ""
    old_avatar_storage = profile.avatar.storage if profile.avatar else None
    if uploaded_avatar:
        profile.avatar = uploaded_avatar
        changed_profile_fields.append("avatar")
    elif remove_avatar and old_avatar_name:
        profile.avatar = ""
        changed_profile_fields.append("avatar")

    if changed_profile_fields:
        profile.save(update_fields=[*changed_profile_fields, "updated_at"])

    if old_avatar_name and old_avatar_name != profile.avatar.name and old_avatar_storage:
        try:
            old_avatar_storage.delete(old_avatar_name)
        except OSError:
            # The profile update is already committed; a stale media object is
            # harmless and should not turn a successful update into an error.
            pass

    if email_changed and user.email:
        raw = secrets.token_urlsafe(48)
        from .models import EmailVerificationToken
        EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        EmailVerificationToken.objects.create(user=user, token_hash=sha256(raw), expires_at=timezone.now() + timedelta(hours=24))
        url = f"{FRONTEND_URL}/verify-email?token={raw}&uid={user.pk}"
        queue_email("email_verification", user.email, "Verify your updated Developer OS email", f"Verify your new email: {url}", f"<p><a href=\"{url}\">Verify email</a></p>")

    return profile_response()


# =========================================================
# REGISTER
# =========================================================

@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def register_api(request):

    username = (
        request.data.get("username") or ""
    ).strip()

    email = (
        request.data.get("email") or ""
    ).strip()

    password = (
        request.data.get("password") or ""
    )

    first_name = (
        request.data.get("first_name") or ""
    ).strip()

    last_name = (
        request.data.get("last_name") or ""
    ).strip()

    if not username or not email or not password:

        return Response(
            {
                "error": (
                    "username, email and password "
                    "are required."
                )
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        validate_email(email)
        candidate = User(username=username, email=email)
        validate_password(password, user=candidate)
    except ValidationError as error:
        return Response({"error": error.messages}, status=status.HTTP_400_BAD_REQUEST)

    # Keep the same response for every duplicate case so this endpoint cannot
    # be used to enumerate registered usernames or email addresses.
    if User.objects.filter(username__iexact=username).exists() or User.objects.filter(email__iexact=email).exists():
        return Response(
            {"error": "Unable to create an account with these details."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.email_verified = False
            profile.save(update_fields=["email_verified", "updated_at"])
            NotificationPreference.objects.get_or_create(user=user)
            Subscription.objects.get_or_create(user=user, defaults={"plan": "free", "status": "active"})
            ProductEvent.objects.create(user=user, name="account_created", properties={"source": "web"})
            raw = secrets.token_urlsafe(48)
            from .models import EmailVerificationToken
            EmailVerificationToken.objects.create(user=user, token_hash=sha256(raw), expires_at=timezone.now() + timedelta(hours=24))
            verify_url = f"{FRONTEND_URL}/verify-email?token={raw}&uid={user.pk}"
            queue_email("email_verification", user.email, "Verify your Developer OS email", f"Verify your email: {verify_url}", f"<p>Welcome to Developer OS.</p><p><a href=\"{verify_url}\">Verify email</a></p>")
    except IntegrityError:
        return Response(
            {"error": "Unable to create an account with these details."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        status=status.HTTP_201_CREATED
    )


# =========================================================
# FAVORITES
# =========================================================

@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def favorites_api(request):

    if request.method == "GET":

        favorites = Favorite.objects.filter(
            user=request.user
        ).order_by("-created_at")

        serializer = FavoriteSerializer(
            favorites,
            many=True
        )

        return Response(serializer.data)

    if request.method == "DELETE":

        tool_name = (request.data.get("tool_name") or request.query_params.get("tool_name") or "").strip()

        if not tool_name:

            return Response(
                {
                    "error": "tool_name is required."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        deleted_count, _ = Favorite.objects.filter(user=request.user, tool__name__iexact=tool_name).delete()

        if deleted_count == 0:

            return Response(
                {
                    "error": "Favorite not found."
                },
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            status=status.HTTP_204_NO_CONTENT
        )

    tool_name = (request.data.get("tool_name") or "").strip()
    if not tool_name or len(tool_name) > 120:
        return Response({"error": "A valid tool_name is required."}, status=status.HTTP_400_BAD_REQUEST)

    # Favorites reference the staff-managed shared catalog. A regular user
    # must never be able to create or modify global Tool records here.
    tool = Tool.objects.filter(name__iexact=tool_name).first()
    if tool is None:
        return Response({"error": "Tool not found in the catalog."}, status=status.HTTP_404_NOT_FOUND)

    favorite, created = Favorite.objects.get_or_create(user=request.user, tool=tool)
    return Response(
        FavoriteSerializer(favorite).data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


# =========================================================
# RESOURCES
# =========================================================

@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def resources_api(request):

    if request.method == "GET":

        resources = Resource.objects.all().order_by("-created_at")
        query = str(request.query_params.get("q") or "").strip()
        category = str(request.query_params.get("category") or "").strip()
        if query:
            resources = resources.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query) |
                Q(category__icontains=query)
            )
        if category and category.lower() != "all":
            resources = resources.filter(category__iexact=category)

        serializer = ResourceSerializer(resources, many=True)

        return Response(serializer.data)

    if not request.user.is_staff:
        return Response({"error": "Only staff can manage the shared resource catalog."}, status=status.HTTP_403_FORBIDDEN)

    serializer = ResourceSerializer(
        data=request.data
    )

    if serializer.is_valid():

        serializer.save()

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED
        )

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


# =========================================================
# WORKFLOWS
# =========================================================

@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def workflows_api(request):

    if request.method == "GET":

        workflows = Workflow.objects.all().order_by("-created_at")
        query = str(request.query_params.get("q") or "").strip()
        if query:
            workflows = workflows.filter(
                Q(title__icontains=query) |
                Q(summary__icontains=query) |
                Q(level__icontains=query)
            )

        serializer = WorkflowSerializer(workflows, many=True)

        return Response(serializer.data)

    if not request.user.is_staff:
        return Response({"error": "Only staff can manage the shared workflow catalog."}, status=status.HTTP_403_FORBIDDEN)

    serializer = WorkflowSerializer(
        data=request.data
    )

    if serializer.is_valid():

        serializer.save()

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED
        )

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


# =========================================================
# TOOLS
# =========================================================

@api_view(["GET"])
@permission_classes([AllowAny])
def tools_api(request):
    tools = Tool.objects.all().order_by("name")
    query = str(request.query_params.get("q") or "").strip()
    category = str(request.query_params.get("category") or "").strip()
    if query:
        tools = tools.filter(
            Q(name__icontains=query) |
            Q(tag__icontains=query) |
            Q(description__icontains=query) |
            Q(category__icontains=query)
        )
    if category and category.lower() != "all":
        tools = tools.filter(category__iexact=category)

    serializer = ToolSerializer(tools, many=True)

    return Response(serializer.data)

# =========================================================
# GLOBAL SEARCH
# =========================================================

@api_view(["GET"])
@permission_classes([AllowAny])
def global_search_api(request):
    """Search the public developer catalog from one stable endpoint."""
    query = str(request.query_params.get("q") or "").strip()
    category = str(request.query_params.get("category") or "").strip()
    if len(query) > 120:
        return Response({"error": "Search query is too long."}, status=status.HTTP_400_BAD_REQUEST)

    tools = Tool.objects.all().order_by("name")
    resources = Resource.objects.all().order_by("-created_at")
    workflows = Workflow.objects.all().order_by("-created_at")
    if query:
        tools = tools.filter(Q(name__icontains=query) | Q(tag__icontains=query) | Q(description__icontains=query) | Q(category__icontains=query))
        resources = resources.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(category__icontains=query))
        workflows = workflows.filter(Q(title__icontains=query) | Q(summary__icontains=query) | Q(level__icontains=query))
    if category and category.lower() != "all":
        tools = tools.filter(category__iexact=category)
        resources = resources.filter(category__iexact=category)

    return Response({
        "query": query,
        "results": {
            "tools": ToolSerializer(tools[:20], many=True).data,
            "resources": ResourceSerializer(resources[:20], many=True).data,
            "workflows": WorkflowSerializer(workflows[:20], many=True).data,
        },
    })

# =========================================================
# TAGS
# =========================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def tags_api(request):

    if request.method == "GET":

        tags = Tag.objects.all().order_by(
            "name"
        )

        serializer = TagSerializer(
            tags,
            many=True
        )

        return Response(serializer.data)

    if not request.user.is_staff:
        return Response({"error": "Only staff can manage shared tags."}, status=status.HTTP_403_FORBIDDEN)

    serializer = TagSerializer(
        data=request.data
    )

    if serializer.is_valid():

        serializer.save()

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED
        )

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


# =========================================================
# TASKS
# =========================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def tasks_api(request):
    if request.method == "GET":
        qs = Task.objects.filter(Q(project__owner=request.user) | Q(project__collaborators=request.user)).distinct().select_related("project", "assignee").order_by("status", "due_date", "-created_at")
        project_id = request.query_params.get("project")
        status_filter = request.query_params.get("status")
        if project_id:
            qs = qs.filter(project_id=project_id)
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(TaskSerializer(qs, many=True).data)

    project_id = request.data.get("project")
    project_obj = get_object_or_404(Project.objects.filter(Q(owner=request.user) | Q(collaborators=request.user)).distinct(), pk=project_id) if project_id else None
    if project_obj is None:
        return Response({"error": "project is required."}, status=status.HTTP_400_BAD_REQUEST)
    serializer = TaskSerializer(data=request.data)
    if serializer.is_valid():
        task = serializer.save(project=project_obj, assignee=request.user)
        Activity.objects.create(actor=request.user, verb="created a task", message=task.title, related_type="task", related_id=task.id, metadata={"project_id": project_obj.id})
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(["GET", "PATCH", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def task_detail_api(request, pk):
    task = get_object_or_404(Task.objects.filter(Q(project__owner=request.user) | Q(project__collaborators=request.user)).distinct(), pk=pk)
    if request.method == "GET":
        return Response(TaskSerializer(task).data)
    if request.method == "DELETE":
        title = task.title
        project_id = task.project_id
        task.delete()
        Activity.objects.create(actor=request.user, verb="deleted a task", message=title, related_type="task", related_id=pk, metadata={"project_id": project_id})
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = TaskSerializer(task, data=request.data, partial=request.method == "PATCH", context={"request": request})
    if serializer.is_valid():
        updated = serializer.save()
        Activity.objects.create(actor=request.user, verb="updated a task", message=updated.title, related_type="task", related_id=updated.id, metadata={"project_id": updated.project_id, "status": updated.status})
        return Response(TaskSerializer(updated).data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# =========================================================
# NOTES
# =========================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def notes_api(request):

    if request.method == "GET":
        notes = Note.objects.filter(Q(project__owner=request.user) | Q(project__collaborators=request.user)).distinct().order_by("-created_at")

        serializer = NoteSerializer(notes, many=True)
        return Response(serializer.data)

    if request.method == "POST":
        project_id = request.data.get("project")
        if not project_id:
            return Response({"error": "project is required."}, status=status.HTTP_400_BAD_REQUEST)

        project_obj = get_object_or_404(Project.objects.filter(Q(owner=request.user) | Q(collaborators=request.user)).distinct(), pk=project_id)
        serializer = NoteSerializer(data=request.data)
        if serializer.is_valid():
            note = serializer.save(author=request.user, project=project_obj)
            Activity.objects.create(
                actor=request.user, verb="created a note", message=note.title or "Untitled note",
                related_type="note", related_id=note.id, metadata={"project_id": project_obj.id}
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PATCH", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def note_detail_api(request, pk):
    note = get_object_or_404(Note.objects.filter(Q(project__owner=request.user) | Q(project__collaborators=request.user)).distinct(), pk=pk)
    if request.method == "GET":
        return Response(NoteSerializer(note).data)
    if request.method == "DELETE":
        title, project_id = note.title or "Untitled note", note.project_id
        note.delete()
        Activity.objects.create(actor=request.user, verb="deleted a note", message=title, related_type="note", related_id=pk, metadata={"project_id": project_id})
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = NoteSerializer(note, data=request.data, partial=request.method == "PATCH")
    if serializer.is_valid():
        updated = serializer.save()
        Activity.objects.create(actor=request.user, verb="updated a note", message=updated.title or "Untitled note", related_type="note", related_id=updated.id, metadata={"project_id": updated.project_id})
        return Response(NoteSerializer(updated).data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# =========================================================
# ACTIVITY
# =========================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def activity_api(request):
    """Return only the authenticated user's activity, never global history."""
    activities = Activity.objects.filter(actor=request.user).order_by("-created_at")[:200]
    return Response(ActivitySerializer(activities, many=True).data)

# =========================================================
# SNIPPETS
# =========================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def snippets_api(request):

    if request.method == "GET":
        snippets = Snippet.objects.filter(
            author=request.user
        ).order_by("-created_at")

        serializer = SnippetSerializer(
            snippets,
            many=True
        )

        return Response(serializer.data)

    if request.method == "POST":

        project_id = request.data.get("project")

        project_obj = None

        if project_id:
            project_obj = get_object_or_404(
                Project,
                pk=project_id,
                owner=request.user
            )

        serializer = SnippetSerializer(data=request.data)

        if serializer.is_valid():
            serializer.save(
                author=request.user,
                project=project_obj
            )

            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

@api_view(["GET", "PATCH", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def snippet_detail_api(request, pk):
    snippet = get_object_or_404(Snippet, pk=pk, author=request.user)
    if request.method == "GET":
        return Response(SnippetSerializer(snippet).data)
    if request.method == "DELETE":
        snippet.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = SnippetSerializer(snippet, data=request.data, partial=request.method == "PATCH")
    if serializer.is_valid():
        return Response(SnippetSerializer(serializer.save()).data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# =========================================================
# GITHUB AUTHORIZE
# =========================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_authorize(request):

    if not GITHUB_CLIENT_ID:
        return Response(
            {
                "error": "GITHUB_CLIENT_ID is not configured."
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )

    if not GITHUB_CLIENT_SECRET:
        return Response(
            {
                "error": "GITHUB_CLIENT_SECRET is not configured."
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )

    if not settings.GITHUB_TOKEN_ENCRYPTION_KEY:
        return Response(
            {"error": "GitHub token encryption is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    expires_before = timezone.now() - timedelta(minutes=10)
    GitHubOAuthState.objects.filter(user=request.user, created_at__lt=expires_before).delete()
    GitHubOAuthState.objects.filter(user=request.user, used=False).delete()
    GitHubOAuthState.objects.create(
        user=request.user,
        state=state,
        code_verifier=code_verifier,
    )

    github_url = "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_OAUTH_REDIRECT,
        "scope": GITHUB_OAUTH_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "allow_signup": "true",
    })

    # Frontend receives this URL and redirects browser to it.
    return Response({
        "authorization_url": github_url
    })


# =========================================================
# GITHUB CALLBACK
# =========================================================

@api_view(["GET"])
@permission_classes([AllowAny])
def github_callback(request):

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    github_error = request.query_params.get("error")

    if github_error:

        return redirect(f"{FRONTEND_URL}/?github=error")

    if not code or not state:

        return Response(
            {
                "error": "Missing code or state."
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        with transaction.atomic():
            oauth_state = GitHubOAuthState.objects.select_for_update().select_related("user").get(
                state=state,
                used=False,
                created_at__gte=timezone.now() - timedelta(minutes=10),
            )
            # Consume state before the external exchange to prevent replay.
            oauth_state.used = True
            oauth_state.save(update_fields=["used"])
    except GitHubOAuthState.DoesNotExist:
        return Response({"error": "Invalid or expired OAuth state."}, status=status.HTTP_400_BAD_REQUEST)

    # Exchange code for GitHub access token
    try:
        token_response = requests.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_OAUTH_REDIRECT,
                "code_verifier": oauth_state.code_verifier,
            },
            headers={"Accept": "application/json"},
            timeout=GITHUB_TIMEOUT,
        )

    except requests.RequestException:
        return github_service_unavailable()

    if token_response.status_code != 200:

        return Response({"error": "Failed to exchange GitHub code."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        token_data = token_response.json()
    except ValueError:
        return github_service_unavailable()

    access_token = token_data.get(
        "access_token"
    )

    if not access_token:

        return Response({"error": "GitHub did not return an access token."}, status=status.HTTP_400_BAD_REQUEST)

    # -----------------------------------------------------
    # Get GitHub user
    # -----------------------------------------------------

    try:
        user_response = requests.get(
            f"{GITHUB_API_URL}/user",
            headers=github_headers(access_token),
            timeout=GITHUB_TIMEOUT,
        )
    except requests.RequestException:
        return github_service_unavailable()

    if user_response.status_code != 200:

        return Response({"error": "Failed to fetch GitHub user."}, status=status.HTTP_502_BAD_GATEWAY)

    try:
        github_user = user_response.json()
    except ValueError:
        return github_service_unavailable()

    github_id = github_user.get("id")
    github_login = github_user.get("login", "")

    # -----------------------------------------------------
    # Save GitHub account
    # -----------------------------------------------------

    GitHubAccount.objects.update_or_create(
        user=oauth_state.user,
        defaults={
            "github_id": github_id,
            "login": github_login,
            "access_token": access_token,
            "scope": token_data.get("scope", ""),
            "token_type": token_data.get(
                "token_type",
                "bearer"
            ),
        }
    )

    # -----------------------------------------------------
    # Update user profile
    # -----------------------------------------------------

    profile, _ = UserProfile.objects.get_or_create(
        user=oauth_state.user
    )

    github_html_url = github_user.get(
        "html_url",
        ""
    )

    if github_html_url:
        profile.github = github_html_url

    if not profile.avatar_url:
        profile.avatar_url = github_user.get(
            "avatar_url",
            ""
        )

    profile.save()

    # -----------------------------------------------------
    # Create activity
    # -----------------------------------------------------

    Activity.objects.create(
        actor=oauth_state.user,
        verb="connected_github",
        message=f"Connected GitHub account @{github_login}",
        related_type="github_account",
        metadata={
            "github_id": github_id,
            "login": github_login,
        }
    )

    # Redirect back to frontend
    return redirect(f"{FRONTEND_URL}/?github=connected")


# =========================================================
# GITHUB ACCOUNT
# =========================================================

@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def github_account_api(request):

    try:

        github_account = GitHubAccount.objects.get(
            user=request.user
        )

    except GitHubAccount.DoesNotExist:

        return Response({
            "connected": False,
            "account": None,
        })

    if request.method == "GET":

        serializer = GitHubAccountSerializer(
            github_account
        )

        data = serializer.data

        # Never expose access token
        data.pop(
            "access_token",
            None
        )

        return Response({
            "connected": True,
            "account": data,
        })

    # -----------------------------------------------------
    # Disconnect GitHub
    # -----------------------------------------------------

    github_account.delete()

    Activity.objects.create(
        actor=request.user,
        verb="disconnected_github",
        message="Disconnected GitHub account",
        related_type="github_account",
    )

    return Response(
        {
            "message": "GitHub account disconnected successfully."
        }
    )


# =========================================================
# GITHUB REPOSITORIES
# =========================================================

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def github_account_disconnect_api(request):
    deleted, _ = GitHubAccount.objects.filter(user=request.user).delete()
    GitHubOAuthState.objects.filter(user=request.user, used=False).update(used=True)
    return Response(status=status.HTTP_204_NO_CONTENT if deleted else status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_repos_api(request):


    try:

        github_account = GitHubAccount.objects.get(
            user=request.user
        )

    except GitHubAccount.DoesNotExist:

        return Response(
            {
                "error": "GitHub account is not connected."
            },
            status=status.HTTP_404_NOT_FOUND
        )

    if not github_account.access_token:

        return Response(
            {
                "error": "GitHub access token is missing."
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    page = request.query_params.get(
        "page",
        "1"
    )

    per_page = request.query_params.get(
        "per_page",
        "30"
    )

    try:
        page = max(1, int(page))
        per_page = min(
            100,
            max(1, int(per_page))
        )

    except ValueError:

        page = 1
        per_page = 30

    try:
        response = requests.get(
            f"{GITHUB_API_URL}/user/repos",
            headers=github_headers(github_account.access_token),
            params={
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            },
            timeout=GITHUB_TIMEOUT,
        )
    except requests.RequestException:
        return github_service_unavailable()

    if response.status_code == 401:

        return Response(
            {
                "error": "GitHub access token is invalid or expired."
            },
            status=status.HTTP_401_UNAUTHORIZED
        )

    if response.status_code != 200:

        return Response({"error": "Failed to fetch GitHub repositories."}, status=status.HTTP_502_BAD_GATEWAY)

    try:
        repositories = response.json()
    except ValueError:
        return github_service_unavailable()

    formatted_repositories = []

    for repo in repositories:

        formatted_repositories.append({
            "id": repo.get("id"),
            "name": repo.get("name"),
            "full_name": repo.get("full_name"),
            "description": repo.get("description"),
            "html_url": repo.get("html_url"),
            "language": repo.get("language"),
            "private": repo.get("private"),
            "fork": repo.get("fork"),
            "default_branch": repo.get(
                "default_branch"
            ),
            "stars": repo.get(
                "stargazers_count",
                0
            ),
            "forks": repo.get(
                "forks_count",
                0
            ),
            "open_issues": repo.get(
                "open_issues_count",
                0
            ),
            "updated_at": repo.get(
                "updated_at"
            ),
            "created_at": repo.get(
                "created_at"
            ),
            "pushed_at": repo.get(
                "pushed_at"
            ),
            "owner": {
                "login": (
                    repo.get("owner") or {}
                ).get("login"),
                "avatar_url": (
                    repo.get("owner") or {}
                ).get("avatar_url"),
            },
        })

    return Response({
        "count": len(formatted_repositories),
        "page": page,
        "per_page": per_page,
        "repositories": formatted_repositories,
    })


# =========================================================
# GITHUB SYNC ACTIVITY
# =========================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def github_sync_activity(request):

    try:

        github_account = GitHubAccount.objects.get(
            user=request.user
        )

    except GitHubAccount.DoesNotExist:

        return Response(
            {
                "error": "GitHub account is not connected."
            },
            status=status.HTTP_404_NOT_FOUND
        )

    if not github_account.access_token:

        return Response(
            {
                "error": "GitHub access token is missing."
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    # -----------------------------------------------------
    # Get authenticated GitHub user
    # -----------------------------------------------------

    try:
        user_response = requests.get(
            f"{GITHUB_API_URL}/user",
            headers=github_headers(github_account.access_token),
            timeout=GITHUB_TIMEOUT,
        )
    except requests.RequestException:
        return github_service_unavailable()

    if user_response.status_code != 200:

        return Response({"error": "Failed to authenticate with GitHub."}, status=status.HTTP_502_BAD_GATEWAY)

    try:
        github_user = user_response.json()
    except ValueError:
        return github_service_unavailable()

    github_login = github_user.get("login")
    requested_repo = str(request.data.get("repo_full_name") or "").strip()

    # -----------------------------------------------------
    # Get GitHub events
    # -----------------------------------------------------

    try:
        events_response = requests.get(
            f"{GITHUB_API_URL}/users/{github_login}/events",
            headers=github_headers(github_account.access_token),
            params={"per_page": 30},
            timeout=GITHUB_TIMEOUT,
        )
    except requests.RequestException:
        return github_service_unavailable()

    if events_response.status_code != 200:

        return Response({"error": "Failed to fetch GitHub activity."}, status=status.HTTP_502_BAD_GATEWAY)

    try:
        events = events_response.json()
    except ValueError:
        return github_service_unavailable()

    synced = 0

    activities = []

    # -----------------------------------------------------
    # Convert GitHub events -> Developer OS Activity
    # -----------------------------------------------------

    for event in events:

        event_id = event.get(
            "id"
        )

        event_type = event.get(
            "type",
            "GitHubActivity"
        )

        repo = event.get(
            "repo"
        ) or {}

        repo_name = repo.get("name", "")
        if requested_repo and repo.get("full_name", repo_name) != requested_repo:
            continue

        message = f"{event_type} in {repo_name}" if repo_name else event_type

        # Avoid duplicate activity using metadata event_id
        already_exists = Activity.objects.filter(
            actor=request.user,
            related_type="github_event",
            metadata__github_event_id=event_id,
        ).exists()

        if already_exists:
            continue

        activity = Activity.objects.create(
            actor=request.user,
            verb=event_type,
            message=message,
            related_type="github_event",
            metadata={
                "github_event_id": event_id,
                "repo": repo_name,
                "event_type": event_type,
                "created_at": event.get(
                    "created_at"
                ),
            }
        )

        activities.append(
            ActivitySerializer(
                activity
            ).data
        )

        synced += 1

    # -----------------------------------------------------
    # Final sync activity
    # -----------------------------------------------------

    Activity.objects.create(
        actor=request.user,
        verb="synced_github_activity",
        message=f"Synced {synced} GitHub activities",
        related_type="github_account",
        metadata={
            "github_login": github_login,
            "synced_count": synced,
        }
    )

    return Response({
        "success": True,
        "github_user": github_login,
        "synced": synced,
        "activities": activities,
    })

# =========================================================
# WORKSPACE INTELLIGENCE + COLLABORATION
# =========================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def workspace_summary_api(request):
    user = request.user
    projects = Project.objects.filter(owner=user).annotate(
        task_count=Count("tasks"),
        completed_task_count=Count("tasks", filter=Q(tasks__status="done")),
    )
    tasks = Task.objects.filter(project__owner=user)
    total = tasks.count()
    done = tasks.filter(status="done").count()
    blocked = tasks.filter(status="blocked").count()
    urgent = tasks.filter(priority="urgent").exclude(status="done").count()
    active = tasks.exclude(status="done").count()
    completion = round(done * 100 / total) if total else 0
    overdue = tasks.filter(due_date__lt=timezone.localdate()).exclude(status="done").count()
    notes = Note.objects.filter(author=user).count()
    snippets = Snippet.objects.filter(author=user).count()
    recent_activity = Activity.objects.filter(actor=user).order_by("-created_at")[:12]
    return Response({
        "projects": projects.count(), "active_tasks": active, "done_tasks": done,
        "blocked_tasks": blocked, "urgent_tasks": urgent, "overdue_tasks": overdue,
        "completion": completion, "notes": notes, "snippets": snippets,
        "project_completion": [
            {"id": p.id, "title": p.title, "progress": round(p.completed_task_count * 100 / p.task_count) if p.task_count else 0,
             "tasks": p.task_count, "status": p.status, "deadline": p.deadline}
            for p in projects.order_by("-created_at")[:10]
        ],
        "recent_activity": ActivitySerializer(recent_activity, many=True).data,
    })


def _project_access(project, user):
    return can_access_project(project, user)


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def project_collaborators_api(request, pk):
    project = get_object_or_404(Project.objects.filter(Q(owner=request.user) | Q(collaborators=request.user)).distinct(), pk=pk)

    if request.method == "GET":
        members = [project.owner, *project.collaborators.all()]
        seen = set()
        data = []
        for member in members:
            if member.id in seen:
                continue
            seen.add(member.id)
            data.append({
                "id": member.id,
                "username": member.username,
                "email": member.email,
                "full_name": " ".join(filter(None, [member.first_name, member.last_name])).strip() or member.username,
                "role": "owner" if member.id == project.owner_id else "member",
            })
        return Response(data)

    if project.owner_id != request.user.id:
        return Response({"error": "Only the project owner can change collaborators."}, status=status.HTTP_403_FORBIDDEN)

    identifier = str(request.data.get("username") or request.data.get("email") or "").strip()
    if not identifier:
        return Response({"error": "username or email is required."}, status=status.HTTP_400_BAD_REQUEST)
    member = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()
    if not member:
        return Response({"error": "No registered user matches that username or email."}, status=status.HTTP_404_NOT_FOUND)
    if member.id == request.user.id:
        return Response({"error": "The project owner is already a member."}, status=status.HTTP_400_BAD_REQUEST)

    if request.method == "POST":
        project.collaborators.add(member)
        Activity.objects.create(actor=request.user, verb="added a collaborator", message=f"{member.username} → {project.title}", related_type="project", related_id=project.id)
        return Response({"id": member.id, "username": member.username, "email": member.email, "role": "member"}, status=status.HTTP_201_CREATED)

    project.collaborators.remove(member)
    Activity.objects.create(actor=request.user, verb="removed a collaborator", message=f"{member.username} ← {project.title}", related_type="project", related_id=project.id)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([AssistantRateThrottle])
def assistant_api(request):
    prompt = str(request.data.get("message") or "").strip()
    if not prompt:
        return Response({"error": "message is required."}, status=status.HTTP_400_BAD_REQUEST)
    if len(prompt) > 6000:
        return Response({"error": "message must be 6000 characters or fewer."}, status=status.HTTP_400_BAD_REQUEST)

    tasks = Task.objects.filter(project__owner=request.user)
    projects = Project.objects.filter(owner=request.user)
    total = tasks.count(); done = tasks.filter(status="done").count()
    blocked = tasks.filter(status="blocked").count()
    urgent = tasks.filter(priority="urgent").exclude(status="done").count()
    overdue = tasks.filter(due_date__lt=timezone.localdate()).exclude(status="done").count()
    context = {
        "projects": projects.count(), "tasks": total, "completed": done,
        "completion": round(done * 100 / total) if total else 0,
        "blocked": blocked, "urgent": urgent, "overdue": overdue,
        "recent_projects": list(projects.order_by("-created_at").values_list("title", flat=True)[:6]),
    }

    # Optional OpenAI-compatible provider. The app remains functional without it
    # by using deterministic workspace intelligence rather than fake AI text.
    provider_url = os.environ.get("AI_API_URL", "").rstrip("/")
    provider_key = os.environ.get("AI_API_KEY", "")
    provider_model = os.environ.get("AI_MODEL", "")
    if provider_url and provider_key and provider_model:
        try:
            upstream = requests.post(
                f"{provider_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider_key}", "Content-Type": "application/json"},
                json={"model": provider_model, "temperature": 0.2, "messages": [
                    {"role": "system", "content": "You are DeveloperOS, a concise software project copilot. Use only the supplied workspace context. Give practical next actions and never invent project facts."},
                    {"role": "user", "content": f"Workspace context: {context}\nUser request: {prompt}"},
                ]}, timeout=25,
            )
            upstream.raise_for_status()
            content = upstream.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if content:
                return Response({"mode": "provider", "answer": content, "context": context})
        except (requests.RequestException, ValueError, IndexError, KeyError):
            pass

    if blocked or overdue:
        issues = []
        if blocked: issues.append(f"{blocked} blocked")
        if overdue: issues.append(f"{overdue} overdue")
        answer = f"Your workspace has {', '.join(issues)} item(s). Clear those first, then move the highest-priority active task forward."
    elif urgent:
        answer = f"You have {urgent} urgent active task(s). Finish or rescope the smallest urgent item first; your current completion is {context['completion']}%."
    elif total:
        answer = f"Your workspace is {context['completion']}% complete across {total} tasks. Keep the active queue small and define the next concrete milestone for {context['recent_projects'][0] if context['recent_projects'] else 'your current project'}."
    else:
        answer = "Your workspace is ready. Create a project and a few concrete tasks so DeveloperOS can generate useful delivery signals."
    return Response({"mode": "local", "answer": answer, "context": context})



@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([AssistantRateThrottle])
def ai_actions_api(request):
    """Structured AI engineering actions with a deterministic safe fallback.

    Actions are intentionally constrained to workspace context; no arbitrary
    tool execution is performed by this endpoint. A configured OpenAI-compatible
    provider can supply richer generation, while local mode remains useful and
    honest when no provider is configured.
    """
    action = str(request.data.get("action") or "").strip().lower()
    user_input = str(request.data.get("input") or request.data.get("message") or "").strip()
    project_id = request.data.get("project")
    workspace_id = request.data.get("workspace")
    allowed_actions = {"review", "tests", "explain", "plan", "debug"}
    if action not in allowed_actions:
        return Response({"error": "Unsupported AI action.", "actions": sorted(allowed_actions)}, status=400)
    if not user_input:
        return Response({"error": "input is required."}, status=400)
    if len(user_input) > 12000:
        return Response({"error": "input must be 12,000 characters or fewer."}, status=400)

    allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 1)
    if not allowed:
        return Response(
            {"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit},
            status=429,
        )

    project = None
    if project_id:
        project = get_object_or_404(Project, pk=project_id)
        if not _project_access(project, request.user):
            return Response({"error": "Forbidden"}, status=403)

    if workspace_id:
        workspace = _workspace_for_user(workspace_id, request.user)
        if project and workspace.project_id not in (None, project.id):
            return Response({"error": "Workspace does not belong to the selected project."}, status=400)
    context = _workspace_context(request.user, project.id if project else None, workspace_id)
    prompts = {
        "review": "Review the supplied code or engineering text for correctness, security, maintainability and likely bugs. Separate confirmed issues from suggestions.",
        "tests": "Design focused automated tests for the supplied code. Prefer executable test cases and edge cases over generic advice.",
        "explain": "Explain the supplied code or engineering problem clearly, including data flow, assumptions and failure modes.",
        "plan": "Turn the supplied requirement into a practical implementation plan with small ordered steps, acceptance criteria and verification.",
        "debug": "Debug the supplied error or code. Identify the most likely root cause, evidence to inspect, and the smallest safe fix.",
    }
    system = (
        "You are Developer OS Intelligence, a senior software engineering copilot. "
        "Use only supplied workspace context for project facts. Never claim a test passed "
        "unless the supplied evidence says it passed. Be precise and security-conscious. "
        + prompts[action]
    )
    provider_url = os.environ.get("AI_API_URL", "").rstrip("/")
    provider_key = os.environ.get("AI_API_KEY", "")
    provider_model = os.environ.get("AI_MODEL", "")
    answer = ""
    mode = "local"
    if provider_url and provider_key and provider_model:
        try:
            upstream = requests.post(
                f"{provider_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider_key}", "Content-Type": "application/json"},
                json={
                    "model": provider_model,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps({"workspace": context, "input": user_input}, default=str)},
                    ],
                },
                timeout=35,
            )
            upstream.raise_for_status()
            answer = str(upstream.json().get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
            if answer:
                mode = "provider"
        except (requests.RequestException, ValueError, IndexError, KeyError, TypeError):
            answer = ""

    if not answer:
        project_count = len(context["projects"])
        task_count = len(context["tasks"])
        if action == "plan":
            answer = (
                f"Plan: 1) define acceptance criteria, 2) split the work into small tasks, "
                f"3) implement the smallest vertical slice, 4) run focused tests, 5) verify deployment. "
                f"Current context contains {project_count} project(s) and {task_count} task(s)."
            )
        elif action == "tests":
            answer = (
                "Test matrix: happy path, empty input, invalid input, authorization boundary, "
                "concurrent/repeated request, persistence/rollback, and external-service failure. "
                "Add a regression test for every confirmed bug before changing behavior."
            )
        elif action == "review":
            answer = (
                "Local review mode cannot prove code correctness without executing the code. "
                "Use the workspace context to identify affected projects/tasks, then run the "
                "repository's lint, unit, integration and security gates before accepting changes."
            )
        elif action == "debug":
            answer = (
                "Debug sequence: reproduce the exact request, inspect server logs and response "
                "body, verify route/auth/CORS configuration, isolate the failing dependency, "
                "apply the smallest change, then rerun the regression and integration tests."
            )
        else:
            answer = (
                "Explain mode: start from the request boundary, trace authentication and data "
                "validation, follow persistence and external calls, then inspect the response. "
                f"The current workspace exposes {project_count} project(s) and {task_count} task(s)."
            )

    return Response({
        "action": action,
        "mode": mode,
        "answer": answer,
        "usage": {"used": used + 1, "limit": limit, "plan": plan},
        "context": context,
    })


# =========================================================
# PLATFORM UPGRADE — SEARCH / COLLAB / IDE / AI / SAAS
# =========================================================

def _visible_projects(user):
    return Project.objects.filter(Q(owner=user) | Q(collaborators=user)).distinct()


def _workspace_context(user, project_id=None, workspace_id=None):
    projects = _visible_projects(user)
    if project_id:
        projects = projects.filter(pk=project_id)
    project_rows = list(projects.order_by("-uploaded_at").values(
        "id", "title", "description", "status", "priority", "category", "deadline", "repository_url", "stack"
    )[:20])
    project_ids = [p["id"] for p in project_rows]
    tasks = Task.objects.filter(project_id__in=project_ids).select_related("project", "assignee").order_by("status", "priority", "due_date")[:80]
    notes = Note.objects.filter(project_id__in=project_ids).order_by("-updated_at")[:40]
    snippets = Snippet.objects.filter(Q(author=user) | Q(project_id__in=project_ids)).order_by("-updated_at")[:40]
    workspaces = _workspace_access_queryset(user)
    if project_ids:
        workspaces = workspaces.filter(Q(project_id__in=project_ids) | Q(project__isnull=True))
    if workspace_id:
        workspaces = workspaces.filter(pk=workspace_id)
    workspace_rows = []
    for ws in workspaces.order_by("-updated_at")[:3]:
        files = {}
        for name, content in (ws.files or {}).items():
            if isinstance(name, str) and isinstance(content, str):
                files[name] = content[:8000]
        workspace_rows.append({
            "id": ws.id, "name": ws.name, "project": ws.project_id, "language": ws.language,
            "framework": ws.framework, "runtime": ws.runtime, "active_file": ws.active_file, "files": files,
        })
    return {
        "projects": project_rows,
        "tasks": [
            {"id": t.id, "project": t.project.title if t.project else None, "title": t.title, "status": t.status,
             "priority": t.priority, "due_date": t.due_date.isoformat() if t.due_date else None,
             "assignee": t.assignee.username if t.assignee else None}
            for t in tasks
        ],
        "notes": [{"id": n.id, "project": n.project_id, "title": n.title, "content": n.content[:1500]} for n in notes],
        "snippets": [{"id": s.id, "project": s.project_id, "title": s.title, "language": s.language, "code": s.code[:3000]} for s in snippets],
        "workspaces": workspace_rows,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def platform_search_api(request):
    q = str(request.query_params.get("q") or "").strip()
    if not q:
        return Response({"query": "", "results": []})
    if len(q) > 160:
        return Response({"error": "Query too long."}, status=400)
    projects = _visible_projects(request.user).filter(
        Q(title__icontains=q) | Q(description__icontains=q) | Q(category__icontains=q)
    )[:15]
    tasks = Task.objects.filter(
        Q(project__in=_visible_projects(request.user)) &
        (Q(title__icontains=q) | Q(description__icontains=q) | Q(tags__icontains=q))
    ).select_related("project")[:20]
    notes = Note.objects.filter(
        Q(project__in=_visible_projects(request.user)) &
        (Q(title__icontains=q) | Q(content__icontains=q))
    ).select_related("project")[:15]
    snippets = Snippet.objects.filter(
        Q(author=request.user) | Q(project__in=_visible_projects(request.user))
    ).filter(Q(title__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q))[:15]
    tools = Tool.objects.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(tag__icontains=q))[:10]
    resources = Resource.objects.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(category__icontains=q))[:10]
    results = (
        [{"type":"project","id":p.id,"title":p.title,"subtitle":p.description[:120],"route":f"/projects/{p.id}"} for p in projects] +
        [{"type":"task","id":t.id,"title":t.title,"subtitle":f"{t.project.title} · {t.status}","route":f"/projects/{t.project_id}?tab=tasks"} for t in tasks] +
        [{"type":"note","id":n.id,"title":n.title or "Untitled note","subtitle":(n.content or "")[:120],"route":f"/projects/{n.project_id}?tab=notes"} for n in notes] +
        [{"type":"snippet","id":s.id,"title":s.title,"subtitle":s.language,"route":"/ide"} for s in snippets] +
        [{"type":"tool","id":t.id,"title":t.name,"subtitle":t.category,"route":"/explore"} for t in tools] +
        [{"type":"resource","id":r.id,"title":r.title,"subtitle":r.category,"route":"/resources"} for r in resources]
    )
    return Response({"query": q, "results": results[:80]})


@api_view(["GET", "POST", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def notifications_api(request):
    if request.method == "GET":
        qs = Notification.objects.filter(user=request.user).order_by("-created_at")[:100]
        unread = Notification.objects.filter(user=request.user, read=False).count()
        return Response({"unread": unread, "items": NotificationSerializer(qs, many=True).data})
    if request.method == "POST":
        # Internal-safe endpoint for creating user notifications; clients can only target themselves.
        payload = {"kind": request.data.get("kind","system"), "title": request.data.get("title","Notification"),
                   "body": request.data.get("body",""), "link": request.data.get("link","")}
        n = Notification.objects.create(user=request.user, **payload)
        return Response(NotificationSerializer(n).data, status=201)
    if request.method == "PATCH":
        ids = request.data.get("ids")
        qs = Notification.objects.filter(user=request.user)
        if ids:
            qs = qs.filter(id__in=ids)
        qs.update(read=True)
        return Response({"ok": True})
    Notification.objects.filter(user=request.user, id=request.data.get("id")).delete()
    return Response(status=204)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def comments_api(request):
    if request.method == "GET":
        qs = Comment.objects.filter(Q(project__in=_visible_projects(request.user)) | Q(task__project__in=_visible_projects(request.user))).select_related("author").order_by("-created_at")
        if request.query_params.get("project"): qs = qs.filter(project_id=request.query_params["project"])
        if request.query_params.get("task"): qs = qs.filter(task_id=request.query_params["task"])
        return Response(CommentSerializer(qs[:100], many=True).data)
    project_id = request.data.get("project")
    task_id = request.data.get("task")
    if not project_id and not task_id:
        return Response({"error":"project or task is required"}, status=400)
    if task_id:
        task = get_object_or_404(Task.objects.select_related("project"), pk=task_id)
        if not _project_access(task.project, request.user): return Response({"error":"Forbidden"}, status=403)
    if project_id:
        project = get_object_or_404(Project, pk=project_id)
        if not _project_access(project, request.user): return Response({"error":"Forbidden"}, status=403)
    if task_id and project_id and task.project_id != int(project_id):
        return Response({"error":"Project and task targets must belong to the same project."}, status=400)
    serializer = CommentSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    c = serializer.save(author=request.user)
    Activity.objects.create(actor=request.user, verb="commented", message=c.body[:200], related_type="comment", related_id=c.id)
    return Response(CommentSerializer(c).data, status=201)


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def task_dependencies_api(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not task.project or not _project_access(task.project, request.user): return Response({"error":"Forbidden"}, status=403)
    if request.method == "GET":
        deps = TaskDependency.objects.filter(task=task)
        return Response(TaskDependencySerializer(deps, many=True).data)
    if request.method == "DELETE":
        dep = get_object_or_404(TaskDependency, pk=request.data.get("dependency_id"), task=task)
        dep.delete(); return Response(status=204)
    dep_task = get_object_or_404(Task.objects.select_related("project"), pk=request.data.get("depends_on"))
    if not dep_task.project_id or not _project_access(dep_task.project, request.user):
        return Response({"error":"Forbidden"}, status=403)
    if dep_task == task: return Response({"error":"A task cannot depend on itself."}, status=400)
    if dep_task.project_id != task.project_id: return Response({"error":"Dependencies must stay inside the project."}, status=400)
    dep, _ = TaskDependency.objects.get_or_create(task=task, depends_on=dep_task)
    return Response(TaskDependencySerializer(dep).data, status=201)


@api_view(["GET", "POST", "PATCH"])
@permission_classes([IsAuthenticated])
def ide_workspaces_api(request):
    if request.method == "GET":
        qs = _workspace_access_queryset(request.user).order_by("-updated_at")
        return Response(CodeWorkspaceSerializer(qs[:30], many=True).data)
    if request.method == "POST":
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)
            plan, _ = _plan_for(locked_user)
            limit = PLAN_LIMITS[plan]["workspaces"]
            if CodeWorkspace.objects.filter(owner=locked_user).count() >= limit:
                return Response({"error": f"Your {plan} plan allows {limit} workspace(s).", "limit": limit}, status=403)
        payload = request.data.copy()
        project_id = payload.get("project")
        if project_id not in (None, "", 0, "0"):
            project = get_object_or_404(Project, pk=project_id)
            if not _project_access(project, request.user):
                return Response({"error": "You do not have access to this project."}, status=403)
        elif project_id in ("", 0, "0"):
            payload["project"] = None
        payload.setdefault("files", {
            "main.py": "# Developer OS Web IDE\nprint('Hello, Developer OS')\n",
            "README.md": "# Workspace\n\nBuild, test and ship from the Developer OS command center.\n"
        })
        serializer = CodeWorkspaceSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        ws = serializer.save(owner=request.user)
        return Response(CodeWorkspaceSerializer(ws).data, status=201)
    ws = get_object_or_404(CodeWorkspace, pk=request.data.get("id"), owner=request.user)
    if "project" in request.data and request.data.get("project") not in (None, "", 0, "0"):
        project = get_object_or_404(Project, pk=request.data.get("project"))
        if not _project_access(project, request.user):
            return Response({"error": "You do not have access to this project."}, status=403)
    serializer = CodeWorkspaceSerializer(ws, data=request.data, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    return Response(CodeWorkspaceSerializer(serializer.save()).data)


# =========================================================
# WEB IDE — file system, framework provisioning and sandboxed execution
# =========================================================

FRAMEWORK_CATALOG = {
    "react-vite": {"label": "React + Vite", "runtime": "node", "package_manager": "npm", "install": "npm install react react-dom && npm install -D vite @vitejs/plugin-react", "start": "npm run dev -- --host 0.0.0.0"},
    "vue-vite": {"label": "Vue + Vite", "runtime": "node", "package_manager": "npm", "install": "npm install vue && npm install -D vite @vitejs/plugin-vue", "start": "npm run dev -- --host 0.0.0.0"},
    "svelte-vite": {"label": "Svelte + Vite", "runtime": "node", "package_manager": "npm", "install": "npm install svelte && npm install -D vite @sveltejs/vite-plugin-svelte", "start": "npm run dev -- --host 0.0.0.0"},
    "nextjs": {"label": "Next.js", "runtime": "node", "package_manager": "npm", "install": "npm install next react react-dom", "start": "npm run dev -- --hostname 0.0.0.0"},
    "express": {"label": "Express", "runtime": "node", "package_manager": "npm", "install": "npm install express", "start": "node server.js"},
    "nestjs": {"label": "NestJS", "runtime": "node", "package_manager": "npm", "install": "npm install @nestjs/core @nestjs/common reflect-metadata rxjs", "start": "npm run start:dev"},
    "django": {"label": "Django + DRF", "runtime": "python", "package_manager": "pip", "install": "python -m pip install django djangorestframework django-cors-headers", "start": "python manage.py runserver 0.0.0.0:8000"},
    "fastapi": {"label": "FastAPI", "runtime": "python", "package_manager": "pip", "install": "python -m pip install fastapi uvicorn[standard] pydantic", "start": "python -m uvicorn main:app --host 0.0.0.0 --port 8000"},
    "flask": {"label": "Flask", "runtime": "python", "package_manager": "pip", "install": "python -m pip install flask", "start": "flask --app app run --host 0.0.0.0 --port 8000"},
    "litestar": {"label": "Litestar", "runtime": "python", "package_manager": "pip", "install": "python -m pip install litestar uvicorn", "start": "uvicorn app:app --host 0.0.0.0 --port 8000"},
    "streamlit": {"label": "Streamlit", "runtime": "python", "package_manager": "pip", "install": "python -m pip install streamlit", "start": "streamlit run app.py --server.address 0.0.0.0"},
    "httpx": {"label": "Python HTTP stack", "runtime": "python", "package_manager": "pip", "install": "python -m pip install httpx", "start": "python main.py"},
    "django-ninja": {"label": "Django Ninja", "runtime": "python", "package_manager": "pip", "install": "python -m pip install django django-ninja", "start": "python manage.py runserver 0.0.0.0:8000"},
}

RUNNER_URL = os.environ.get("IDE_RUNNER_URL", "http://runner:8080").rstrip("/")
RUNNER_TOKEN = os.environ.get("IDE_RUNNER_TOKEN", "")

def _runner_request(method, path, payload, timeout=30):
    if not RUNNER_TOKEN:
        return None, {"error": "IDE runner is not configured. Set IDE_RUNNER_TOKEN and start the runner service."}
    try:
        response = requests.request(
            method, f"{RUNNER_URL}{path}", json=payload,
            headers={"Authorization": f"Bearer {RUNNER_TOKEN}"},
            timeout=timeout,
        )
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            return None, data
        return data, None
    except requests.RequestException as exc:
        return None, {"error": f"IDE runner unavailable: {exc.__class__.__name__}"}

def _workspace_access_queryset(user):
    return CodeWorkspace.objects.select_related("project").filter(
        Q(owner=user) | Q(project__owner=user) | Q(project__collaborators=user)
    ).distinct()

def _workspace_for_user(pk, user, *, for_update=False):
    qs = _workspace_access_queryset(user)
    if for_update:
        qs = qs.select_for_update()
    return get_object_or_404(qs, pk=pk)

def _workspace_write_allowed(ws, user):
    # Project collaborators are first-class IDE users. A workspace without a
    # project remains owner-only so private scratch workspaces stay private.
    return bool(ws.owner_id == user.id or (ws.project_id and _project_access(ws.project, user)))

def _workspace_payload(ws):
    if ws.project_id and not can_access_project(ws.project, ws.owner):
        raise PermissionError("workspace project access invariant failed")
    return {"workspace_id": str(ws.id), "files": ws.files, "active_file": ws.active_file}

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ide_frameworks_api(request):
    return Response([
        {"id": key, **value}
        for key, value in FRAMEWORK_CATALOG.items()
    ])

def _safe_ide_path(value):
    path = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not path or len(path) > 500 or any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    if path.startswith(".git/") or "/.git/" in path or path == ".git":
        return None
    return path


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def ide_workspace_files_api(request, pk):
    ws = _workspace_for_user(pk, request.user, for_update=request.method in {"POST", "DELETE"})
    if request.method in {"POST", "DELETE"} and not _workspace_write_allowed(ws, request.user):
        return Response({"error": "You have read-only access to this workspace."}, status=403)
    expected_revision = request.data.get("revision")
    if request.method in {"POST", "DELETE"} and expected_revision not in (None, ""):
        try:
            expected_revision = int(expected_revision)
        except (TypeError, ValueError):
            return Response({"error": "Invalid workspace revision."}, status=400)
        if expected_revision != ws.revision:
            return Response({"error": "Workspace changed elsewhere. Reload before saving.", "code": "stale_workspace", "revision": ws.revision}, status=409)
    files = dict(ws.files or {})
    if request.method == "GET":
        return Response({"files": files, "active_file": ws.active_file, "revision": ws.revision})
    if request.method == "DELETE":
        path = _safe_ide_path(request.data.get("path"))
        if not path:
            return Response({"error": "Invalid path."}, status=400)
        # Directories are virtual (files are stored by path), so deleting a
        # directory removes every descendant. This makes the IDE behave like
        # a real project filesystem without storing unsafe filesystem paths.
        if path in files:
            del files[path]
        else:
            prefix = path.rstrip("/") + "/"
            removed = [key for key in files if key.startswith(prefix)]
            if not removed:
                return Response({"error": "File or directory not found."}, status=404)
            for key in removed:
                del files[key]
        if ws.active_file not in files:
            ws.active_file = next(iter(files), "")
        ws.files = files
        ws.save(update_fields=["files", "active_file", "updated_at"])
        _runner_request("POST", "/sync", _workspace_payload(ws), timeout=30)
        return Response({"files": files, "active_file": ws.active_file, "revision": ws.revision})
    action = str(request.data.get("action") or "write")
    path = _safe_ide_path(request.data.get("path"))
    if action == "rename":
        target = _safe_ide_path(request.data.get("to"))
        if not path or not target:
            return Response({"error": "Valid source and target paths are required."}, status=400)
        if target in files:
            return Response({"error": "Target already exists."}, status=409)
        if path in files:
            files[target] = files.pop(path)
        else:
            prefix = path.rstrip("/") + "/"
            matches = [key for key in files if key.startswith(prefix)]
            if not matches:
                return Response({"error": "Source file or directory not found."}, status=404)
            target_prefix = target.rstrip("/") + "/"
            if any(key == target or key.startswith(target_prefix) for key in files):
                return Response({"error": "Target already exists."}, status=409)
            moved = {}
            for key in matches:
                moved[target_prefix + key[len(prefix):]] = files.pop(key)
            files.update(moved)
        if ws.active_file == path:
            ws.active_file = target
        elif ws.active_file.startswith(path.rstrip("/") + "/"):
            ws.active_file = target.rstrip("/") + ws.active_file[len(path.rstrip("/")): ]
        ws.files = files
        ws.save(update_fields=["files", "active_file", "updated_at"])
        _runner_request("POST", "/sync", _workspace_payload(ws), timeout=30)
        return Response({"files": files, "active_file": ws.active_file, "revision": ws.revision})
    content = request.data.get("content", "")
    if not path or not isinstance(content, str):
        return Response({"error": "Valid path and text content are required."}, status=400)
    files[path] = content
    serializer = CodeWorkspaceSerializer(ws, data={"files": files, "active_file": path}, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    ws = serializer.save()
    _runner_request("POST", "/sync", _workspace_payload(ws), timeout=30)
    return Response({"files": ws.files, "active_file": ws.active_file, "revision": ws.revision})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ide_workspace_sync_api(request, pk):
    ws = _workspace_for_user(pk, request.user)
    if not _workspace_write_allowed(ws, request.user):
        return Response({"error": "You have read-only access to this workspace."}, status=403)
    data, error = _runner_request("POST", "/sync", _workspace_payload(ws), timeout=30)
    if error:
        return Response(error, status=503)
    return Response(data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ide_install_framework_api(request, pk):
    ws = _workspace_for_user(pk, request.user)
    if not _workspace_write_allowed(ws, request.user):
        return Response({"error": "You have read-only access to this workspace."}, status=403)
    framework = str(request.data.get("framework") or "").lower()
    spec = FRAMEWORK_CATALOG.get(framework)
    if not spec:
        return Response({"error": "Unsupported framework preset."}, status=400)
    installation = FrameworkInstallation.objects.create(
        workspace=ws, framework=framework, package_manager=spec["package_manager"], status="running"
    )
    data, error = _runner_request("POST", "/install", {
        **_workspace_payload(ws), "command": spec["install"], "framework": framework,
        "package_manager": spec["package_manager"],
    }, timeout=240)
    if error:
        installation.status = "failed"; installation.output = json.dumps(error); installation.finished_at = timezone.now(); installation.save()
        return Response(error, status=503)
    runner_files = data.get("files")
    if isinstance(runner_files, dict):
        serializer = CodeWorkspaceSerializer(ws, data={"files": runner_files}, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        ws = serializer.save()
    installation.status = "success" if data.get("exit_code", 1) == 0 else "failed"
    installation.output = (data.get("stdout", "") + "\n" + data.get("stderr", ""))[-30000:]
    installation.finished_at = timezone.now(); installation.save()
    ws.framework = framework
    ws.runtime = spec["runtime"]
    ws.package_manager = spec["package_manager"]
    ws.save(update_fields=["framework", "runtime", "package_manager", "updated_at"])
    return Response({"installation": {"id": installation.id, "status": installation.status, "output": installation.output}, "workspace": CodeWorkspaceSerializer(ws).data})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ide_install_packages_api(request, pk):
    ws = _workspace_for_user(pk, request.user)
    if not _workspace_write_allowed(ws, request.user):
        return Response({"error": "You have read-only access to this workspace."}, status=403)
    manager = str(request.data.get("package_manager") or ws.package_manager or "npm").lower()
    packages = request.data.get("packages") or []
    if isinstance(packages, str):
        packages = [x.strip() for x in packages.split(",") if x.strip()]
    if manager not in {"npm", "pip"} or not isinstance(packages, list) or not packages or len(packages) > 50:
        return Response({"error": "Use npm or pip and provide 1–50 packages."}, status=400)
    safe = re.compile(r"^[A-Za-z0-9_.@/\-<>=!~\[\],]+$")
    if any(not isinstance(pkg, str) or len(pkg) > 160 or not safe.fullmatch(pkg) for pkg in packages):
        return Response({"error": "One or more package names are invalid."}, status=400)
    quoted_packages = " ".join(shlex.quote(pkg) for pkg in packages)
    command = ("npm install " + quoted_packages) if manager == "npm" else ("python -m pip install " + quoted_packages)
    data, error = _runner_request("POST", "/exec", {**_workspace_payload(ws), "command": command}, timeout=240)
    if error:
        return Response(error, status=503)
    if isinstance(data.get("files"), dict):
        serializer = CodeWorkspaceSerializer(ws, data={"files": data["files"], "package_manager": manager}, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        ws = serializer.save()
    return Response({
        "status": "success" if data.get("exit_code") == 0 else "failed",
        "command": command,
        "exit_code": data.get("exit_code"),
        "stdout": data.get("stdout", ""),
        "stderr": data.get("stderr", ""),
        "workspace": CodeWorkspaceSerializer(ws).data,
    })

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ide_diagnostics_api(request, pk):
    """Run a read-only project check and return editor-friendly diagnostics."""
    ws = _workspace_for_user(pk, request.user)
    if not _workspace_write_allowed(ws, request.user):
        return Response({"error": "You have read-only access to this workspace."}, status=403)
    language = str(request.data.get("language") or "").lower()
    path = _safe_ide_path(request.data.get("path") or "")
    if not path:
        return Response({"error": "A valid file path is required."}, status=400)
    _, sync_error = _runner_request("POST", "/sync", _workspace_payload(ws), timeout=30)
    if sync_error:
        return Response(sync_error, status=503)
    if language in {"python", "py"} or path.endswith(".py"):
        command = "python -m py_compile " + shlex.quote(path)
    elif language in {"javascript", "typescript", "javascriptreact", "typescriptreact"} or re.search(r"\.(js|jsx|ts|tsx)$", path):
        command = "npx tsc --noEmit --pretty false 2>/dev/null || npm run build --if-present"
    else:
        return Response({"status": "skipped", "diagnostics": [], "message": "No language checker configured for this file."})
    data, error = _runner_request("POST", "/exec", {**_workspace_payload(ws), "command": command}, timeout=125)
    if error:
        return Response(error, status=503)
    output = "\n".join(x for x in [str(data.get("stdout") or ""), str(data.get("stderr") or "")] if x)
    diagnostics = []
    pattern = re.compile(r"(?P<file>[^:\\n]+?)[(:](?P<line>\d+)(?:[:,](?P<column>\d+))?\)?[: -]+(?P<message>.+)")
    for raw in output.splitlines():
        match = pattern.search(raw.strip())
        if not match:
            continue
        diagnostics.append({"path": _safe_ide_path(match.group("file")) or path, "line": max(1, int(match.group("line") or 1)), "column": max(1, int(match.group("column") or 1)), "severity": "error" if data.get("exit_code") not in (0, None) else "warning", "message": match.group("message")[:1000]})
    if data.get("exit_code") not in (0, None) and not diagnostics:
        diagnostics.append({"path": path, "line": 1, "column": 1, "severity": "error", "message": (output or "Project check failed.")[-1000:]})
    return Response({"status": "success" if data.get("exit_code") == 0 else "failed", "command": command, "diagnostics": diagnostics[:100], "stdout": str(data.get("stdout") or "")[-10000:], "stderr": str(data.get("stderr") or "")[-10000:]})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ide_execute_api(request, pk):
    ws = _workspace_for_user(pk, request.user)
    if not _workspace_write_allowed(ws, request.user):
        return Response({"error": "You have read-only access to this workspace."}, status=403)
    allowed, used, limit, plan = _consume_usage(request.user, "ide_runs_month", 1)
    if not allowed:
        return Response({"error": "Monthly IDE execution limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)
    command = str(request.data.get("command") or "").strip()
    if not command or len(command) > 2000:
        return Response({"error": "A command up to 2,000 characters is required."}, status=400)
    execution = IDEExecution.objects.create(workspace=ws, command=command, status="running")
    started = timezone.now()
    data, error = _runner_request("POST", "/exec", {**_workspace_payload(ws), "command": command}, timeout=125)
    duration = int((timezone.now() - started).total_seconds() * 1000)
    if error:
        execution.status = "failed"; execution.stderr = json.dumps(error); execution.duration_ms = duration; execution.finished_at = timezone.now(); execution.save()
        return Response(error, status=503)
    runner_files = data.get("files")
    if isinstance(runner_files, dict):
        serializer = CodeWorkspaceSerializer(ws, data={"files": runner_files}, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        ws = serializer.save()
    execution.exit_code = data.get("exit_code")
    execution.stdout = str(data.get("stdout") or "")[-50000:]
    execution.stderr = str(data.get("stderr") or "")[-50000:]
    execution.duration_ms = duration
    execution.status = "timeout" if execution.exit_code == 124 else ("success" if execution.exit_code == 0 else "failed")
    execution.finished_at = timezone.now()
    execution.save()
    return Response({"id": execution.id, "status": execution.status, "exit_code": execution.exit_code, "stdout": execution.stdout, "stderr": execution.stderr, "duration_ms": duration})


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def api_keys_api(request):
    if request.method == "GET":
        return Response(APIKeySerializer(APIKey.objects.filter(user=request.user).order_by("-created_at"), many=True).data)
    if request.method == "DELETE":
        key = get_object_or_404(APIKey, pk=request.data.get("id"), user=request.user)
        key.revoked_at = timezone.now(); key.save(update_fields=["revoked_at"]); return Response(status=204)
    name = str(request.data.get("name") or "Developer OS CLI").strip()[:100]
    plan, _ = _plan_for(request.user)
    limit = PLAN_LIMITS[plan]["api_keys"]
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=request.user.pk)
        active_keys = APIKey.objects.filter(user=locked_user, revoked_at__isnull=True).count()
        if active_keys >= limit:
            return Response({"error": f"Your {plan} plan allows {limit} active API key(s).", "plan": plan, "limit": limit}, status=403)
        raw = "dos_live_" + secrets.token_urlsafe(32)
        key = APIKey.objects.create(user=locked_user, name=name, prefix=raw[:14], key_hash=hashlib.sha256(raw.encode()).hexdigest())
    return Response({"key": raw, "record": APIKeySerializer(key).data}, status=201)


PLAN_LIMITS = {
    "free": {"ai_messages_month": 50, "ide_runs_month": 30, "api_keys": 2, "org_members": 1, "workspaces": 3, "projects": 5},
    "pro": {"ai_messages_month": 1000, "ide_runs_month": 500, "api_keys": 10, "org_members": 5, "workspaces": 25, "projects": 50},
    "team": {"ai_messages_month": 5000, "ide_runs_month": 2500, "api_keys": 50, "org_members": 50, "workspaces": 100, "projects": 250},
    "enterprise": {"ai_messages_month": 50000, "ide_runs_month": 20000, "api_keys": 200, "org_members": 500, "workspaces": 1000, "projects": 1000},
}

def _subscription_for(user):
    sub, _ = Subscription.objects.get_or_create(user=user)
    return sub

def _plan_for(user):
    sub = _subscription_for(user)
    if sub.status in {"canceled"} and sub.plan != "free":
        return "free", sub
    return sub.plan, sub

def _usage_period():
    now = timezone.now()
    return now.date().replace(day=1)

def _usage_count(user, metric):
    return UsageRecord.objects.filter(user=user, period=_usage_period(), metric=metric).values_list("quantity", flat=True).first() or 0

def _consume_usage(user, metric, amount=1):
    """Atomically consume metered usage so concurrent requests cannot overspend a plan."""
    if amount < 0:
        raise ValueError("Usage amount cannot be negative.")
    plan, _ = _plan_for(user)
    limit = PLAN_LIMITS[plan].get(metric)
    period = _usage_period()
    with transaction.atomic():
        record, _ = UsageRecord.objects.select_for_update().get_or_create(
            user=user, period=period, metric=metric
        )
        current = record.quantity
        if amount == 0:
            return current < limit if limit is not None else True, current, limit, plan
        if limit is not None and current + amount > limit:
            return False, current, limit, plan
        record.quantity = current + amount
        record.save(update_fields=["quantity", "updated_at"])
        return True, record.quantity, limit, plan

def _audit(user, action, target_type="", target_id="", organization=None, metadata=None):
    return AuditLog.objects.create(user=user, organization=organization, action=action, target_type=target_type, target_id=str(target_id or ""), metadata=metadata or {})

def _entitlement(user, feature):
    plan, _ = _plan_for(user)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    if feature in {"ai", "ide"}:
        metric = "ai_messages_month" if feature == "ai" else "ide_runs_month"
        return limits[metric] > 0, plan, limits[metric]
    return True, plan, limits.get(feature)

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_PLANS = {
    "pro": os.environ.get("STRIPE_PRICE_PRO", ""),
    "team": os.environ.get("STRIPE_PRICE_TEAM", ""),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", ""),
}

def _stripe_request(path, data=None, method="POST"):
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        return None, "Billing provider is not configured."
    try:
        response = requests.request(
            method, f"{STRIPE_API}{path}", auth=(key, ""), data=data or {},
            timeout=(3.05, 20),
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError):
        return None, "Billing provider is temporarily unavailable."
    if response.status_code >= 400:
        return None, payload.get("error", {}).get("message", "Billing provider rejected the request.")
    return payload, None

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def usage_api(request):
    plan, sub = _plan_for(request.user)
    limits = PLAN_LIMITS[plan]
    return Response({
        "plan": plan,
        "status": sub.status,
        "period": _usage_period().isoformat(),
        "metrics": {
            "ai_messages": {"used": _usage_count(request.user, "ai_messages_month"), "limit": limits["ai_messages_month"]},
            "ide_runs": {"used": _usage_count(request.user, "ide_runs_month"), "limit": limits["ide_runs_month"]},
            "api_keys": {"used": APIKey.objects.filter(user=request.user, revoked_at__isnull=True).count(), "limit": limits["api_keys"]},
            "workspaces": {"used": CodeWorkspace.objects.filter(owner=request.user).count(), "limit": limits["workspaces"]},
            "projects": {"used": Project.objects.filter(owner=request.user).count(), "limit": limits["projects"]},
        },
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def billing_portal_api(request):
    sub = _subscription_for(request.user)
    if not sub.provider_customer_id or not os.environ.get("STRIPE_SECRET_KEY"):
        return Response({"error": "Billing portal is not configured for this account."}, status=503)
    portal, error = _stripe_request("/billing_portal/sessions", {"customer": sub.provider_customer_id, "return_url": f"{FRONTEND_URL}/billing"})
    if error:
        return Response({"error": error}, status=502)
    return Response({"url": portal.get("url")})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def subscription_api(request):
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    if request.method == "GET":
        return Response(SubscriptionSerializer(sub).data)

    plan = str(request.data.get("plan") or "free").lower()
    if plan not in dict(Subscription.PLAN_CHOICES):
        return Response({"error": "Unsupported plan"}, status=400)
    if plan == "free":
        if sub.provider_subscription_id and sub.status in {"active", "trialing", "past_due"}:
            _, error = _stripe_request(f"/subscriptions/{sub.provider_subscription_id}", {"cancel_at_period_end": "true"})
            if error:
                return Response({"error": error}, status=502)
            sub.cancel_at_period_end = True
            sub.save(update_fields=["cancel_at_period_end", "updated_at"])
            return Response(SubscriptionSerializer(sub).data)
        sub.plan = "free"
        sub.status = "active"
        sub.provider_subscription_id = ""
        sub.cancel_at_period_end = False
        sub.save(update_fields=["plan", "status", "provider_subscription_id", "cancel_at_period_end", "updated_at"])
        return Response(SubscriptionSerializer(sub).data)

    price_id = STRIPE_PLANS.get(plan, "")
    if not os.environ.get("STRIPE_SECRET_KEY") or not price_id:
        return Response({"error": "Paid billing is not configured for this plan."}, status=503)

    customer_id = sub.provider_customer_id
    if not customer_id:
        customer, error = _stripe_request("/customers", {
            "email": request.user.email,
            "metadata[user_id]": str(request.user.id),
        })
        if error:
            return Response({"error": error}, status=502)
        customer_id = customer.get("id", "")
        sub.provider_customer_id = customer_id
        sub.save(update_fields=["provider_customer_id", "updated_at"])

    checkout, error = _stripe_request("/checkout/sessions", {
        "mode": "subscription",
        "customer": customer_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": f"{FRONTEND_URL}/billing?checkout=success",
        "cancel_url": f"{FRONTEND_URL}/billing?checkout=cancel",
        "client_reference_id": str(request.user.id),
        "metadata[user_id]": str(request.user.id),
        "metadata[plan]": plan,
        "subscription_data[metadata][user_id]": str(request.user.id),
        "subscription_data[metadata][plan]": plan,
    })
    if error:
        return Response({"error": error}, status=502)
    return Response({"checkout_url": checkout.get("url"), "plan": plan})

def _stripe_signature_valid(payload, signature, secret):
    if not signature or not secret:
        return False
    timestamp = None
    signatures = []
    for item in signature.split(","):
        key, _, value = item.partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        return False
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False
    if abs(timezone.now().timestamp() - timestamp_int) > 300:
        return False
    signed = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(secrets.compare_digest(expected, value) for value in signatures)

@api_view(["POST"])
@permission_classes([AllowAny])
def billing_webhook_api(request):
    """Verify and apply Stripe events exactly once."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    signature = request.headers.get("Stripe-Signature", "")
    if not secret or not _stripe_signature_valid(request.body, signature, secret):
        return Response({"error": "Invalid webhook signature."}, status=400)
    try:
        event = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return Response({"error": "Invalid webhook payload."}, status=400)
    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "")
    if not event_id or not event_type:
        return Response({"error": "Webhook event id and type are required."}, status=400)
    with transaction.atomic():
        ledger, created = BillingEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event_type, "payload": event}
        )
        if not created:
            return Response({"received": True, "duplicate": True, "event_type": event_type})
        obj = ((event.get("data") or {}).get("object") or {})
        metadata = obj.get("metadata") or {}
        org_id = metadata.get("organization_id")
        if org_id:
            try:
                org = Organization.objects.get(pk=int(org_id))
            except (Organization.DoesNotExist, ValueError, TypeError):
                org = None
            if org:
                org_sub, _ = OrganizationSubscription.objects.select_for_update().get_or_create(organization=org, defaults={"plan": org.plan})
                price_id = (((obj.get("items") or {}).get("data") or [{}])[0].get("price") or {}).get("id")
                reverse_prices = {v: k for k, v in STRIPE_PLANS.items() if v}
                org_plan = metadata.get("plan") or reverse_prices.get(price_id) or org_sub.plan
                if event_type in {"checkout.session.completed", "customer.subscription.created", "customer.subscription.updated"}:
                    org_sub.provider_customer_id = obj.get("customer") or org_sub.provider_customer_id
                    org_sub.provider_subscription_id = obj.get("subscription") or obj.get("id") or org_sub.provider_subscription_id
                    org_sub.price_id = price_id or org_sub.price_id
                    org_sub.plan = org_plan
                    org_sub.status = {"trialing": "trialing", "active": "active", "past_due": "past_due", "canceled": "canceled"}.get(obj.get("status"), "active")
                    org_sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
                    org_sub.quantity = int((((obj.get("items") or {}).get("data") or [{}])[0].get("quantity") or org_sub.quantity or 1))
                    if obj.get("current_period_end"):
                        org_sub.current_period_end = timezone.datetime.fromtimestamp(int(obj["current_period_end"]), tz=dt_timezone.utc)
                    org_sub.save()
                    org.plan = org_plan if org_plan in dict(Organization.PLAN_CHOICES) else "free"
                    org.save(update_fields=["plan", "updated_at"])
                elif event_type == "customer.subscription.deleted":
                    org_sub.status = "canceled"; org_sub.plan = "free"; org_sub.cancel_at_period_end = False; org_sub.save()
                    org.plan = "free"; org.save(update_fields=["plan", "updated_at"])
                if event_type.startswith("invoice."):
                    invoice_id = obj.get("id")
                    if invoice_id:
                        inv, _ = BillingInvoice.objects.update_or_create(provider_invoice_id=invoice_id, defaults={
                            "organization": org, "number": obj.get("number") or "", "status": obj.get("status") or ("paid" if event_type == "invoice.paid" else "open"),
                            "currency": obj.get("currency") or "usd", "subtotal": int(obj.get("subtotal") or 0), "tax": int(obj.get("tax") or 0),
                            "total": int(obj.get("total") or 0), "amount_due": int(obj.get("amount_due") or 0), "hosted_url": obj.get("hosted_invoice_url") or "", "invoice_pdf": obj.get("invoice_pdf") or "",
                            "due_at": timezone.datetime.fromtimestamp(int(obj["due_date"]), tz=dt_timezone.utc) if obj.get("due_date") else None,
                            "paid_at": timezone.now() if event_type == "invoice.paid" else None,
                        })
                        if event_type == "invoice.payment_failed":
                            org_sub.status = "past_due"; org_sub.save(update_fields=["status", "updated_at"])
                            PaymentAttempt.objects.create(invoice=inv, status="failed", amount=int(obj.get("amount_due") or 0), failure_code=str(((obj.get("last_payment_error") or {}).get("code") or "")), failure_message=str(((obj.get("last_payment_error") or {}).get("message") or "")))
                        elif event_type == "invoice.paid":
                            org_sub.status = "active"; org_sub.save(update_fields=["status", "updated_at"])
                            PaymentAttempt.objects.create(invoice=inv, status="succeeded", amount=int(obj.get("amount_paid") or obj.get("total") or 0), provider_payment_id=str(obj.get("payment_intent") or ""))
                _audit(None, "billing.organization_webhook", "organization", org.id, organization=org, metadata={"event_id": event_id, "event_type": event_type})
        customer_id = obj.get("customer")
        user_id = metadata.get("user_id") or event.get("client_reference_id")
        if not user_id and customer_id:
            user_id = Subscription.objects.filter(provider_customer_id=customer_id).values_list("user_id", flat=True).first()
        if user_id:
            try:
                sub = Subscription.objects.select_for_update().get(user_id=int(user_id))
            except (Subscription.DoesNotExist, ValueError, TypeError):
                sub = Subscription.objects.create(user_id=int(user_id))
            price_id = (((obj.get("items") or {}).get("data") or [{}])[0].get("price") or {}).get("id")
            reverse_prices = {v: k for k, v in STRIPE_PLANS.items() if v}
            plan = metadata.get("plan") or reverse_prices.get(price_id) or sub.plan
            if event_type == "checkout.session.completed":
                sub.provider_customer_id = customer_id or sub.provider_customer_id
                sub.provider_subscription_id = obj.get("subscription") or sub.provider_subscription_id
                sub.plan = plan
                sub.status = "active"
            elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
                sub.provider_customer_id = customer_id or sub.provider_customer_id
                sub.provider_subscription_id = obj.get("id") or sub.provider_subscription_id
                sub.plan = plan
                sub.status = {"trialing": "trialing", "active": "active", "past_due": "past_due", "canceled": "canceled"}.get(obj.get("status"), sub.status)
                sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
                if obj.get("current_period_end"):
                    sub.current_period_end = timezone.datetime.fromtimestamp(int(obj["current_period_end"]), tz=dt_timezone.utc)
            elif event_type == "customer.subscription.deleted":
                sub.status = "canceled"
                sub.plan = "free"
                sub.current_period_end = timezone.now()
            elif event_type == "invoice.payment_failed":
                sub.status = "past_due"
            elif event_type == "invoice.paid":
                sub.status = "active"
            sub.save()
            Organization.objects.filter(owner_id=sub.user_id).update(plan=sub.plan if sub.plan in dict(Organization.PLAN_CHOICES) else "free")
            _audit(None, "billing.webhook", "subscription", sub.id, metadata={"event_id": event_id, "event_type": event_type, "user_id": sub.user_id})
    return Response({"received": True, "event_type": event_type, "duplicate": False})

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def organizations_api(request):
    if request.method == "GET":
        orgs = Organization.objects.filter(Q(owner=request.user) | Q(memberships__user=request.user)).distinct()
        return Response(OrganizationSerializer(orgs, many=True).data)
    name = str(request.data.get("name") or "").strip()
    if not name: return Response({"error":"name is required"}, status=400)
    slug = slugify(name)[:160] or "organization"
    base = slug
    n = 2
    try:
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)
            owner_plan, _ = _plan_for(locked_user)
            while Organization.objects.filter(slug=slug).exists():
                slug = f"{base}-{n}"; n += 1
            org = Organization.objects.create(owner=locked_user, name=name, slug=slug, plan=owner_plan)
            OrganizationMembership.objects.create(organization=org, user=locked_user, role="owner")
            _audit(locked_user, "organization.created", "organization", org.id, organization=org)
    except IntegrityError:
        return Response({"error": "Unable to create the organization. Please retry."}, status=409)
    return Response(OrganizationSerializer(org).data, status=201)


@api_view(["GET", "POST", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def organization_members_api(request, pk):
    membership = _org_access(request.user, pk, roles=["owner", "admin"])
    if not membership:
        return Response({"error":"Forbidden"}, status=403)
    org = membership.organization
    if request.method == "GET":
        return Response(OrganizationMembershipSerializer(org.memberships.select_related("user"), many=True).data)
    if request.method == "DELETE":
        m = get_object_or_404(OrganizationMembership, pk=request.data.get("id"), organization=org)
        if m.user_id == org.owner_id: return Response({"error":"Owner cannot be removed."}, status=400)
        m.delete(); return Response(status=204)
    identifier = str(request.data.get("username") or request.data.get("email") or "").strip()
    user = get_object_or_404(User, Q(username__iexact=identifier) | Q(email__iexact=identifier))
    role = str(request.data.get("role") or "developer")
    if role not in {"admin","developer","viewer"}: return Response({"error":"Invalid role"}, status=400)
    m, created = OrganizationMembership.objects.update_or_create(organization=org, user=user, defaults={"role":role})
    return Response(OrganizationMembershipSerializer(m).data, status=201 if created else 200)


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def organization_invites_api(request, pk):
    membership = _org_access(request.user, pk, roles=["owner", "admin"])
    if not membership:
        return Response({"error": "Forbidden"}, status=403)
    org = membership.organization
    if request.method == "GET":
        invites = org.invites.filter(accepted_at__isnull=True, expires_at__gt=timezone.now()).order_by("-created_at")[:100]
        return Response([{"id": i.id, "email": i.email, "role": i.role, "expires_at": i.expires_at, "created_at": i.created_at} for i in invites])
    if request.method == "DELETE":
        invite = get_object_or_404(OrganizationInvite, pk=request.data.get("id"), organization=org)
        invite.delete(); _audit(request.user, "organization.invite_revoked", "invite", invite.id, organization=org)
        return Response(status=204)
    email = str(request.data.get("email") or "").strip().lower()
    role = str(request.data.get("role") or "developer")
    if not email or "@" not in email:
        return Response({"error": "A valid email is required."}, status=400)
    if role not in {"admin", "developer", "viewer"}:
        return Response({"error": "Invalid role."}, status=400)
    with transaction.atomic():
        org = Organization.objects.select_for_update().get(pk=org.pk)
        plan = org.plan
        member_count = org.memberships.count()
        org_sub = OrganizationSubscription.objects.filter(organization=org).first()
        base_limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["org_members"]
        seat_limit = max(base_limit, org_sub.quantity) if org_sub and plan in {"team", "enterprise"} else base_limit
        if member_count >= seat_limit:
            return Response({"error": "Your organization plan has reached its member limit.", "limit": seat_limit}, status=403)
        invite = OrganizationInvite.objects.create(organization=org, inviter=request.user, email=email, role=role, token=secrets.token_urlsafe(48), expires_at=timezone.now()+timedelta(days=7))
    _audit(request.user, "organization.invite_created", "invite", invite.id, organization=org, metadata={"email": email, "role": role})
    return Response({"id": invite.id, "email": invite.email, "role": invite.role, "expires_at": invite.expires_at, "token": invite.token}, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def organization_invite_accept_api(request):
    token = str(request.data.get("token") or "").strip()
    invite = get_object_or_404(OrganizationInvite.objects.select_related("organization"), token=token, accepted_at__isnull=True)
    if invite.expires_at <= timezone.now():
        return Response({"error": "This invitation has expired."}, status=400)
    if request.user.email.lower() != invite.email.lower():
        return Response({"error": "This invitation is for a different email address."}, status=403)
    membership, _ = OrganizationMembership.objects.update_or_create(organization=invite.organization, user=request.user, defaults={"role": invite.role})
    invite.accepted_at = timezone.now(); invite.save(update_fields=["accepted_at"])
    _audit(request.user, "organization.invite_accepted", "organization", invite.organization_id, organization=invite.organization)
    return Response(OrganizationMembershipSerializer(membership).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_log_api(request):
    qs = AuditLog.objects.filter(Q(user=request.user) | Q(organization__memberships__user=request.user)).distinct().order_by("-created_at")[:200]
    return Response([{"id": x.id, "action": x.action, "target_type": x.target_type, "target_id": x.target_id, "metadata": x.metadata, "created_at": x.created_at} for x in qs])


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def ai_conversations_api(request):
    if request.method == "GET":
        qs = AIConversation.objects.filter(owner=request.user).order_by("-updated_at")[:50]
        return Response(AIConversationSerializer(qs, many=True).data)
    project_id = request.data.get("project")
    if project_id:
        project = get_object_or_404(Project, pk=project_id)
        if not _project_access(project, request.user):
            return Response({"error":"Forbidden"}, status=403)
    c = AIConversation.objects.create(owner=request.user, project_id=project_id, title=str(request.data.get("title") or "New conversation")[:200])
    return Response(AIConversationSerializer(c).data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_conversation_messages_api(request, pk):
    c = get_object_or_404(AIConversation, pk=pk, owner=request.user)
    return Response(AIMessageSerializer(c.messages.all(), many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([AssistantRateThrottle])
def ai_chat_api(request):
    message = str(request.data.get("message") or "").strip()
    if not message: return Response({"error":"message is required"}, status=400)
    if len(message) > 12000: return Response({"error":"message too long"}, status=400)
    allowed, used, limit, plan = _consume_usage(request.user, "ai_messages_month", 1)
    if not allowed:
        return Response({"error": "Monthly AI usage limit reached.", "plan": plan, "used": used, "limit": limit}, status=429)
    conversation = None
    workspace_id = request.data.get("workspace")
    if request.data.get("conversation"):
        conversation = get_object_or_404(AIConversation, pk=request.data["conversation"], owner=request.user)
    else:
        project_id = request.data.get("project")
        if project_id:
            project = get_object_or_404(Project, pk=project_id)
            if not _project_access(project, request.user):
                return Response({"error":"Forbidden"}, status=403)
        conversation = AIConversation.objects.create(owner=request.user, project_id=project_id, title=message[:60])
    if workspace_id:
        workspace = _workspace_for_user(workspace_id, request.user)
        if conversation.project_id and workspace.project_id not in (None, conversation.project_id):
            return Response({"error": "Workspace does not belong to the conversation project."}, status=400)
    context = _workspace_context(request.user, conversation.project_id, workspace_id)
    previous_history = list(conversation.messages.order_by("created_at").values("role", "content")[-12:])
    user_msg = AIMessage.objects.create(conversation=conversation, role="user", content=message, context=context)
    provider_url = os.environ.get("AI_API_URL", "").rstrip("/")
    provider_key = os.environ.get("AI_API_KEY", "")
    provider_model = os.environ.get("AI_MODEL", "")
    system = ("You are Developer OS Intelligence. You are a software engineering copilot. "
              "Use only the supplied workspace context for project facts. Be explicit about uncertainty. "
              "Return practical, technically precise steps. You may review architecture, tasks, notes and snippets.")
    answer = ""
    provider_error = None
    if provider_url and provider_key and provider_model:
        try:
            messages = [{"role":"system","content":system}] + [
                {"role": item["role"], "content": item["content"]} for item in previous_history
                if item["role"] in {"user", "assistant"}
            ]
            messages.append({"role":"user","content":json.dumps({"workspace":context,"request":message}, default=str)})
            endpoint = provider_url.rstrip("/")
            if not endpoint.endswith("/chat/completions"):
                endpoint = f"{endpoint}/chat/completions"
            upstream = requests.post(
                endpoint,
                headers={"Authorization":f"Bearer {provider_key}","Content-Type":"application/json"},
                json={"model":provider_model,"temperature":0.15,"messages":messages},
                timeout=(5, 35),
            )
            upstream.raise_for_status()
            payload = upstream.json()
            answer = str(payload.get("choices",[{}])[0].get("message",{}).get("content","") or "").strip()
            if not answer:
                provider_error = "AI provider returned an empty response."
        except requests.Timeout:
            provider_error = "AI provider timed out. Developer OS used its safe local engine instead."
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 502
            provider_error = f"AI provider rejected the request ({code}). Developer OS used its safe local engine instead."
        except (requests.RequestException, ValueError, IndexError, KeyError, TypeError):
            provider_error = "AI provider is temporarily unavailable. Developer OS used its safe local engine instead."

    if not answer:
        tasks = context["tasks"]
        blocked = [t for t in tasks if t["status"]=="blocked"]
        urgent = [t for t in tasks if t["priority"]=="urgent" and t["status"]!="done"]
        answer = (
            f"I indexed {len(context['projects'])} project(s), {len(tasks)} task(s), "
            f"{len(context['notes'])} note(s), and {len(context['snippets'])} snippet(s). "
            f"There are {len(blocked)} blocked and {len(urgent)} urgent active tasks. "
            "Connect an OpenAI-compatible provider for generative reasoning; the local engine will keep returning deterministic workspace signals."
        )
    try:
        assistant_msg = AIMessage.objects.create(conversation=conversation, role="assistant", content=answer, context=context)
        message_payload = AIMessageSerializer(assistant_msg).data
        conversation_payload = AIConversationSerializer(conversation).data
    except DatabaseError:
        message_payload = {"id": None, "role": "assistant", "content": answer, "context": context}
        conversation_payload = {"id": getattr(conversation, "id", None), "title": getattr(conversation, "title", "Workspace Intelligence")}
    payload = {"conversation": conversation_payload, "message": message_payload, "context": context}
    if provider_error:
        payload["provider_status"] = provider_error
    return Response(payload)



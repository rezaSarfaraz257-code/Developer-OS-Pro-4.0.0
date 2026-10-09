"""Mature SaaS control-plane services for Developer OS.

The module deliberately uses Django primitives and a durable database queue so
local development works without Redis. Redis/S3/SMTP are production adapters,
not fake implementations: enabling them changes the runtime path through the
same interfaces.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
import requests
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.mail import EmailMultiAlternatives, send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import (
    AccountDeletionRequest,
    BackgroundJob,
    BillingCredit,
    BillingInvoice,
    DataExportRequest,
    EmailVerificationToken,
    Incident,
    LoginAttempt,
    MFADevice,
    NotificationPreference,
    ObjectStorageFile,
    Organization,
    OrganizationMembership,
    OrganizationSubscription, OrganizationRolePermission,
    PaymentAttempt,
    ProductEvent,
    SecuritySession,
    SupportTicket,
)

User = get_user_model()


class EmailVerificationRateThrottle(AnonRateThrottle):
    scope = "email_verification"


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


# ---------------------------------------------------------------------------
# Security primitives
# ---------------------------------------------------------------------------

def client_ip(request):
    # X-Forwarded-For is only trusted when the deployment explicitly declares
    # a trusted reverse proxy. Otherwise it is attacker-controlled input.
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def revoke_user_sessions(user):
    """Revoke tracked sessions and blacklist every outstanding refresh token."""
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
    now = timezone.now()
    SecuritySession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=now)
    for token in OutstandingToken.objects.filter(user=user, expires_at__gt=now):
        BlacklistedToken.objects.get_or_create(token=token)


class MatureTokenRefreshSerializer(TokenRefreshSerializer):
    """Bind refresh rotation to a live SecuritySession record."""
    def validate(self, attrs):
        raw_refresh = attrs.get("refresh")
        try:
            old_token = RefreshToken(raw_refresh)
            user_id = old_token.get("user_id")
            old_jti = str(old_token.get("jti"))
        except Exception as exc:
            raise serializers.ValidationError("Invalid refresh token.") from exc

        session = SecuritySession.objects.filter(
            user_id=user_id, jti=old_jti, revoked_at__isnull=True
        ).first()
        if session is None:
            raise serializers.ValidationError("Refresh session is no longer active.")

        data = super().validate(attrs)
        new_refresh = data.get("refresh")
        if new_refresh:
            rotated = RefreshToken(new_refresh)
            session.jti = str(rotated.get("jti"))
            session.last_seen_at = timezone.now()
            session.save(update_fields=["jti", "last_seen_at"])
        else:
            session.last_seen_at = timezone.now()
            session.save(update_fields=["last_seen_at"])
        return data


def _totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def verify_totp(secret: str, code: str, window=1):
    code = str(code or "").replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    counter = int(time.time() // 30)
    return any(hmac.compare_digest(_totp(secret, counter + offset), code) for offset in range(-window, window + 1))


def _backup_codes(device):
    return device.backup_code_hashes or []


def consume_backup_code(device, supplied):
    supplied = str(supplied or "").strip().upper()
    digest = sha256(supplied)
    hashes = _backup_codes(device)
    if digest not in hashes:
        return False
    hashes.remove(digest)
    device.backup_code_hashes = hashes
    device.last_used_at = timezone.now()
    device.save(update_fields=["backup_code_hashes", "last_used_at"])
    return True


def _email(subject, to, text, html=None):
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@developer-os.local")
    message = EmailMultiAlternatives(subject, text, from_email, [to])
    if html:
        message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)


def queue_email(kind, to, subject, text, html=None, *, idempotency_key=""):
    payload = {"template": kind, "to": to, "subject": subject, "text": text, "html": html or ""}
    if idempotency_key:
        job, _ = BackgroundJob.objects.get_or_create(
            kind="email", idempotency_key=idempotency_key,
            defaults={"payload": payload},
        )
        return job
    return BackgroundJob.objects.create(kind="email", payload=payload)


def absolute_frontend(path):
    return f"{settings.FRONTEND_URL.rstrip('/')}/{path.lstrip('/')}"


def record_login(request, identifier, user=None, success=False, reason=""):
    LoginAttempt.objects.create(
        user=user,
        identifier=str(identifier)[:254],
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:2000],
        success=success,
        reason=reason,
    )


def _login_blocked(identifier, request):
    since = timezone.now() - timedelta(minutes=15)
    attempts = LoginAttempt.objects.filter(
        identifier__iexact=str(identifier), success=False, created_at__gte=since,
    ).count()
    ip_attempts = LoginAttempt.objects.filter(
        ip_address=client_ip(request), success=False, created_at__gte=since,
    ).count()
    return attempts >= 8 or ip_attempts >= 20


class MatureTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "username"

    def validate(self, attrs):
        identifier = attrs.get(self.username_field, "")
        request = self.context.get("request")
        if request and _login_blocked(identifier, request):
            record_login(request, identifier, success=False, reason="rate_limited")
            raise serializers.ValidationError("Too many failed sign-in attempts. Try again later.")

        password = attrs.get("password")
        user = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()
        username = user.username if user else identifier
        authenticated = authenticate(request=request, username=username, password=password)
        if not authenticated:
            if request:
                record_login(request, identifier, user=user, success=False, reason="invalid_credentials")
            raise serializers.ValidationError("Invalid credentials.")
        user = authenticated

        if getattr(settings, "EMAIL_VERIFICATION_REQUIRED", True) and user.email and not getattr(getattr(user, "profile", None), "email_verified", False):
            if request:
                record_login(request, identifier, user=user, success=False, reason="email_unverified")
            raise serializers.ValidationError("Verify your email before signing in.")

        device = getattr(user, "mfa_device", None)
        otp = self.initial_data.get("otp")
        backup_code = self.initial_data.get("backup_code")
        if device and device.enabled:
            if not (verify_totp(device.secret, otp) or consume_backup_code(device, backup_code)):
                if request:
                    record_login(request, identifier, user=user, success=False, reason="mfa_required")
                raise serializers.ValidationError({"mfa": "A valid authenticator code or backup code is required."})
            device.last_used_at = timezone.now()
            device.save(update_fields=["last_used_at"])

        data = super().validate({self.username_field: user.username, "password": password})
        refresh = self.get_token(user)
        data["access"] = str(refresh.access_token)
        data["refresh"] = str(refresh)
        if request:
            record_login(request, identifier, user=user, success=True, reason="success")
            previous = SecuritySession.objects.filter(user=user, ip_address=client_ip(request), revoked_at__isnull=True).exists()
            SecuritySession.objects.create(
                user=user,
                jti=str(refresh["jti"]),
                device_name=request.META.get("HTTP_SEC_CH_UA", "")[:200] or "Browser",
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:4000],
                ip_address=client_ip(request),
            )
            prefs, _ = NotificationPreference.objects.get_or_create(user=user)
            if not previous and user.email and prefs.security_alerts:
                queue_email("security_alert", user.email, "New Developer OS sign-in", f"A new sign-in was detected from {client_ip(request)}.", f"<p>A new Developer OS sign-in was detected.</p><p>IP: {client_ip(request)}</p>")
        return data


class MatureTokenObtainPairView(TokenObtainPairView):
    serializer_class = MatureTokenObtainPairSerializer


class MatureTokenRefreshView(TokenRefreshView):
    serializer_class = MatureTokenRefreshSerializer


# ---------------------------------------------------------------------------
# Email verification / password recovery / account security
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([EmailVerificationRateThrottle])
def resend_verification_api(request):
    email = str(request.data.get("email") or "").strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    # Always return the same response to prevent account enumeration.
    if user and user.email and not user.profile.email_verified:
        raw = secrets.token_urlsafe(48)
        EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        EmailVerificationToken.objects.create(user=user, token_hash=sha256(raw), expires_at=timezone.now() + timedelta(hours=24))
        url = absolute_frontend(f"verify-email?token={raw}&uid={user.pk}")
        queue_email("email_verification", user.email, "Verify your Developer OS email", f"Verify your email: {url}", f"<p>Verify your Developer OS email.</p><p><a href=\"{url}\">Verify email</a></p>", idempotency_key=f"email-verification:{user.pk}:{sha256(raw)}")
    return Response({"detail": "If that address is registered and unverified, a verification email has been queued."}, status=202)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([EmailVerificationRateThrottle])
def verify_email_api(request):
    raw = str(request.data.get("token") or "").strip()
    uid = request.data.get("uid")
    if not raw or not uid:
        return Response({"error": "Verification link is invalid or expired."}, status=400)

    # Lock the token row so simultaneous requests cannot both consume the same link.
    with transaction.atomic():
        token = EmailVerificationToken.objects.select_for_update().filter(
            token_hash=sha256(raw), user_id=uid, used_at__isnull=True
        ).select_related("user").first()
        if not token or token.expires_at <= timezone.now():
            return Response({"error": "Verification link is invalid or expired."}, status=400)
        now = timezone.now()
        token.used_at = now
        token.save(update_fields=["used_at"])
        profile = token.user.profile
        profile.email_verified = True
        profile.email_verified_at = now
        profile.save(update_fields=["email_verified", "email_verified_at"])
    return Response({"verified": True})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetRateThrottle])
def password_reset_request_api(request):
    email = str(request.data.get("email") or "").strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user and user.email:
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        url = absolute_frontend(f"reset-password?uid={uid}&token={token}")
        queue_email("password_reset", user.email, "Reset your Developer OS password", f"Reset your password: {url}", f"<p>A password reset was requested.</p><p><a href=\"{url}\">Reset password</a></p>", idempotency_key=f"password-reset:{user.pk}:{sha256(token)}")
    return Response({"detail": "If that address is registered, a password reset email has been queued."}, status=202)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetRateThrottle])
def password_reset_confirm_api(request):
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_decode
    uid = str(request.data.get("uid") or "")
    token = str(request.data.get("token") or "")
    password = str(request.data.get("password") or "")
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uid).decode())
    except Exception:
        return Response({"error": "Invalid reset link."}, status=400)
    if not default_token_generator.check_token(user, token):
        return Response({"error": "Invalid or expired reset link."}, status=400)
    try:
        validate_password(password, user)
    except Exception as exc:
        return Response({"error": getattr(exc, "messages", [str(exc)])}, status=400)
    user.set_password(password)
    user.save(update_fields=["password"])
    revoke_user_sessions(user)
    return Response({"reset": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password_api(request):
    current = str(request.data.get("current_password") or "")
    new_password = str(request.data.get("new_password") or "")
    if not request.user.check_password(current):
        return Response({"error": "Current password is incorrect."}, status=400)
    try:
        validate_password(new_password, request.user)
    except Exception as exc:
        return Response({"error": getattr(exc, "messages", [str(exc)])}, status=400)
    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    revoke_user_sessions(request.user)
    return Response({"changed": True, "sessions_revoked": True})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def security_sessions_api(request):
    if request.method == "GET":
        sessions = SecuritySession.objects.filter(user=request.user).order_by("-last_seen_at")[:100]
        return Response([{"id": s.id, "device": s.device_name or "Browser", "ip": s.ip_address, "user_agent": s.user_agent, "created_at": s.created_at, "last_seen_at": s.last_seen_at, "revoked": bool(s.revoked_at)} for s in sessions])
    session = SecuritySession.objects.filter(id=request.data.get("id"), user=request.user, revoked_at__isnull=True).first()
    if not session:
        return Response({"error": "Session not found."}, status=404)
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
    outstanding = OutstandingToken.objects.filter(jti=session.jti, user=request.user).first()
    if outstanding:
        BlacklistedToken.objects.get_or_create(token=outstanding)
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    return Response({"revoked": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def revoke_all_sessions_api(request):
    revoke_user_sessions(request.user)
    return Response({"revoked": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_setup_api(request):
    device, _ = MFADevice.objects.get_or_create(user=request.user, defaults={"secret": generate_totp_secret()})
    if device.enabled:
        return Response({"enabled": True}, status=409)
    if not device.secret:
        device.secret = generate_totp_secret()
        device.save(update_fields=["secret"])
    issuer = "Developer%20OS"
    label = f"{issuer}:{request.user.email or request.user.username}"
    uri = f"otpauth://totp/{label}?secret={device.secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return Response({"enabled": False, "secret": device.secret, "otpauth_uri": uri})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_confirm_api(request):
    device = getattr(request.user, "mfa_device", None)
    if not device or not verify_totp(device.secret, request.data.get("code")):
        return Response({"error": "Invalid authenticator code."}, status=400)
    codes = [secrets.token_hex(4).upper() for _ in range(10)]
    device.backup_code_hashes = [sha256(code) for code in codes]
    device.enabled = True
    device.confirmed_at = timezone.now()
    device.save(update_fields=["backup_code_hashes", "enabled", "confirmed_at"])
    return Response({"enabled": True, "backup_codes": codes})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_disable_api(request):
    device = getattr(request.user, "mfa_device", None)
    if not device or not device.enabled:
        return Response({"enabled": False})
    if not (verify_totp(device.secret, request.data.get("code")) or consume_backup_code(device, request.data.get("backup_code"))):
        return Response({"error": "Valid MFA verification is required."}, status=400)
    device.enabled = False
    device.save(update_fields=["enabled"])
    return Response({"enabled": False})


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def notification_preferences_api(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    if request.method == "GET":
        return Response({k: getattr(prefs, k) for k in ("product_updates", "security_alerts", "team_activity", "billing", "marketing", "email_enabled")})
    allowed = {k for k in ("product_updates", "security_alerts", "team_activity", "billing", "marketing", "email_enabled")}
    for key, value in request.data.items():
        if key in allowed:
            setattr(prefs, key, bool(value))
    prefs.save()
    return Response({k: getattr(prefs, k) for k in allowed})


# ---------------------------------------------------------------------------
# Durable jobs / exports / lifecycle
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def jobs_api(request):
    qs = BackgroundJob.objects.filter(payload__user_id=request.user.id).order_by("-created_at")[:100]
    return Response([{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "created_at": j.created_at, "finished_at": j.finished_at, "error": j.error} for j in qs])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def data_export_api(request):
    existing = DataExportRequest.objects.filter(user=request.user, status__in=["queued", "running"], created_at__gte=timezone.now() - timedelta(hours=1)).first()
    if existing:
        return Response({"id": existing.id, "status": existing.status}, status=202)
    export = DataExportRequest.objects.create(user=request.user, expires_at=timezone.now() + timedelta(hours=24))
    BackgroundJob.objects.create(kind="data_export", idempotency_key=f"data-export:{request.user.id}:{export.id}", payload={"user_id": request.user.id, "export_id": export.id}, max_attempts=2)
    return Response({"id": export.id, "status": export.status}, status=202)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def data_export_status_api(request, pk):
    export = DataExportRequest.objects.filter(pk=pk, user=request.user).first()
    if not export:
        return Response({"error": "Export not found."}, status=404)
    download = export.file_path if export.status == "ready" else None
    if export.status == "ready":
        from .storage import presigned_get
        download = presigned_get(export.file_path) or export.file_path
    return Response({"id": export.id, "status": export.status, "download_path": download, "expires_at": export.expires_at})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def account_deletion_request_api(request):
    confirm = str(request.data.get("confirm") or "").strip()
    if confirm != "DELETE":
        return Response({"error": "Type DELETE to schedule account deletion."}, status=400)
    raw = secrets.token_urlsafe(48)
    obj, _ = AccountDeletionRequest.objects.update_or_create(user=request.user, defaults={"token_hash": sha256(raw), "status": "pending", "scheduled_for": timezone.now() + timedelta(days=7)})
    return Response({"scheduled": True, "scheduled_for": obj.scheduled_for, "confirmation_token": raw})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def account_deletion_confirm_api(request):
    raw = str(request.data.get("token") or "")
    obj = AccountDeletionRequest.objects.filter(user=request.user, token_hash=sha256(raw), status="pending").first()
    if not obj or obj.scheduled_for > timezone.now():
        return Response({"error": "Invalid confirmation token or deletion window is still pending."}, status=400)
    obj.status = "confirmed"
    obj.confirmed_at = timezone.now()
    obj.save(update_fields=["status", "confirmed_at"])
    request.user.delete()
    return Response({"deleted": True})


# ---------------------------------------------------------------------------
# Organization billing, invoices, support, analytics
# ---------------------------------------------------------------------------

def _org_access(user, org_id, roles=None):
    q = OrganizationMembership.objects.select_related("organization").filter(user=user, organization_id=org_id)
    if roles:
        q = q.filter(role__in=roles)
    return q.first()


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def organization_billing_checkout_api(request, pk):
    membership = _org_access(request.user, pk, roles=["owner", "admin"])
    if not membership:
        return Response({"error": "Only organization owners/admins can manage billing."}, status=403)
    org = membership.organization
    plan = str(request.data.get("plan") or "free").lower()
    billing_cycle = str(request.data.get("billing_cycle") or "monthly").strip().lower()
    if billing_cycle not in {"monthly", "annual"}:
        return Response({"error": "billing_cycle must be monthly or annual."}, status=400)
    if plan not in {"team", "enterprise"}:
        return Response({
            "error": "Organization billing supports Team seat billing. Pro is personal and Enterprise is custom.",
            "code": "organization_plan_not_supported",
        }, status=400)
    if plan == "enterprise":
        return Response({
            "error": "Enterprise organization billing is custom. Contact sales for a quote and security requirements.",
            "code": "enterprise_contact_sales",
            "required_plan": "enterprise",
        }, status=400)
    if plan == "free":
        return Response({"error": "Use the billing portal to cancel a paid subscription."}, status=400)
    secret = os.getenv("STRIPE_SECRET_KEY", "")
    prices = {
        "team": {"monthly": os.getenv("STRIPE_PRICE_TEAM", ""), "annual": os.getenv("STRIPE_PRICE_TEAM_ANNUAL", "")},
        "enterprise": {"monthly": os.getenv("STRIPE_PRICE_ENTERPRISE", ""), "annual": os.getenv("STRIPE_PRICE_ENTERPRISE_ANNUAL", "")},
    }
    price = prices.get(plan, {}).get(billing_cycle, "")
    if not secret or not price:
        return Response({"error": "Stripe organization billing is not configured for this plan."}, status=503)
    sub, _ = OrganizationSubscription.objects.get_or_create(organization=org, defaults={"plan": org.plan})
    customer_id = sub.provider_customer_id
    if not customer_id:
        r = requests.post("https://api.stripe.com/v1/customers", auth=(secret, ""), data={"name": org.name, "metadata[organization_id]": org.id, "metadata[owner_id]": org.owner_id}, timeout=(3.05, 20))
        if r.status_code >= 400:
            return Response({"error": "Unable to create the billing customer."}, status=502)
        customer_id = r.json().get("id", "")
        sub.provider_customer_id = customer_id
        sub.save(update_fields=["provider_customer_id", "updated_at"])
    try:
        requested_quantity = max(1, min(5000, int(request.data.get("quantity") or 1)))
    except (TypeError, ValueError):
        return Response({"error": "quantity must be a positive integer."}, status=400)
    active_members = OrganizationMembership.objects.filter(organization=org).count()
    # Only Team is seat-metered. Pro and Enterprise are organization-level plans;
    # Enterprise remains a starting price and can be handled through custom sales.
    quantity = requested_quantity if plan == "team" else 1
    if plan == "team" and quantity < active_members:
        return Response({"error": f"Team seats ({quantity}) cannot be lower than active members ({active_members}).", "quantity": quantity, "active_members": active_members}, status=400)
    if sub.provider_subscription_id and sub.status in {"active", "trialing", "past_due"}:
        r = requests.get(f"https://api.stripe.com/v1/subscriptions/{sub.provider_subscription_id}", auth=(secret, ""), timeout=(3.05, 20))
        if r.ok:
            items = (r.json().get("items") or {}).get("data") or []
            if items:
                item_id = items[0].get("id")
                update = {
                    "items[0][id]": item_id, "items[0][price]": price, "items[0][quantity]": str(quantity),
                    "proration_behavior": "create_prorations", "metadata[organization_id]": str(org.id), "metadata[plan]": plan, "metadata[billing_cycle]": billing_cycle,
                }
                changed = requests.post(f"https://api.stripe.com/v1/subscriptions/{sub.provider_subscription_id}", auth=(secret, ""), data=update, timeout=(3.05, 20))
                if changed.ok:
                    payload = changed.json()
                    sub.plan = plan; sub.quantity = quantity; sub.price_id = price; sub.status = payload.get("status", sub.status)
                    sub.cancel_at_period_end = bool(payload.get("cancel_at_period_end", False))
                    if payload.get("current_period_end"):
                        from datetime import datetime, timezone as dt_timezone
                        sub.current_period_end = datetime.fromtimestamp(int(payload["current_period_end"]), tz=dt_timezone.utc)
                    sub.save()
                    org.plan = plan; org.save(update_fields=["plan", "updated_at"])
                    return Response({"updated": True, "plan": plan, "quantity": quantity, "proration": "create_prorations"})
    checkout_data = {
        "mode": "subscription", "customer": customer_id, "line_items[0][price]": price,
        "line_items[0][quantity]": str(quantity), "success_url": f"{settings.FRONTEND_URL}/billing?organization={org.id}&checkout=success",
        "cancel_url": f"{settings.FRONTEND_URL}/billing?organization={org.id}&checkout=cancel",
        "metadata[organization_id]": str(org.id), "metadata[plan]": plan,
        "subscription_data[metadata][organization_id]": str(org.id), "subscription_data[metadata][plan]": plan, "subscription_data[metadata][billing_cycle]": billing_cycle,
        "subscription_data[metadata][owner_id]": str(org.owner_id),
    }
    r = requests.post("https://api.stripe.com/v1/checkout/sessions", auth=(secret, ""), data=checkout_data, timeout=(3.05, 20))
    if r.status_code >= 400:
        return Response({"error": "Billing provider rejected the checkout request."}, status=502)
    return Response({"checkout_url": r.json().get("url"), "organization": org.id, "plan": plan, "quantity": quantity})


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def organization_permissions_api(request, pk):
    membership = _org_access(request.user, pk, roles=["owner", "admin"])
    if not membership:
        return Response({"error": "Forbidden"}, status=403)
    org = membership.organization
    if request.method == "GET":
        defaults = {"owner": ["organization.manage", "billing.manage", "members.manage", "audit.read"], "admin": ["members.manage", "projects.manage", "audit.read"], "developer": ["projects.read", "projects.write", "ide.run"], "viewer": ["projects.read"]}
        rows = []
        for role, perms in defaults.items():
            row, _ = OrganizationRolePermission.objects.get_or_create(organization=org, role=role, defaults={"permissions": perms})
            rows.append({"role": row.role, "permissions": row.permissions, "updated_at": row.updated_at})
        return Response(rows)
    role = str(request.data.get("role") or "").strip()
    permissions = request.data.get("permissions") or []
    if role not in {"owner", "admin", "developer", "viewer", "custom"} or not isinstance(permissions, list):
        return Response({"error": "Invalid role or permissions."}, status=400)
    row, _ = OrganizationRolePermission.objects.update_or_create(organization=org, role=role, defaults={"permissions": [str(x) for x in permissions][:100]})
    return Response({"role": row.role, "permissions": row.permissions})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def organization_billing_api(request, pk):
    membership = _org_access(request.user, pk)
    if not membership:
        return Response({"error": "Forbidden"}, status=403)
    org = membership.organization
    sub, _ = OrganizationSubscription.objects.get_or_create(organization=org, defaults={"plan": org.plan})
    invoices = BillingInvoice.objects.filter(organization=org).order_by("-created_at")[:100]
    return Response({
        "organization": {"id": org.id, "name": org.name, "plan": org.plan},
        "subscription": {"plan": sub.plan, "status": sub.status, "quantity": sub.quantity, "current_period_end": sub.current_period_end, "cancel_at_period_end": sub.cancel_at_period_end},
        "invoices": [{"id": x.id, "number": x.number, "status": x.status, "currency": x.currency, "subtotal": x.subtotal, "tax": x.tax, "total": x.total, "amount_due": x.amount_due, "hosted_url": x.hosted_url, "invoice_pdf": x.invoice_pdf, "created_at": x.created_at, "paid_at": x.paid_at} for x in invoices],
    })


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def support_tickets_api(request):
    if request.method == "GET":
        qs = SupportTicket.objects.filter(user=request.user).order_by("-updated_at")[:100]
        return Response([{"id": x.id, "subject": x.subject, "body": x.body, "status": x.status, "priority": x.priority, "created_at": x.created_at, "updated_at": x.updated_at} for x in qs])
    subject = str(request.data.get("subject") or "").strip()
    body = str(request.data.get("body") or "").strip()
    priority = str(request.data.get("priority") or "normal")
    if not subject or not body:
        return Response({"error": "subject and body are required"}, status=400)
    if priority not in {"low", "normal", "high", "urgent"}:
        return Response({"error": "Invalid priority"}, status=400)
    ticket = SupportTicket.objects.create(user=request.user, subject=subject[:240], body=body, priority=priority)
    return Response({"id": ticket.id, "status": ticket.status}, status=201)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def support_admin_api(request):
    qs = SupportTicket.objects.select_related("user").order_by("status", "-created_at")[:500]
    return Response([{"id": x.id, "user": x.user.email, "subject": x.subject, "body": x.body, "status": x.status, "priority": x.priority, "created_at": x.created_at} for x in qs])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def product_event_api(request):
    name = str(request.data.get("name") or "").strip()
    if not name or len(name) > 120:
        return Response({"error": "event name is required"}, status=400)
    event = ProductEvent.objects.create(user=request.user, organization_id=request.data.get("organization"), name=name, properties=request.data.get("properties") or {})
    return Response({"id": event.id, "recorded": True}, status=201)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def product_analytics_api(request):
    since = timezone.now() - timedelta(days=min(365, max(1, int(request.query_params.get("days", 30)))))
    events = ProductEvent.objects.filter(occurred_at__gte=since)
    activation = events.filter(name="project_created").values("user_id").distinct().count()
    registered = User.objects.filter(date_joined__gte=since).count()
    registered_users = list(User.objects.filter(date_joined__gte=since).values("id", "date_joined"))
    ids = [u["id"] for u in registered_users]
    seven = ProductEvent.objects.filter(user_id__in=ids, occurred_at__gte=since + timedelta(days=7), occurred_at__lte=timezone.now()).values_list("user_id", flat=True).distinct().count()
    thirty = ProductEvent.objects.filter(user_id__in=ids, occurred_at__gte=since + timedelta(days=30), occurred_at__lte=timezone.now()).values_list("user_id", flat=True).distinct().count()
    paid = User.objects.filter(id__in=ids, subscription__plan__in=["pro", "team", "enterprise"]).count()
    return Response({
        "period_start": since,
        "registered_users": registered,
        "activated_users": activation,
        "activation_rate": round(activation * 100 / registered, 2) if registered else 0,
        "retention_7d_proxy": round(seven * 100 / registered, 2) if registered else 0,
        "retention_30d_proxy": round(thirty * 100 / registered, 2) if registered else 0,
        "paid_conversion_rate": round(paid * 100 / registered, 2) if registered else 0,
        "events": list(events.values("name").annotate(count=Count("id")).order_by("-count")[:100]),
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def public_status_api(request):
    incidents = Incident.objects.filter(status__in=["investigating", "identified", "monitoring"]).order_by("-started_at")[:20]
    return Response({"status": "degraded" if incidents else "operational", "incidents": [{"id": i.id, "title": i.title, "status": i.status, "severity": i.severity, "summary": i.summary, "started_at": i.started_at} for i in incidents]})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def operational_metrics_api(request):
    from django.db import connection
    checks = {"database": False, "email_configured": bool(getattr(settings, "EMAIL_HOST", "") or getattr(settings, "EMAIL_BACKEND", "").endswith("console.EmailBackend")), "object_storage": bool(os.getenv("S3_BUCKET"))}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = True
    except Exception:
        pass
    return Response({"checks": checks, "queued_jobs": BackgroundJob.objects.filter(status="queued").count(), "failed_jobs": BackgroundJob.objects.filter(status="failed").count(), "active_sessions": SecuritySession.objects.filter(revoked_at__isnull=True).count()})

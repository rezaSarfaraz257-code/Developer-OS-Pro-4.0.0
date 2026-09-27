"""Authentication helpers for Developer OS public API access."""
import hashlib
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import APIKey

class APIKeyAuthentication(BaseAuthentication):
    """Authenticate first-party API keys issued from /api/api-keys/."""
    keyword = "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith(f"{self.keyword} "):
            return None
        raw = header.split(" ", 1)[1].strip()
        if not raw.startswith("dos_live_"):
            return None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key = APIKey.objects.select_related("user").filter(
            key_hash=digest, revoked_at__isnull=True
        ).first()
        if not key or not key.user.is_active:
            raise AuthenticationFailed("Invalid or revoked API key.")
        APIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return (key.user, key)

    def authenticate_header(self, request):
        return self.keyword

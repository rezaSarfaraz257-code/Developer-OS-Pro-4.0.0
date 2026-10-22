import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def enabled(value):
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Command(BaseCommand):
    help = "Explicitly create or repair the configured Django administrator from environment variables."

    def handle(self, *args, **options):
        if not enabled(os.getenv("DJANGO_SUPERUSER_BOOTSTRAP", "")):
            raise CommandError(
                "Admin bootstrap is disabled. Set DJANGO_SUPERUSER_BOOTSTRAP=true for one deployment."
            )

        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")
        if not username or not email or not password:
            raise CommandError(
                "DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, and "
                "DJANGO_SUPERUSER_PASSWORD must all be set."
            )
        if len(password) < 14:
            raise CommandError("DJANGO_SUPERUSER_PASSWORD must contain at least 14 characters.")

        User = get_user_model()
        matches = list(User.objects.filter(username__iexact=username)[:2])
        if len(matches) > 1:
            raise CommandError("Multiple accounts match this username ignoring case; no changes were made.")
        user = matches[0] if matches else User(username=username, email=email)

        if User.objects.exclude(pk=user.pk).filter(email__iexact=email).exists():
            raise CommandError("The configured email belongs to a different account; no changes were made.")

        user.username = user.get_username()
        user.email = email
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError("The configured password failed Django validation: " + "; ".join(exc.messages)) from exc

        with transaction.atomic():
            user.set_password(password)
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            user.save()

        self.stdout.write(self.style.SUCCESS(
            f"Administrator bootstrap completed for username={user.get_username()!r}. "
            "No password was printed."
        ))
        self.stdout.write(
            "IMPORTANT: set DJANGO_SUPERUSER_BOOTSTRAP=false and remove "
            "DJANGO_SUPERUSER_PASSWORD from Render Environment after this deployment."
        )

from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Interactively repair Django admin access for an existing user, or "
        "explicitly create a new staff superuser when --create-if-missing is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Existing account username (case-insensitive lookup).")
        parser.add_argument("--email", help="Email required only when creating a new account.")
        parser.add_argument(
            "--create-if-missing",
            action="store_true",
            help="Allow creation of a new admin only if the requested username does not exist.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("--username cannot be empty.")

        User = get_user_model()
        matches = list(User.objects.filter(username__iexact=username)[:2])
        if len(matches) > 1:
            raise CommandError("More than one case-insensitive username match exists; resolve duplicates manually.")

        user = matches[0] if matches else None
        if user is None and not options["create_if_missing"]:
            raise CommandError(
                "No account with that username exists in the configured database. "
                "Re-run with --create-if-missing only if you intend to create a new administrator."
            )

        email = (options.get("email") or "").strip().lower()
        if user is None:
            if not email:
                raise CommandError("--email is required with --create-if-missing.")
            if User.objects.filter(email__iexact=email).exists():
                raise CommandError("That email is already associated with another account.")
        elif email and User.objects.exclude(pk=user.pk).filter(email__iexact=email).exists():
            raise CommandError("That email is already associated with another account.")

        password = getpass("New admin password (input hidden): ")
        confirm = getpass("Confirm new admin password: ")
        if not password or password != confirm:
            raise CommandError("Passwords are empty or do not match.")
        if len(password) < 14:
            raise CommandError("Use a password with at least 14 characters.")

        with transaction.atomic():
            if user is None:
                user = User(username=username, email=email)
            elif email:
                user.email = email
            user.set_password(password)
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            user.save()

        self.stdout.write(self.style.SUCCESS(
            f"Admin access repaired for username={user.get_username()!r}; "
            "password updated, is_active/is_staff/is_superuser enabled."
        ))
        self.stdout.write("No password or secret was written to logs.");

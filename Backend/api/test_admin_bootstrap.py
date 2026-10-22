import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings


class BootstrapAdminFromEnvTests(TestCase):
    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_creates_active_staff_superuser_from_explicit_environment_opt_in(self):
        values = {
            "DJANGO_SUPERUSER_BOOTSTRAP": "true",
            "DJANGO_SUPERUSER_USERNAME": "recovery-admin",
            "DJANGO_SUPERUSER_EMAIL": "recovery-admin@example.test",
            "DJANGO_SUPERUSER_PASSWORD": "Z7!mQ2@pL9#vR4$x",
        }
        with patch.dict(os.environ, values):
            output = StringIO()
            call_command("bootstrap_admin_from_env", stdout=output)

        user = get_user_model().objects.get(username="recovery-admin")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(values["DJANGO_SUPERUSER_PASSWORD"]))
        self.assertNotIn(values["DJANGO_SUPERUSER_PASSWORD"], output.getvalue())

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_repairs_existing_matching_account_without_creating_duplicate(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="Recovery-Admin",
            email="old@example.test",
            password="old-password",
            is_active=False,
            is_staff=False,
            is_superuser=False,
        )
        values = {
            "DJANGO_SUPERUSER_BOOTSTRAP": "1",
            "DJANGO_SUPERUSER_USERNAME": "recovery-admin",
            "DJANGO_SUPERUSER_EMAIL": "recovery-admin@example.test",
            "DJANGO_SUPERUSER_PASSWORD": "Z7!mQ2@pL9#vR4$x",
        }
        with patch.dict(os.environ, values):
            call_command("bootstrap_admin_from_env", stdout=StringIO())

        self.assertEqual(User.objects.count(), 1)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(values["DJANGO_SUPERUSER_PASSWORD"]))

    def test_refuses_to_run_without_explicit_opt_in(self):
        values = {
            "DJANGO_SUPERUSER_BOOTSTRAP": "false",
            "DJANGO_SUPERUSER_USERNAME": "recovery-admin",
            "DJANGO_SUPERUSER_EMAIL": "recovery-admin@example.test",
            "DJANGO_SUPERUSER_PASSWORD": "Z7!mQ2@pL9#vR4$x",
        }
        with patch.dict(os.environ, values):
            with self.assertRaises(CommandError):
                call_command("bootstrap_admin_from_env")

        self.assertFalse(get_user_model().objects.filter(username="recovery-admin").exists())

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_rejects_email_owned_by_a_different_user(self):
        User = get_user_model()
        User.objects.create_user(username="another-user", email="recovery-admin@example.test")
        values = {
            "DJANGO_SUPERUSER_BOOTSTRAP": "true",
            "DJANGO_SUPERUSER_USERNAME": "recovery-admin",
            "DJANGO_SUPERUSER_EMAIL": "recovery-admin@example.test",
            "DJANGO_SUPERUSER_PASSWORD": "Z7!mQ2@pL9#vR4$x",
        }
        with patch.dict(os.environ, values):
            with self.assertRaises(CommandError):
                call_command("bootstrap_admin_from_env")

        self.assertFalse(User.objects.filter(username="recovery-admin").exists())

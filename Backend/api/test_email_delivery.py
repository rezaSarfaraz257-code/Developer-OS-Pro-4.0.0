import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from api.management.commands.worker import execute


class EmailWorkerDeliverySafetyTests(SimpleTestCase):
    def email_job(self):
        return SimpleNamespace(
            kind="email",
            payload={
                "subject": "Verify your Developer OS email",
                "text": "Verification message",
                "html": "<p>Verification message</p>",
                "to": "member@example.test",
            },
        )

    @override_settings(
        IS_PRODUCTION=True,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        EMAIL_HOST="",
    )
    def test_deployed_console_backend_fails_closed_without_printing_email(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            with self.assertRaisesMessage(
                RuntimeError, "authenticated encrypted SMTP is required"
            ):
                execute(self.email_job())
        mail.assert_not_called()

    @override_settings(
        IS_PRODUCTION=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST="smtp.example.test",
        EMAIL_HOST_USER="user",
        EMAIL_HOST_PASSWORD="secret",
        DEFAULT_FROM_EMAIL="no-reply@example.test",
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=False,
    )
    def test_deployed_non_smtp_backend_is_rejected_even_with_host(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            with self.assertRaisesMessage(
                RuntimeError, "authenticated encrypted SMTP is required"
            ):
                execute(self.email_job())
        mail.assert_not_called()

    @override_settings(
        IS_PRODUCTION=True,
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.example.test",
        EMAIL_HOST_USER="user",
        EMAIL_HOST_PASSWORD="",
        DEFAULT_FROM_EMAIL="no-reply@example.test",
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=False,
    )
    def test_deployed_smtp_without_password_is_rejected(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            with self.assertRaisesMessage(
                RuntimeError, "authenticated encrypted SMTP is required"
            ):
                execute(self.email_job())
        mail.assert_not_called()

    @override_settings(
        IS_PRODUCTION=True,
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.example.test",
        EMAIL_HOST_USER="user@example.test",
        EMAIL_HOST_PASSWORD="test-app-password",
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=False,
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@example.test>",
    )
    def test_configured_smtp_backend_sends_using_configured_sender(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            mail.return_value.send.return_value = 1
            result = execute(self.email_job())

        self.assertEqual(result, {"sent": True, "to": "member@example.test"})
        mail.assert_called_once_with(
            "Verify your Developer OS email",
            "Verification message",
            "Developer OS <no-reply@example.test>",
            ["member@example.test"],
        )
        mail.return_value.attach_alternative.assert_called_once_with(
            "<p>Verification message</p>", "text/html"
        )
        mail.return_value.send.assert_called_once_with(fail_silently=False)

    @override_settings(
        IS_PRODUCTION=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        EMAIL_HOST="",
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@localhost>",
    )
    def test_local_development_console_backend_remains_available(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            mail.return_value.send.return_value = 1
            result = execute(self.email_job())

        self.assertTrue(result["sent"])
        mail.return_value.send.assert_called_once_with(fail_silently=False)

    @override_settings(
        IS_PRODUCTION=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        EMAIL_HOST="",
    )
    def test_render_runtime_fails_closed_even_if_environment_is_not_marked_production(self):
        with patch.dict(
            os.environ,
            {"RENDER_SERVICE_ID": "srv-test", "RENDER_GIT_COMMIT": ""},
        ):
            with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
                with self.assertRaisesMessage(
                    RuntimeError, "a real email transport is required"
                ):
                    execute(self.email_job())
        mail.assert_not_called()

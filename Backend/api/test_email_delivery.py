import json
import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from api.management.commands.worker import execute
from api.email_backends.resend import EmailBackend as ResendEmailBackend


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
            with self.assertRaisesMessage(RuntimeError, "email is required"):
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
            with self.assertRaisesMessage(RuntimeError, "email is required"):
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
            with self.assertRaisesMessage(RuntimeError, "email is required"):
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
                with self.assertRaisesMessage(RuntimeError, "email is required"):
                    execute(self.email_job())
        mail.assert_not_called()

    @override_settings(
        IS_PRODUCTION=True,
        EMAIL_BACKEND="api.email_backends.resend.EmailBackend",
        RESEND_API_KEY="re_test_key",
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@example.com>",
    )
    def test_resend_backend_is_accepted_without_smtp_settings(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            mail.return_value.send.return_value = 1
            result = execute(self.email_job())
        self.assertEqual(result, {"sent": True, "to": "member@example.test"})
        mail.return_value.send.assert_called_once_with(fail_silently=False)

    @override_settings(
        IS_PRODUCTION=True,
        EMAIL_BACKEND="api.email_backends.resend.EmailBackend",
        RESEND_API_KEY="",
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@example.com>",
    )
    def test_resend_backend_fails_closed_without_api_key(self):
        with patch("api.management.commands.worker.EmailMultiAlternatives") as mail:
            with self.assertRaisesMessage(RuntimeError, "Resend HTTPS API"):
                execute(self.email_job())
        mail.assert_not_called()

    @override_settings(
        RESEND_API_KEY="re_test_key",
        EMAIL_TIMEOUT=7,
    )
    def test_resend_backend_posts_html_and_text_over_https(self):
        message = EmailMultiAlternatives(
            "Verify your Developer OS email",
            "Plain text body",
            "Developer OS <no-reply@example.com>",
            ["member@example.test"],
        )
        message.attach_alternative("<p>HTML body</p>", "text/html")
        response = SimpleNamespace(status=200, read=lambda: b'{"id":"email_test"}')

        with patch(
            "api.email_backends.resend.urllib.request.urlopen",
            return_value=nullcontext(response),
        ) as urlopen:
            sent = ResendEmailBackend().send_messages([message])

        self.assertEqual(sent, 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("Authorization"), "Bearer re_test_key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["to"], ["member@example.test"])
        self.assertEqual(payload["text"], "Plain text body")
        self.assertEqual(payload["html"], "<p>HTML body</p>")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)

    @override_settings(RESEND_API_KEY="re_test_key")
    def test_resend_http_error_does_not_leak_provider_response(self):
        from urllib.error import HTTPError
        from io import BytesIO

        message = EmailMultiAlternatives(
            "Subject", "Body", "no-reply@example.com", ["member@example.test"]
        )
        error = HTTPError(
            "https://api.resend.com/emails",
            401,
            "Unauthorized",
            {},
            BytesIO(b"secret provider diagnostic"),
        )
        with patch("api.email_backends.resend.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesMessage(RuntimeError, "HTTP 401") as raised:
                ResendEmailBackend().send_messages([message])
        self.assertNotIn("secret provider diagnostic", str(raised.exception))

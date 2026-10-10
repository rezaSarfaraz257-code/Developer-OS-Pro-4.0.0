from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .mature import sha256
from .models import BackgroundJob, EmailVerificationToken, UserProfile
from .mature import queue_email


class AuthenticationHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("api.views.queue_email")
    def test_registration_normalizes_email_and_queues_single_use_verification(self, queue_email):
        response = self.client.post(
            "/api/register/",
            {
                "username": "secure-new-user",
                "email": "Secure.New.User@Example.test",
                "password": "A-Unique-Long-Password-934!",
                "first_name": "Secure",
                "last_name": "User",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(username="secure-new-user")
        self.assertEqual(user.email, "secure.new.user@example.test")
        profile, _ = UserProfile.objects.get_or_create(user=user)
        self.assertFalse(profile.email_verified)
        self.assertTrue(EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).exists())
        queue_email.assert_called_once()
        self.assertEqual(queue_email.call_args.args[1], user.email)
        self.assertEqual(queue_email.call_args.kwargs["idempotency_key"].split(":")[:2], ["email-verification", str(user.pk)])

    @override_settings(
        EMAIL_DELIVERY_MODE="inline",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@example.test>",
    )
    def test_registration_sends_verification_email_without_worker(self):
        from django.core import mail

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/register/",
                {
                    "username": "inline-mail-user",
                    "email": "inline-mail@example.test",
                    "password": "A-Unique-Long-Password-934!",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["inline-mail@example.test"])
        self.assertIn("verify-email?token=", mail.outbox[0].body)
        job = BackgroundJob.objects.get(kind="email", payload__to="inline-mail@example.test")
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.result.get("delivery_mode"), "inline")

    @override_settings(
        EMAIL_DELIVERY_MODE="inline",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@example.test>",
    )
    def test_inline_email_failure_is_recorded_without_raising(self):
        with patch("api.mature.EmailMultiAlternatives.send", side_effect=OSError("SMTP unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                job = queue_email(
                    "email_verification",
                    "failure@example.test",
                    "Verify",
                    "Verification link",
                    idempotency_key="inline-failure-test",
                )

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)
        self.assertIn("SMTP unavailable", job.error)
        self.assertIsNotNone(job.finished_at)

    @override_settings(
        EMAIL_DELIVERY_MODE="inline",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="Developer OS <no-reply@example.test>",
    )
    def test_inline_delivery_respects_idempotency(self):
        from django.core import mail

        with self.captureOnCommitCallbacks(execute=True):
            first = queue_email(
                "email_verification", "same@example.test", "Verify", "Link",
                idempotency_key="same-verification-token",
            )
        with self.captureOnCommitCallbacks(execute=True):
            second = queue_email(
                "email_verification", "same@example.test", "Verify again", "Different link",
                idempotency_key="same-verification-token",
            )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(BackgroundJob.objects.filter(kind="email", idempotency_key="same-verification-token").count(), 1)

    def test_verification_token_is_single_use(self):
        user = User.objects.create_user(
            username="verify-user",
            email="verify-user@example.test",
            password="A-Unique-Long-Password-934!",
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.email_verified = False
        profile.save(update_fields=["email_verified"])
        raw_token = "a-unique-test-verification-token"
        token = EmailVerificationToken.objects.create(
            user=user,
            token_hash=sha256(raw_token),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        first = self.client.post(
            "/api/auth/verify-email/",
            {"uid": user.pk, "token": raw_token},
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.data)
        profile.refresh_from_db()
        token.refresh_from_db()
        self.assertTrue(profile.email_verified)
        self.assertIsNotNone(token.used_at)

        second = self.client.post(
            "/api/auth/verify-email/",
            {"uid": user.pk, "token": raw_token},
            format="json",
        )
        self.assertEqual(second.status_code, 400)

    def test_expired_verification_token_is_rejected(self):
        user = User.objects.create_user(
            username="expired-verify-user",
            email="expired-verify@example.test",
            password="A-Unique-Long-Password-934!",
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        raw_token = "expired-test-verification-token"
        EmailVerificationToken.objects.create(
            user=user,
            token_hash=sha256(raw_token),
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.post(
            "/api/auth/verify-email/",
            {"uid": user.pk, "token": raw_token},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        profile.refresh_from_db()
        self.assertFalse(profile.email_verified)

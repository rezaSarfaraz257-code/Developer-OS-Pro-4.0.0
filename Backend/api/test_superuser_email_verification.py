from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from api.mature import MatureTokenObtainPairSerializer
from api.models import UserProfile


User = get_user_model()


@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class SuperuserEmailVerificationExemptionTests(TestCase):
    def test_unverified_superuser_can_obtain_tokens(self):
        user = User.objects.create_superuser(
            username="platform-admin",
            email="admin@example.test",
            password="Strong-Test-Password-123!",
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.email_verified = False
        profile.save(update_fields=["email_verified"])

        serializer = MatureTokenObtainPairSerializer(
            data={
                "username": user.username,
                "password": "Strong-Test-Password-123!",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIn("access", serializer.validated_data)
        self.assertIn("refresh", serializer.validated_data)

    def test_unverified_regular_user_still_requires_email_verification(self):
        user = User.objects.create_user(
            username="regular-member",
            email="member@example.test",
            password="Strong-Test-Password-123!",
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.email_verified = False
        profile.save(update_fields=["email_verified"])

        serializer = MatureTokenObtainPairSerializer(
            data={
                "username": user.username,
                "password": "Strong-Test-Password-123!",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("Verify your email before signing in.", str(serializer.errors))

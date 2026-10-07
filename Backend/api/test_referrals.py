from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Project, Referral, ReferralCode, ReferralReward, UserProfile, Task, Activity, ProductEvent
from .views import _plan_for, _qualify_referral


class ReferralProgramTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.referrer = User.objects.create_user(
            username="referrer",
            email="referrer@example.com",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(
            user=self.referrer,
            email_verified=True,
            email_verified_at=timezone.now(),
        )

    def test_referral_code_is_created_and_stable(self):
        self.client.force_authenticate(self.referrer)
        first = self.client.get("/api/referrals/")
        second = self.client.get("/api/referrals/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["code"], second.data["code"])
        self.assertEqual(len(first.data["code"]), 10)

    def test_unverified_referral_does_not_qualify(self):
        referred = User.objects.create_user(
            username="pending-user",
            email="pending@example.com",
            password="StrongPass123!",
        )
        Referral.objects.create(
            referrer=self.referrer,
            referred=referred,
            code=ReferralCode.objects.get_or_create(
                user=self.referrer,
                defaults={"code": "REFPENDING1"},
            )[0],
        )
        _qualify_referral(referred)
        self.assertEqual(Referral.objects.get(referred=referred).status, "pending")
        self.assertEqual(ReferralReward.objects.filter(user=self.referrer).count(), 0)

    def test_tenth_qualified_referral_unlocks_thirty_days(self):
        code = ReferralCode.objects.create(user=self.referrer, code="REF10ABCDE")
        for index in range(10):
            referred = User.objects.create_user(
                username=f"referred-{index}",
                email=f"referred-{index}@example.com",
                password="StrongPass123!",
            )
            UserProfile.objects.create(
                user=referred,
                email_verified=True,
                email_verified_at=timezone.now(),
            )
            referral = Referral.objects.create(
                referrer=self.referrer,
                referred=referred,
                code=code,
            )
            Referral.objects.filter(pk=referral.pk).update(
                attributed_at=timezone.now() - timedelta(days=7)
            )
            project = Project.objects.create(owner=referred, title=f"Qualified Project {index}")
            Task.objects.create(project=project, title="Real task 1")
            Task.objects.create(project=project, title="Real task 2")
            referred.date_joined = timezone.now() - timedelta(hours=73)
            referred.save(update_fields=["date_joined"])
            Activity.objects.create(actor=referred, verb="worked", message="day 1", created_at=timezone.now() - timedelta(days=3))
            Activity.objects.create(actor=referred, verb="worked", message="day 2", created_at=timezone.now() - timedelta(days=2))
            Activity.objects.create(actor=referred, verb="worked", message="day 3", created_at=timezone.now() - timedelta(days=1))
            for event_index in range(3):
                ProductEvent.objects.create(user=referred, name=f"referral-test-event-{index}-{event_index}", properties={})
            _qualify_referral(referred)

        reward = ReferralReward.objects.get(user=self.referrer, milestone=10)
        self.assertEqual(reward.plan, "pro")
        self.assertEqual(reward.duration_days, 30)
        self.assertEqual(reward.expires_at - reward.starts_at, timedelta(days=30))
        self.assertEqual(Referral.objects.filter(referrer=self.referrer, status="rewarded").count(), 10)
        self.assertEqual(_plan_for(self.referrer)[0], "pro")

    def test_qualified_referral_cannot_be_rewarded_twice(self):
        code = ReferralCode.objects.create(user=self.referrer, code="REFONCE123")
        referred = User.objects.create_user(
            username="once-user",
            email="once@example.com",
            password="StrongPass123!",
        )
        UserProfile.objects.create(
            user=referred,
            email_verified=True,
            email_verified_at=timezone.now(),
        )
        referral = Referral.objects.create(
            referrer=self.referrer,
            referred=referred,
            code=code,
        )
        Referral.objects.filter(pk=referral.pk).update(
            attributed_at=timezone.now() - timedelta(days=4)
        )
        project = Project.objects.create(owner=referred, title="Qualified Once")
        Task.objects.create(project=project, title="Real task 1")
        Task.objects.create(project=project, title="Real task 2")
        referred.date_joined = timezone.now() - timedelta(hours=73)
        referred.save(update_fields=["date_joined"])
        Activity.objects.create(actor=referred, verb="worked", message="day 1", created_at=timezone.now() - timedelta(days=3))
        Activity.objects.create(actor=referred, verb="worked", message="day 2", created_at=timezone.now() - timedelta(days=2))
        Activity.objects.create(actor=referred, verb="worked", message="day 3", created_at=timezone.now() - timedelta(days=1))
        for event_index in range(3):
            ProductEvent.objects.create(user=referred, name=f"referral-once-event-{event_index}", properties={})
        for _ in range(2):
            _qualify_referral(referred)
        self.assertEqual(Referral.objects.filter(pk=referral.pk, status="rewarded").count(), 1)
        self.assertEqual(ReferralReward.objects.filter(user=self.referrer).count(), 0)

    def test_registration_attributes_referral_code(self):
        self.client.force_authenticate(None)
        code = ReferralCode.objects.create(user=self.referrer, code="REGREF1234")
        response = self.client.post(
            "/api/register/",
            {
                "username": "new-referred",
                "email": "new-referred@example.com",
                "password": "StrongPass123!",
                "first_name": "New",
                "last_name": "Developer",
                "referral_code": code.code,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        referred = User.objects.get(username="new-referred")
        referral = Referral.objects.get(referred=referred)
        self.assertEqual(referral.referrer_id, self.referrer.id)
        self.assertEqual(referral.status, "pending")

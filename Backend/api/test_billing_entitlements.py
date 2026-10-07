from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Organization, OrganizationMembership, OrganizationSubscription
from .views import PLAN_LIMITS, _plan_for

User = get_user_model()


class BillingEntitlementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="billing-user",
            email="billing@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(self.user)

    def test_public_pricing_contract(self):
        response = self.client.get("/api/pricing/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["plans"]["pro"]["monthly_usd"], 29)
        self.assertEqual(data["plans"]["team"]["monthly_usd"], 15)
        self.assertEqual(data["plans"]["team"]["billing_model"], "per_seat")
        self.assertEqual(data["plans"]["enterprise"]["monthly_usd"], 299)
        self.assertEqual(set(data["entitlements"]), set(PLAN_LIMITS))

    def test_free_is_default(self):
        plan, _ = _plan_for(self.user)
        self.assertEqual(plan, "free")

    def test_team_plan_grants_member_entitlement(self):
        org = Organization.objects.create(owner=self.user, name="Acme", slug="acme", plan="team")
        OrganizationMembership.objects.create(organization=org, user=self.user, role="owner")
        OrganizationSubscription.objects.create(
            organization=org, plan="team", status="active", quantity=1
        )
        plan, _ = _plan_for(self.user)
        self.assertEqual(plan, "team")

    def test_enterprise_overrides_team(self):
        team = Organization.objects.create(owner=self.user, name="Team Org", slug="team-org", plan="team")
        OrganizationMembership.objects.create(organization=team, user=self.user, role="owner")
        OrganizationSubscription.objects.create(
            organization=team, plan="team", status="active", quantity=1
        )
        ent = Organization.objects.create(owner=self.user, name="Enterprise Org", slug="enterprise-org", plan="enterprise")
        OrganizationMembership.objects.create(organization=ent, user=self.user, role="owner")
        OrganizationSubscription.objects.create(
            organization=ent, plan="enterprise", status="active", quantity=1
        )
        plan, _ = _plan_for(self.user)
        self.assertEqual(plan, "enterprise")

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Organization, OrganizationMembership, OrganizationSubscription, Subscription
from .views import PLAN_FEATURES, PLAN_LIMITS, _plan_for

User = get_user_model()


class BillingEntitlementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="billing-user",
            email="billing@example.com",
            password="StrongPass123!",
        )
        Subscription.objects.create(user=self.user, plan="free", status="active")
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
        self.assertEqual(data["feature_matrix"], PLAN_FEATURES)

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


    def test_free_and_pro_cannot_create_organizations(self):
        for plan in ("free", "pro"):
            self.user.subscription.plan = plan
            self.user.subscription.status = "active"
            self.user.subscription.save(update_fields=["plan", "status", "updated_at"])
            response = self.client.post("/api/organizations/", {"name": f"{plan}-org"}, format="json")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "plan_upgrade_required")

    def test_team_can_create_organization(self):
        self.user.subscription.plan = "team"
        self.user.subscription.status = "active"
        self.user.subscription.save(update_fields=["plan", "status", "updated_at"])
        response = self.client.post("/api/organizations/", {"name": "Team Org"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["plan"], "team")


    def test_personal_billing_rejects_team_and_enterprise(self):
        for plan, code in (("team", "organization_billing_required"), ("enterprise", "enterprise_contact_sales")):
            response = self.client.post("/api/subscription/", {"plan": plan}, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], code)


    def test_free_cannot_read_audit_log(self):
        response = self.client.get("/api/audit/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "plan_upgrade_required")

    def test_team_can_read_audit_log(self):
        self.user.subscription.plan = "team"
        self.user.subscription.status = "active"
        self.user.subscription.save(update_fields=["plan", "status", "updated_at"])
        response = self.client.get("/api/audit/")
        self.assertEqual(response.status_code, 200)

    def test_team_seat_limit_is_enforced(self):
        from .models import Organization
        from .views import PLAN_LIMITS
        self.user.subscription.plan = "team"
        self.user.subscription.status = "active"
        self.user.subscription.save(update_fields=["plan", "status", "updated_at"])
        org = Organization.objects.create(owner=self.user, name="Seat Org", slug="seat-org", plan="team")
        OrganizationMembership.objects.create(organization=org, user=self.user, role="owner")
        second = User.objects.create_user(username="seat-user", email="seat@example.com", password="StrongPass123!")
        for n in range(PLAN_LIMITS["team"]["org_members"] - 1):
            u = User.objects.create_user(username=f"member-{n}", email=f"member-{n}@example.com", password="StrongPass123!")
            OrganizationMembership.objects.create(organization=org, user=u, role="developer")
        response = self.client.post(f"/api/organizations/{org.id}/members/", {"username": second.username}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "seat_limit_reached")

    def test_pricing_packaging_contract(self):
        from .views import PLAN_CATALOG
        self.assertTrue(PLAN_CATALOG["pro"]["recommended"])
        self.assertEqual(PLAN_CATALOG["pro"]["annual_usd"], 290)
        self.assertEqual(PLAN_CATALOG["team"]["annual_usd"], 150)
        self.assertEqual(PLAN_CATALOG["team"]["billing_model"], "per_seat")
        self.assertEqual(PLAN_CATALOG["enterprise"]["billing_model"], "custom")
        self.assertTrue(PLAN_CATALOG["enterprise"]["starting_at"])

    def test_every_plan_exposes_consistent_entitlements(self):
        from .views import PLAN_CATALOG
        self.assertEqual(set(PLAN_CATALOG), {"free", "pro", "team", "enterprise"})
        self.assertEqual(set(PLAN_CATALOG), set(PLAN_LIMITS))
        self.assertEqual(set(PLAN_CATALOG), set(PLAN_FEATURES))
        for plan in ("free", "pro", "team", "enterprise"):
            for value in PLAN_LIMITS[plan].values():
                self.assertGreaterEqual(value, 0)
            self.user.subscription.plan = plan
            self.user.subscription.status = "active"
            self.user.subscription.save(update_fields=["plan", "status", "updated_at"])
            usage = self.client.get("/api/usage/")
            self.assertEqual(usage.status_code, 200)
            self.assertEqual(usage.json()["plan"], plan)
            capabilities = self.client.get("/api/ide/capabilities/")
            self.assertEqual(capabilities.status_code, 200)
            self.assertEqual(capabilities.json()["plan"], plan)

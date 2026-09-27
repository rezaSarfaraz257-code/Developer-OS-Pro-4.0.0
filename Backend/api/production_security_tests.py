from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Project, CodeWorkspace, BackgroundJob, Organization, OrganizationMembership
from .security import can_access_project, can_manage_project


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="owner", password="Strong-password-12345")
        self.other = User.objects.create_user(username="other", password="Strong-password-12345")
        self.project = Project.objects.create(owner=self.owner, title="Private")
        self.client.force_authenticate(self.other)

    def test_project_object_isolation(self):
        self.assertFalse(can_access_project(self.project, self.other))
        self.assertFalse(can_manage_project(self.project, self.other))
        self.assertEqual(self.client.get(f"/api/projects/{self.project.pk}/").status_code, 404)

    def test_workspace_isolation(self):
        ws = CodeWorkspace.objects.create(owner=self.owner, project=self.project, name="private")
        self.assertEqual(self.client.get(f"/api/ide/workspaces/{ws.pk}/files/").status_code, 404)


class DurableJobTests(TestCase):
    def test_idempotent_job_key(self):
        first = BackgroundJob.objects.create(kind="email", idempotency_key="verification:1", payload={"x": 1})
        second, created = BackgroundJob.objects.get_or_create(kind="email", idempotency_key="verification:1", defaults={"payload": {"x": 2}})
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)


class OrganizationIsolationTests(TestCase):
    def test_membership_is_explicit(self):
        owner = User.objects.create_user(username="org-owner", password="Strong-password-12345")
        stranger = User.objects.create_user(username="stranger", password="Strong-password-12345")
        org = Organization.objects.create(owner=owner, name="Acme", slug="acme")
        OrganizationMembership.objects.create(organization=org, user=owner, role="owner")
        self.assertTrue(org.memberships.filter(user=owner).exists())
        self.assertFalse(org.memberships.filter(user=stranger).exists())

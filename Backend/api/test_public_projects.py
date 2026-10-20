from django.contrib.auth.models import User
from rest_framework.test import APITestCase
from rest_framework import status

from .models import Project


class PublicProjectGalleryTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="project-owner", password="test-password")
        self.viewer = User.objects.create_user(username="project-viewer", password="test-password")
        self.public_project = Project.objects.create(
            owner=self.owner,
            title="Public sample",
            description="A project intended for the gallery",
            category="Frontend",
            is_public=True,
        )
        self.private_project = Project.objects.create(
            owner=self.owner,
            title="Private sample",
            description="Must never appear in Explore",
            category="Backend",
            is_public=False,
        )

    def test_gallery_requires_authentication(self):
        response = self.client.get("/api/projects/public/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_gallery_only_returns_explicitly_published_projects(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/projects/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], [self.public_project.id])
        self.assertEqual(response.data[0]["owner_username"], self.owner.username)
        self.assertNotIn("email", response.data[0])

    def test_gallery_search_is_bounded_and_matches_project_metadata(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/projects/public/?q=sample")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], [self.public_project.id])

    def test_only_project_owner_can_change_visibility(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.patch(
            f"/api/projects/{self.public_project.id}/",
            {"is_public": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.public_project.refresh_from_db()
        self.assertTrue(self.public_project.is_public)

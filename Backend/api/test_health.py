import json
import os
from unittest import TestCase
from unittest.mock import patch

from django.test import RequestFactory
from django.urls import reverse

from .operational_health import liveness_api


class LivenessApiTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_liveness_route_is_registered_in_the_public_urlconf(self):
        # View-unit tests alone cannot catch a missing Django URL registration.
        self.assertEqual(reverse("liveness"), "/api/health/live/")

    def test_explicit_project_level_liveness_route_uses_same_public_path(self):
        # Keep a root URLconf alias for production routers/orchestrators to audit.
        self.assertEqual(reverse("liveness-root"), "/api/health/live/")


    def test_public_liveness_url_is_reachable_through_django_client(self):
        # Integration coverage: verify URL routing and response contract together.
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "integration-test"}, clear=False):
            response = self.client.get("/api/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response.json(), {
            "status": "alive",
            "service": "developer-os-api",
            "version": "4.0.0",
            "release_sha": "integration-test",
        })

    def test_liveness_returns_process_contract_and_no_store_cache(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "abc123"}, clear=False):
            response = liveness_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(json.loads(response.content), {
            "status": "alive",
            "service": "developer-os-api",
            "version": "4.0.0",
            "release_sha": "abc123",
        })

    def test_liveness_has_safe_defaults(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {}, clear=True):
            response = liveness_api(request)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["version"], "4.0.0")
        self.assertEqual(payload["release_sha"], "unknown")

    def test_liveness_rejects_non_get_requests(self):
        request = self.factory.post("/api/health/live/")
        response = liveness_api(request)
        self.assertEqual(response.status_code, 405)

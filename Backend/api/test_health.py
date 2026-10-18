import json
import os
from unittest import TestCase
from unittest.mock import patch

from django.test import Client as DjangoTestClient, RequestFactory
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
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "", "RENDER_GIT_COMMIT": "integration-test"}, clear=False):
            response = DjangoTestClient().get("/api/health/live/")

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
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "abc123", "RENDER_GIT_COMMIT": "render-commit"}, clear=False):
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



class SharedReleaseIdentityTests(TestCase):
    def test_all_health_routes_expose_the_same_release_identity_without_caching(self):
        expected = {"version": "4.0.0", "release_sha": "abcdef1234567"}
        env = {"RELEASE_VERSION": expected["version"], "RELEASE_SHA": expected["release_sha"]}
        with patch.dict(os.environ, env, clear=False):
            client = DjangoTestClient()
            responses = [
                client.get("/api/health/live/"),
                client.get("/api/health/?format=json"),
                client.get("/api/health/ready/?format=json"),
            ]

        # Readiness may correctly return 503 when the production worker is absent.
        # Release identity and no-store headers must remain available even then.
        self.assertEqual(responses[0].status_code, 200)  # liveness is independent
        self.assertEqual(responses[1].status_code, 200)  # database health
        self.assertIn(responses[2].status_code, (200, 503))
        for response in responses:
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            payload = response.json()
            self.assertEqual(payload["version"], expected["version"])
            self.assertEqual(payload["release_sha"], expected["release_sha"])

    def test_release_sha_uses_render_commit_fallback_across_health_routes(self):
        env = {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "", "RENDER_GIT_COMMIT": "fedcba987654321"}
        with patch.dict(os.environ, env, clear=False):
            responses = [
                DjangoTestClient().get("/api/health/live/"),
                DjangoTestClient().get("/api/health/?format=json"),
                DjangoTestClient().get("/api/health/ready/?format=json"),
            ]

        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[1].status_code, 200)
        self.assertIn(responses[2].status_code, (200, 503))
        for response in responses:
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(response.json()["release_sha"], "fedcba987654321")

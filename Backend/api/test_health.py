import json
import os
from unittest import TestCase
from unittest.mock import patch

from django.test import Client as DjangoTestClient, RequestFactory
from django.urls import reverse

from .operational_health import liveness_api
from .views import readiness_api


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
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "", "RENDER_GIT_COMMIT": "a" * 40}, clear=False):
            response = DjangoTestClient().get("/api/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["Expires"], "0")
        self.assertEqual(response.json(), {
            "status": "alive",
            "service": "developer-os-api",
            "version": "4.0.0",
            "release_sha": "a" * 40,
        })

    def test_liveness_returns_process_contract_and_no_store_cache(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "abc1234", "RENDER_GIT_COMMIT": ""}, clear=False):
            response = liveness_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(json.loads(response.content), {
            "status": "alive",
            "service": "developer-os-api",
            "version": "4.0.0",
            "release_sha": "abc1234",
        })

    def test_render_commit_overrides_stale_configured_release_sha(self):
        request = self.factory.get("/api/health/live/")
        render_sha = "d" * 40
        with patch.dict(os.environ, {
            "RELEASE_VERSION": "4.0.0",
            "RELEASE_SHA": "9e860bba6603918533a8b90dc0db2f11166793dc",
            "RENDER_GIT_COMMIT": render_sha,
        }, clear=False):
            response = liveness_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["release_sha"], render_sha)

    def test_configured_release_sha_is_fallback_when_render_sha_is_invalid(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {
            "RELEASE_VERSION": "4.0.0",
            "RELEASE_SHA": "abc1234",
            "RENDER_GIT_COMMIT": "not-a-commit",
        }, clear=False):
            response = liveness_api(request)

        self.assertEqual(json.loads(response.content)["release_sha"], "abc1234")

    def test_liveness_has_safe_defaults(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {}, clear=True):
            response = liveness_api(request)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["version"], "4.0.0")
        self.assertEqual(payload["release_sha"], "unknown")


    def test_health_response_is_not_cached_and_shares_release_identity(self):
        # Health responses must not be cached independently of liveness.
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "abc1234", "RENDER_GIT_COMMIT": ""}, clear=False):
            with patch("api.views.connection.cursor") as cursor_factory:
                cursor = cursor_factory.return_value.__enter__.return_value
                cursor.fetchone.return_value = (1,)
                response = DjangoTestClient().get("/api/health/?format=json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["Expires"], "0")
        self.assertEqual(response.json()["version"], "4.0.0")
        self.assertEqual(json.loads(response.content)["release_sha"], "abc1234")

    def test_liveness_rejects_non_get_requests(self):
        request = self.factory.post("/api/health/live/")
        response = liveness_api(request)
        self.assertEqual(response.status_code, 405)

    def test_invalid_configured_release_sha_falls_back_to_unknown(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {
            "RELEASE_VERSION": "4.0.0",
            "RELEASE_SHA": "not-a-commit",
            "RENDER_GIT_COMMIT": "",
        }, clear=False):
            response = liveness_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["release_sha"], "unknown")

    def test_short_or_non_hex_configured_release_sha_is_rejected(self):
        request = self.factory.get("/api/health/live/")
        for invalid_sha in ("", "abc", "123456g", "a" * 41, "unknown"):
            with self.subTest(release_sha=invalid_sha):
                with patch.dict(os.environ, {
                    "RELEASE_VERSION": "4.0.0",
                    "RELEASE_SHA": invalid_sha,
                    "RENDER_GIT_COMMIT": "",
                }, clear=False):
                    response = liveness_api(request)
                self.assertEqual(json.loads(response.content)["release_sha"], "unknown")

    def test_valid_configured_release_sha_is_normalized_by_whitespace(self):
        request = self.factory.get("/api/health/live/")
        configured_sha = "a" * 40
        with patch.dict(os.environ, {
            "RELEASE_VERSION": "4.0.0",
            "RELEASE_SHA": f"  {configured_sha}  ",
            "RENDER_GIT_COMMIT": "",
        }, clear=False):
            response = liveness_api(request)

        self.assertEqual(json.loads(response.content)["release_sha"], configured_sha)


class SharedReleaseIdentityIntegrationTests(TestCase):
    def test_all_public_health_routes_share_release_identity_and_disable_caching(self):
        render_sha = "a" * 40
        with patch.dict(os.environ, {
            "RELEASE_VERSION": "4.0.0",
            "RELEASE_SHA": "b" * 40,
            "RENDER_GIT_COMMIT": render_sha,
        }, clear=False):
            client = DjangoTestClient()
            responses = [
                client.get("/api/health/live/"),
                client.get("/api/health/?format=json"),
                client.get("/api/health/ready/?format=json"),
            ]

        # Readiness may correctly fail closed when its required worker/config
        # is missing; that must not suppress the diagnostic release identity.
        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[1].status_code, 200)
        self.assertIn(responses[2].status_code, (200, 503))
        for response in responses:
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(response["Pragma"], "no-cache")
            self.assertEqual(response["Expires"], "0")
            self.assertEqual(response.json()["version"], "4.0.0")
            self.assertEqual(response.json()["release_sha"], render_sha)


class ReadinessDebugRuntimeRegressionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        DEBUG=True,
        IS_PRODUCTION=True,
        SECRET_KEY="test-secret-key-that-is-not-insecure",
        ALLOWED_HOSTS=["example.com"],
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.example.com",
    )
    def test_debug_does_not_bypass_production_checks_and_worker_is_explicitly_optional(self):
        # A platform deployment must keep production dependency checks strict
        # even if DEBUG is accidentally enabled. The worker itself remains an
        # explicit gate so API-only deployments can start without fake health.
        with (
            patch.dict(os.environ, {
                "RENDER_SERVICE_ID": "srv-readiness-regression",
                "RENDER_GIT_COMMIT": "a" * 40,
                "REQUIRE_BACKGROUND_WORKER": "",
            }, clear=False),
            patch("api.views.connection.cursor") as cursor_factory,
            patch("django.core.cache.cache.set"),
            patch("django.core.cache.cache.get", return_value="ok"),
            patch("api.models.WorkerHeartbeat.objects.filter") as heartbeat_filter,
            patch("api.models.BackgroundJob.objects.filter") as jobs_filter,
        ):
            cursor = cursor_factory.return_value.__enter__.return_value
            cursor.fetchone.return_value = (1,)
            heartbeat_filter.return_value.exists.return_value = False
            jobs_filter.return_value.exists.return_value = False

            optional_response = readiness_api(self.factory.get("/api/health/ready/"))
            self.assertEqual(optional_response.status_code, 200)
            self.assertFalse(optional_response.data["checks"]["worker"])
            self.assertFalse(optional_response.data["worker_required"])
            self.assertEqual(optional_response.data["background_processing"], "unavailable")

            os.environ["REQUIRE_BACKGROUND_WORKER"] = "true"
            required_response = readiness_api(self.factory.get("/api/health/ready/"))
            self.assertEqual(required_response.status_code, 503)
            self.assertFalse(required_response.data["checks"]["worker"])
            self.assertTrue(required_response.data["worker_required"])

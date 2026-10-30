import json
import os
import requests
from unittest import TestCase
from unittest.mock import patch

from django.test import Client as DjangoTestClient, RequestFactory, override_settings
from django.urls import reverse

from .operational_health import liveness_api
from .views import _secure_runner_ready


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

class RenderReadinessFailClosedRegressionTests(TestCase):
    @override_settings(DEBUG=True)
    def test_render_readiness_does_not_bypass_missing_worker_when_debug_is_true(self):
        # Platform runtime markers must keep readiness strict even if DEBUG is accidentally enabled.
        with patch.dict(os.environ, {
            "RENDER_SERVICE_ID": "srv-readiness-regression",
            "RENDER_GIT_COMMIT": "a" * 40,
            "REQUIRE_BACKGROUND_WORKER": "",
        }, clear=False):
            response = DjangoTestClient().get("/api/health/ready/?format=json")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["checks"]["worker"])
        self.assertEqual(payload["release_sha"], "a" * 40)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")


    @override_settings(
        DEBUG=True,
        IS_PRODUCTION=False,
        SECRET_KEY="a-valid-test-secret-key",
        ALLOWED_HOSTS=["*", "localhost"],
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        EMAIL_HOST="",
        DATABASE_URL="",
        CACHE_URL="",
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_render_runtime_fails_closed_on_development_dependency_fallbacks(self):
        # Platform markers must enforce production dependencies even when
        # ENVIRONMENT/DEBUG are accidentally left in development mode.
        with patch.dict(os.environ, {
            "RENDER_SERVICE_ID": "srv-strict-readiness-regression",
            "RENDER_GIT_COMMIT": "b" * 40,
            "DATABASE_URL": "",
            "CACHE_URL": "",
            "REQUIRE_BACKGROUND_WORKER": "",
        }, clear=False):
            with patch("api.views.connection.cursor") as cursor_factory:
                cursor = cursor_factory.return_value.__enter__.return_value
                cursor.fetchone.return_value = (1,)
                response = DjangoTestClient().get("/api/health/ready/?format=json")

        self.assertEqual(response.status_code, 503)
        checks = response.json()["checks"]
        self.assertFalse(checks["database"])
        self.assertFalse(checks["allowed_hosts"])
        self.assertFalse(checks["email"])
        self.assertFalse(checks["cache"])
        self.assertFalse(checks["worker"])



class SecureRunnerReadinessTests(TestCase):
    @patch("api.views.RUNNER_URL", "http://runner:8080")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    @patch("api.views.requests.get")
    def test_accepts_only_live_bubblewrap_with_network_disabled(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = {
            "status": "ok",
            "execution_available": True,
            "sandbox_backend": "bwrap",
            "sandbox": "bubblewrap",
            "network_policy": "isolated-by-default",
        }

        self.assertTrue(_secure_runner_ready())
        get.assert_called_once_with(
            "http://runner:8080/health",
            timeout=(2, 4),
            allow_redirects=False,
        )

    @patch("api.views.RUNNER_URL", "http://runner:8080")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    @patch("api.views.requests.get")
    def test_rejects_unavailable_or_compatibility_runner(self, get):
        unsafe_payloads = (
            {"execution_available": False, "sandbox_backend": "bwrap", "sandbox": "bubblewrap", "network_policy": "isolated-by-default"},
            {"execution_available": True, "sandbox_backend": "container", "sandbox": "container-native", "network_policy": "container-runtime-policy"},
            {"execution_available": True, "sandbox_backend": "bwrap", "sandbox": "bubblewrap", "network_policy": "provisioning-network"},
        )
        for payload in unsafe_payloads:
            with self.subTest(payload=payload):
                get.return_value.status_code = 200
                get.return_value.json.return_value = payload
                self.assertFalse(_secure_runner_ready())

    @patch("api.views.RUNNER_URL", "http://runner:8080")
    @patch("api.views.RUNNER_TOKEN", "")
    @patch("api.views.requests.get")
    def test_rejects_missing_control_plane_token_without_network_call(self, get):
        self.assertFalse(_secure_runner_ready())
        get.assert_not_called()

    @patch("api.views.RUNNER_URL", "http://runner:8080")
    @patch("api.views.RUNNER_TOKEN", "test-runner-token")
    @patch("api.views.requests.get", side_effect=requests.RequestException("offline"))
    def test_runner_network_failure_fails_closed(self, _get):
        self.assertFalse(_secure_runner_ready())

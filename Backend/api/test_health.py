import os
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test import Client


class LivenessApiTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def test_liveness_returns_release_identity_without_cache(self):
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.2.1", "RELEASE_SHA": "abc123"}):
            response = self.client.get("/api/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "alive",
            "service": "developer-os-api",
            "version": "4.2.1",
            "release_sha": "abc123",
        })
        self.assertIn("no-store", response["Cache-Control"])

    def test_liveness_uses_safe_defaults_when_release_metadata_is_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RELEASE_VERSION", None)
            os.environ.pop("RELEASE_SHA", None)
            response = self.client.get("/api/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "4.0.0")
        self.assertEqual(response.json()["release_sha"], "unknown")

    def test_liveness_rejects_non_get_requests(self):
        response = self.client.post("/api/health/live/")
        self.assertEqual(response.status_code, 405)

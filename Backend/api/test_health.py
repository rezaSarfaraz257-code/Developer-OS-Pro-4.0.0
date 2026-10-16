import os
from unittest import TestCase
from unittest.mock import patch

from django.test import RequestFactory

from .operational_health import liveness_api


class LivenessApiTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_liveness_returns_process_contract_and_no_store_cache(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {"RELEASE_VERSION": "4.0.0", "RELEASE_SHA": "abc123"}, clear=False):
            response = liveness_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response.json(), {
            "status": "alive",
            "service": "developer-os-api",
            "version": "4.0.0",
            "release_sha": "abc123",
        })

    def test_liveness_has_safe_defaults(self):
        request = self.factory.get("/api/health/live/")
        with patch.dict(os.environ, {}, clear=True):
            response = liveness_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "4.0.0")
        self.assertEqual(response.json()["release_sha"], "unknown")

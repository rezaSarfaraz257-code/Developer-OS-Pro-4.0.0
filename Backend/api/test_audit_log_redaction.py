from unittest.mock import patch

from django.test import SimpleTestCase

from api.security import _sanitize_audit_value, audit_security_event


class AuditMetadataRedactionTests(SimpleTestCase):
    def test_redacts_secret_fields_recursively_inside_dicts_and_lists(self):
        metadata = {
            "event": "oauth.callback",
            "provider": {
                "name": "github",
                "access_token": "must-not-persist",
                "nested": [{"Authorization": "Bearer must-not-persist", "status": "ok"}],
            },
            "headers": {
                "Cookie": "session=must-not-persist",
                "X-Request-ID": "req-123",
            },
            "client_secret": "must-not-persist",
        }

        with patch("api.security.AuditLog.objects.create") as create:
            audit_security_event(None, "oauth.callback", metadata=metadata)

        saved = create.call_args.kwargs["metadata"]
        self.assertEqual(saved["event"], "oauth.callback")
        self.assertEqual(saved["provider"]["name"], "github")
        self.assertEqual(saved["provider"]["nested"], [{"status": "ok"}])
        self.assertEqual(saved["headers"], {"X-Request-ID": "req-123"})
        self.assertNotIn("client_secret", saved)
        self.assertNotIn("must-not-persist", repr(saved))

    def test_redacts_variants_of_secret_field_names(self):
        result = _sanitize_audit_value({
            "refresh-token": "a",
            "apiKey": "b",
            "db_password": "c",
            "safe_count": 3,
        })
        self.assertEqual(result, {"safe_count": 3})

    def test_bounds_nested_values_and_collection_sizes(self):
        result = _sanitize_audit_value({
            "text": "x" * 700,
            "items": list(range(75)),
            "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": "hidden"}}}}}}},
        })
        self.assertEqual(len(result["text"]), 500)
        self.assertEqual(len(result["items"]), 50)
        self.assertEqual(result["deep"]["a"]["b"]["c"]["d"]["e"]["f"], "[truncated]")

    def test_non_dictionary_metadata_is_discarded(self):
        with patch("api.security.AuditLog.objects.create") as create:
            audit_security_event(None, "event", metadata=["unexpected", "shape"])
        self.assertEqual(create.call_args.kwargs["metadata"], {})

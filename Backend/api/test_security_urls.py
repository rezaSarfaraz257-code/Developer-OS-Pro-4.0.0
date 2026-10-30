from unittest.mock import patch

from django.test import SimpleTestCase

from api.security import validate_remote_url


class OutboundURLValidationTests(SimpleTestCase):
    def test_rejects_embedded_username(self):
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            validate_remote_url("https://user@example.com/resource")

    def test_rejects_embedded_password(self):
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            validate_remote_url("https://user:secret@example.com/resource")

    def test_rejects_private_dns_resolution(self):
        with patch("api.security.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.0.0.8", 443)),
        ]):
            with self.assertRaisesRegex(ValueError, "Private or local"):
                validate_remote_url("https://example.com/resource")

    def test_rejects_loopback_dns_resolution(self):
        with patch("api.security.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with self.assertRaisesRegex(ValueError, "Private or local"):
                validate_remote_url("https://example.com/resource")

    def test_allows_public_dns_resolution(self):
        with patch("api.security.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]):
            self.assertEqual(
                validate_remote_url("https://example.com/resource"),
                "https://example.com/resource",
            )

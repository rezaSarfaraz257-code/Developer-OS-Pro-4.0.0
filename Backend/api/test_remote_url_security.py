from unittest.mock import patch

from django.test import SimpleTestCase

from api.security import validate_remote_url


def dns_result(address):
    return (2, 1, 6, "", (address, 443))


class RemoteURLValidationTests(SimpleTestCase):
    @patch("api.security.socket.getaddrinfo", return_value=[dns_result("8.8.8.8")])
    def test_accepts_public_https_destination(self, resolve):
        self.assertEqual(
            validate_remote_url("https://example.test/resource"),
            "https://example.test/resource",
        )
        resolve.assert_called_once()

    def test_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            validate_remote_url("https://user:password@example.test/")

    def test_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "port is invalid"):
            validate_remote_url("https://example.test:99999/")

    def test_rejects_control_characters(self):
        with self.assertRaisesRegex(ValueError, "control characters"):
            validate_remote_url("https://example.test/\r\nHost: internal")

    @patch("api.security.socket.getaddrinfo", return_value=[dns_result("127.0.0.1")])
    def test_rejects_loopback_resolution(self, _resolve):
        with self.assertRaisesRegex(ValueError, "non-global"):
            validate_remote_url("https://example.test/")

    @patch("api.security.socket.getaddrinfo", return_value=[dns_result("::ffff:127.0.0.1")])
    def test_rejects_ipv4_mapped_ipv6_loopback(self, _resolve):
        with self.assertRaisesRegex(ValueError, "non-global"):
            validate_remote_url("https://example.test/")

    @patch("api.security.socket.getaddrinfo", return_value=[dns_result("10.1.2.3")])
    def test_rejects_private_network_resolution(self, _resolve):
        with self.assertRaisesRegex(ValueError, "non-global"):
            validate_remote_url("https://example.test/")

    @patch("api.security.socket.getaddrinfo", return_value=[dns_result("8.8.8.8")])
    def test_host_allowlist_is_exact(self, _resolve):
        with self.assertRaisesRegex(ValueError, "host is not allowed"):
            validate_remote_url(
                "https://attacker.example/",
                allow_hosts={"trusted.example"},
            )

    @patch("api.security.socket.getaddrinfo", side_effect=OSError("resolver unavailable"))
    def test_fails_closed_when_resolution_fails(self, _resolve):
        with self.assertRaisesRegex(ValueError, "could not be resolved"):
            validate_remote_url("https://example.test/")

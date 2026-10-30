from django.test import SimpleTestCase

from api.websocket_auth import APP_SUBPROTOCOL, extract_websocket_token


class WebSocketTokenTransportTests(SimpleTestCase):
    def test_extracts_single_bounded_token_from_subprotocol(self):
        scope = {"subprotocols": [APP_SUBPROTOCOL, "auth.eyJhbGciOiJIUzI1NiJ9.payload.signature"]}
        self.assertEqual(
            extract_websocket_token(scope),
            "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        )

    def test_query_string_token_is_not_an_authentication_source(self):
        scope = {
            "query_string": b"token=eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "subprotocols": [APP_SUBPROTOCOL],
        }
        self.assertIsNone(extract_websocket_token(scope))

    def test_rejects_missing_duplicate_and_malformed_tokens(self):
        invalid_scopes = (
            {"subprotocols": [APP_SUBPROTOCOL]},
            {"subprotocols": [APP_SUBPROTOCOL, "auth.first", "auth.second"]},
            {"subprotocols": [APP_SUBPROTOCOL, "auth.bad token"]},
            {"subprotocols": [APP_SUBPROTOCOL, "auth." + "a" * 8193]},
        )
        for scope in invalid_scopes:
            with self.subTest(scope=scope):
                self.assertIsNone(extract_websocket_token(scope))

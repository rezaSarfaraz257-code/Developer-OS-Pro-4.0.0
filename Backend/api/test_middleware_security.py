from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from api.middleware import RequestObservabilityMiddleware


class RequestIdValidationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _response_for(self, request_id):
        request = self.factory.get(
            "/api/status/",
            **({"HTTP_X_REQUEST_ID": request_id} if request_id is not None else {}),
        )
        middleware = RequestObservabilityMiddleware(lambda _request: HttpResponse("ok"))
        return middleware(request)

    def test_accepts_short_safe_request_id(self):
        response = self._response_for("client-trace_01.ab")
        self.assertEqual(response["X-Request-ID"], "client-trace_01.ab")

    def test_replaces_overlong_request_id(self):
        response = self._response_for("x" * 65)
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_replaces_request_id_with_unsupported_characters(self):
        response = self._response_for("trace/with spaces")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_generates_request_id_when_header_is_missing(self):
        response = self._response_for(None)
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

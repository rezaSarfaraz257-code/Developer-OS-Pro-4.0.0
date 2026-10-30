from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from api.middleware import ApiSecurityHeadersMiddleware, RequestObservabilityMiddleware


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
        response = self._response_for("trace/with spaces\r\nInjected: true")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_generates_request_id_when_header_is_missing(self):
        response = self._response_for(None)
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")


class ApiSecurityHeadersTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_api_responses_include_required_security_headers(self):
        request = self.factory.get("/api/health/")
        middleware = ApiSecurityHeadersMiddleware(lambda _request: HttpResponse("ok"))

        response = middleware(request)

        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])

    def test_non_api_responses_are_not_given_api_only_policy(self):
        request = self.factory.get("/")
        middleware = ApiSecurityHeadersMiddleware(lambda _request: HttpResponse("ok"))

        response = middleware(request)

        self.assertNotIn("X-Frame-Options", response)
        self.assertNotIn("Referrer-Policy", response)


    def test_api_middleware_preserves_endpoint_specific_csp_and_referrer_policy(self):
        request = self.factory.get("/api/ide/workspaces/1/preview/index.html")

        def preview_response(_request):
            response = HttpResponse("preview")
            response["Content-Security-Policy"] = "sandbox allow-scripts allow-forms"
            response["Referrer-Policy"] = "no-referrer"
            return response

        middleware = ApiSecurityHeadersMiddleware(preview_response)
        response = middleware(request)

        self.assertEqual(response["Content-Security-Policy"], "sandbox allow-scripts allow-forms")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

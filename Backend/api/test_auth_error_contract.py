from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from .security import api_exception_handler


class ApiValidationErrorMessageTests(SimpleTestCase):
    def setUp(self):
        self.request = APIRequestFactory().post("/api/token/", {}, format="json")

    def test_non_field_login_error_is_not_replaced_with_generic_message(self):
        response = api_exception_handler(
            ValidationError("Invalid credentials."),
            {"request": self.request},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"]["message"],
            "Invalid credentials.",
        )

    def test_field_validation_messages_remain_actionable(self):
        response = api_exception_handler(
            ValidationError({"username": ["This field is required."]}),
            {"request": self.request},
        )

        self.assertEqual(
            response.data["error"]["message"],
            "username: This field is required.",
        )
        self.assertIn("username", response.data["error"]["details"])

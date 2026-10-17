import logging
from unittest import TestCase

from .logging import JsonLogFormatter


class JsonLogFormatterTests(TestCase):
    def setUp(self):
        self.formatter = JsonLogFormatter()

    def test_redacts_common_secret_bearing_fields_and_bearer_tokens(self):
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="login failed password=super-secret api_key=sk-live-example Authorization: Bearer abc.def.ghi token=refresh-secret",
            args=(),
            exc_info=None,
        )

        output = self.formatter.format(record)

        self.assertNotIn("super-secret", output)
        self.assertNotIn("sk-live-example", output)
        self.assertNotIn("abc.def.ghi", output)
        self.assertNotIn("refresh-secret", output)
        self.assertIn("[REDACTED]", output)

    def test_preserves_request_correlation_fields(self):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="request completed",
            args=(),
            exc_info=None,
        )
        record.request_id = "req_123"
        record.trace_id = "trace_456"
        record.span_id = "span_789"

        output = self.formatter.format(record)

        self.assertIn('"request_id":"req_123"', output)
        self.assertIn('"trace_id":"trace_456"', output)
        self.assertIn('"span_id":"span_789"', output)

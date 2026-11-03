import io
import json
import unittest
from types import SimpleNamespace

from lsp_engine import LSPError, LSPSession


def _frame(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


class LSPMessageFramingTests(unittest.TestCase):
    def test_notification_frames_use_crlf_and_preserve_consecutive_messages(self):
        first = {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": "file:///a.py"}}
        second = {"jsonrpc": "2.0", "method": "window/logMessage", "params": {"message": "ready"}}
        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=io.BytesIO(_frame(first) + _frame(second)))

        self.assertEqual(session._read_message(), first)
        self.assertEqual(session._read_message(), second)
        self.assertIsNone(session._read_message())

    def test_truncated_lsp_body_fails_closed(self):
        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=io.BytesIO(b"Content-Length: 12\r\n\r\nshort"))

        with self.assertRaisesRegex(LSPError, "truncated"):
            session._read_message()


if __name__ == "__main__":
    unittest.main()

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


    def test_missing_or_duplicate_content_length_is_rejected(self):
        for frame in (
            b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n{}",
            b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        ):
            session = LSPSession(".", [])
            session.proc = SimpleNamespace(stdout=io.BytesIO(frame))
            with self.subTest(frame=frame), self.assertRaisesRegex(LSPError, "Content-Length"):
                session._read_message()

    def test_partial_header_is_not_treated_as_clean_eof(self):
        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=io.BytesIO(b"Content-Length: 2\r\n"))
        with self.assertRaisesRegex(LSPError, "headers were truncated"):
            session._read_message()

    def test_invalid_json_and_non_object_payloads_are_rejected(self):
        for body in (b"{", b"[]"):
            frame = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
            session = LSPSession(".", [])
            session.proc = SimpleNamespace(stdout=io.BytesIO(frame))
            with self.subTest(body=body), self.assertRaises(LSPError):
                session._read_message()

    def test_oversized_header_and_body_are_rejected(self):
        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=io.BytesIO(b"X" * 65537))
        with self.assertRaisesRegex(LSPError, "headers exceed"):
            session._read_message()

        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=io.BytesIO(
            b"Content-Length: 33554433\r\n\r\n"
        ))
        with self.assertRaisesRegex(LSPError, "body exceeds"):
            session._read_message()


if __name__ == "__main__":
    unittest.main()

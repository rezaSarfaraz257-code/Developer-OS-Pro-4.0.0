import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lsp_engine import LSPError, LSPSession


def _frame(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


class _ShortReadStream:
    def __init__(self, data, chunk_size=2):
        self.data = io.BytesIO(data)
        self.chunk_size = chunk_size

    def read(self, size=-1):
        return self.data.read(min(size, self.chunk_size) if size >= 0 else self.chunk_size)


class _ShortWriteStream:
    def __init__(self, chunk_size=3):
        self.data = bytearray()
        self.chunk_size = chunk_size

    def write(self, value):
        chunk = bytes(value[:self.chunk_size])
        self.data.extend(chunk)
        return len(chunk)

    def flush(self):
        pass


class LSPMessageFramingTests(unittest.TestCase):
    def test_subprocess_stdout_is_unbuffered_for_select_based_readiness(self):
        session = LSPSession(".", [])
        process = SimpleNamespace(stdin=Mock(), stdout=Mock())
        with patch("lsp_engine.subprocess.Popen", return_value=process) as popen:
            session.request = Mock(return_value={"result": {}})
            session.notify = Mock()
            session.start()

        self.assertEqual(popen.call_args.kwargs["bufsize"], 0)

    def test_message_body_reads_until_content_length_even_with_short_pipe_reads(self):
        message = {"jsonrpc": "2.0", "method": "window/logMessage", "params": {"message": "ready"}}
        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=_ShortReadStream(_frame(message), chunk_size=2))

        self.assertEqual(session._read_message(), message)

    def test_send_completes_partial_pipe_writes(self):
        session = LSPSession(".", [])
        stream = _ShortWriteStream(chunk_size=3)
        session.proc = SimpleNamespace(stdin=stream)

        session._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

        expected = _frame({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        self.assertEqual(bytes(stream.data), expected)

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

    def test_non_ascii_headers_are_rejected_as_protocol_errors(self):
        session = LSPSession(".", [])
        session.proc = SimpleNamespace(stdout=io.BytesIO(
            b"X-Invalid: \xff\r\nContent-Length: 2\r\n\r\n{}"
        ))
        with self.assertRaisesRegex(LSPError, "headers must be ASCII"):
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

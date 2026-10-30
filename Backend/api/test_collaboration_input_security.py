from django.test import SimpleTestCase

from api.workspace_validation import (
    MAX_FILE_BYTES,
    MAX_PRESENCE_BYTES,
    bounded_json_object,
    validate_client_id,
    validate_workspace_path,
    validate_workspace_text,
    validate_workspace_total,
    validate_websocket_message,
)


class CollaborationInputValidationTests(SimpleTestCase):
    def test_rejects_traversal_absolute_and_git_metadata_paths(self):
        invalid = (
            "../secret.py",
            "src/../../secret.py",
            "/etc/passwd",
            r"C:\Windows\system.ini",
            r"src\..\secret.py",
            ".git/config",
            "src/.GIT/config",
            "src//main.py",
            "src/./main.py",
            "src/\x00main.py",
        )
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    validate_workspace_path(path)

    def test_normalizes_windows_separators_and_preserves_unicode_names(self):
        self.assertEqual(validate_workspace_path(r"src\main.py"), "src/main.py")
        self.assertEqual(validate_workspace_path("src/فایل.py"), "src/فایل.py")

    def test_file_content_is_utf8_byte_bounded_and_rejects_nul(self):
        self.assertEqual(validate_workspace_text("سلام"), "سلام")
        with self.assertRaises(ValueError):
            validate_workspace_text("x" * (MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            validate_workspace_text("bad\x00text")
        with self.assertRaises(ValueError):
            validate_workspace_text("\ud800")

    def test_workspace_total_validates_every_path_and_file(self):
        self.assertEqual(validate_workspace_total({"main.py": "print(1)"}), 8)
        with self.assertRaises(ValueError):
            validate_workspace_total({"../escape.py": "x"})
        with self.assertRaises(ValueError):
            validate_workspace_total({"main.py": "x" * (MAX_FILE_BYTES + 1)})

    def test_presence_and_websocket_messages_are_bounded_json_objects(self):
        valid = {"line": 12, "column": 4}
        self.assertEqual(bounded_json_object(valid), valid)
        self.assertEqual(bounded_json_object({"large": "x" * (MAX_PRESENCE_BYTES + 1)}), {})
        self.assertEqual(bounded_json_object(["not", "an", "object"]), {})
        self.assertTrue(validate_websocket_message({"type": "ping"}))
        self.assertFalse(validate_websocket_message(["ping"]))
        self.assertFalse(validate_websocket_message({"body": "x" * 64_001}))

    def test_client_id_is_short_and_log_safe(self):
        self.assertEqual(validate_client_id("client-123"), "client-123")
        for value in ("", "x" * 97, "bad client", "client/other", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_client_id(value)

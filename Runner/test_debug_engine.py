"""Regression tests for safe DAP breakpoint management."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from debug_engine import Session


class FakeDAP:
    def __init__(self):
        self.requests = []

    def call(self, command, arguments=None, timeout=10):
        if command != "setBreakpoints":
            raise AssertionError(f"Unexpected DAP command: {command}")
        args = arguments or {}
        self.requests.append(args)
        return {
            "breakpoints": [
                {
                    "line": item["line"],
                    "column": item.get("column", 1),
                    "verified": True,
                    "id": index,
                }
                for index, item in enumerate(args.get("breakpoints", []), start=1)
            ]
        }


class DebuggerBreakpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "a.py").write_text("a = 1\nprint(a)\n", encoding="utf-8")
        (self.root / "b.py").write_text("b = 2\n", encoding="utf-8")
        self.session = Session.__new__(Session)
        self.session.root = self.root
        self.session.dap = FakeDAP()
        self.session.breakpoints = []

    def tearDown(self):
        self.temp.cleanup()

    def test_adding_breakpoints_preserves_existing_lines_and_files(self):
        with patch.object(Session, "snapshot", return_value={"ok": True}):
            self.session.set_breakpoint("a.py", 1)
            self.session.set_breakpoint("a.py", 2)
            self.session.set_breakpoint("b.py", 1)

        self.assertEqual(
            [item["line"] for item in self.session.dap.requests[1]["breakpoints"]],
            [1, 2],
        )
        self.assertEqual(
            {(item["path"], item["line"]) for item in self.session.breakpoints},
            {("a.py", 1), ("a.py", 2), ("b.py", 1)},
        )

    def test_removing_one_breakpoint_keeps_other_breakpoints(self):
        with patch.object(Session, "snapshot", return_value={"ok": True}):
            self.session.set_breakpoint("a.py", 1)
            self.session.set_breakpoint("a.py", 2)
            self.session.set_breakpoint("b.py", 1)
            self.session.remove_breakpoint("a.py", 1)

        self.assertEqual(
            [item["line"] for item in self.session.dap.requests[-1]["breakpoints"]],
            [2],
        )
        self.assertEqual(
            {(item["path"], item["line"]) for item in self.session.breakpoints},
            {("a.py", 2), ("b.py", 1)},
        )

    def test_rejects_paths_outside_workspace(self):
        with self.assertRaises(ValueError):
            self.session._debug_path("../outside.py")

    def test_rejects_nonexistent_debug_source(self):
        with self.assertRaises(ValueError):
            self.session._debug_path("missing.py")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi import HTTPException

from main import MAX_COMMAND, safe_rel, run_command, _sandbox_command, _runtime_info


class RunnerSecurityTests(unittest.TestCase):
    def test_rejects_absolute_and_traversal_paths(self):
        for path in ("/etc/passwd", "C:/secret", "../secret", "a/../secret", "./secret", ".git/config", "a//b", ""):
            with self.assertRaises(ValueError):
                safe_rel(path)

    def test_allows_normal_workspace_paths(self):
        self.assertEqual(safe_rel("src/main.py"), "src/main.py")
        self.assertEqual(safe_rel("README.md"), "README.md")

    def test_blocks_dangerous_commands(self):
        for command in ("rm -rf /", "docker ps", "curl https://example.com", "kill 1"):
            with self.assertRaises(HTTPException):
                run_command(Path("/tmp"), command)

    def test_container_native_execution_policy_is_available_without_nested_sandbox(self):
        with patch("main.SANDBOX_MODE", "container"), patch("main.shutil.which", return_value=None):
            command = _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(command[:2], ["bash", "-lc"])

    def test_bubblewrap_mode_fails_closed_when_unavailable(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(ctx.exception.status_code, 503)

    def test_installer_can_request_network_without_disabling_bwrap_network_policy(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value="/usr/bin/bwrap"):
            command = _sandbox_command(Path("/tmp"), "npm install react", allow_network=True)
            self.assertNotIn("--unshare-net", command)

    def test_capability_manifest_is_machine_readable(self):
        from main import _capability_manifest
        manifest = _capability_manifest()
        self.assertEqual(manifest["api_version"], "1")
        self.assertIn("runtimes", manifest)
        self.assertIn("limits", manifest)
        self.assertIn("operations", manifest)
        self.assertIn("network_enforcement", manifest["sandbox"])

    def test_runtime_info_has_stable_capability_shape(self):
        info = _runtime_info()
        for name in ("python", "node", "npm", "git", "bash"):
            self.assertIn(name, info)
            self.assertIn("available", info[name])
            self.assertIn("version", info[name])

    def test_command_policy_blocks_known_escape_commands(self):
        for command in ("docker ps", "nsenter -t 1 -m", "kill 1", "curl https://example.com"):
            with self.assertRaises(HTTPException):
                run_command(Path("/tmp"), command)

    def test_symbol_engine_static_analysis_and_rename_preview(self):
        from symbol_engine import index, references, rename_preview
        root = Path("/tmp/runner-symbol-test")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("def greet(name):\\n    value = name\\n    return value\\nprint(greet('x'))\\n")
        (root / "app.js").write_text("const greet = () => 1;\\nconsole.log(greet());\\n")
        symbols = index(root)
        self.assertTrue(any(s["name"] == "greet" and s["kind"] == "function" for s in symbols))
        refs = references(root, "greet")
        self.assertGreaterEqual(len(refs), 2)
        preview = rename_preview(root, "greet", "welcome")
        self.assertEqual(preview["new"], "welcome")
        self.assertTrue(preview["changes"])

    def test_symbol_engine_p1_definitions_hover_completion(self):
        from symbol_engine import definitions, hover, completion
        root = Path("/tmp/runner-symbol-p1")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("def greet(name):\n    return name\n")
        defs = definitions(root, "greet")
        self.assertTrue(defs)
        self.assertEqual(defs[0]["kind"], "function")
        info = hover(root, "greet")
        self.assertEqual(info["path"], "main.py")
        self.assertIn("greet", info["signature"])
        items = completion(root, "gre")
        self.assertTrue(any(x["label"] == "greet" for x in items))

    def test_symbol_engine_rename_diff(self):
        from symbol_engine import rename_diff
        root = Path("/tmp/runner-symbol-rename")
        root.mkdir(parents=True, exist_ok=True)
        (root / "a.py").write_text("def greet():\n    return greet\n")
        result = rename_diff(root, "greet", "hello")
        self.assertEqual(result["references"], 2)
        self.assertTrue(result["changes"])
        self.assertIn("-def greet()", result["changes"][0]["diff"])
        self.assertIn("+def hello()", result["changes"][0]["diff"])

    def test_symbol_engine_rejects_invalid_rename(self):
        from symbol_engine import rename_preview
        root = Path("/tmp/runner-symbol-invalid")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("value = 1\\n")
        with self.assertRaises(ValueError):
            rename_preview(root, "value", "not valid")

    def test_debug_capability_requires_live_runtime(self):
        from debug_engine import capability
        cap = capability()
        self.assertIn(cap["mode"], {"live-dap", "unavailable"})
        self.assertEqual(cap["protocol"], "DAP" if cap["available"] else None)

    def test_debug_target_stays_inside_workspace(self):
        from debug_engine import Session
        root = Path("/tmp/runner-debug-test")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("x = 1\\n")
        session = Session(root, "../main.py", 1)
        with self.assertRaises(RuntimeError):
            session.start()

    def test_workspace_path_is_rejected_before_filesystem_access(self):
        for path in ("../x", "/tmp/x", "C:/x", ".git/config"):
            with self.assertRaises(ValueError):
                safe_rel(path)

    def test_rejects_oversized_commands(self):
        with self.assertRaises(HTTPException):
            run_command(Path("/tmp"), "x" * (MAX_COMMAND + 1))


if __name__ == "__main__":
    unittest.main()

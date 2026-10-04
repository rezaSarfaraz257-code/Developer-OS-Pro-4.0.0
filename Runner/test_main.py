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

    def test_command_policy_blocks_network_and_process_escape_vectors(self):
        for command in (
            "python -c \"import socket; socket.create_connection(('example.com',80),2)\"",
            "python -c \"import os; os.kill(1,9)\"",
            "python -c \"import subprocess; subprocess.run(['docker','ps'])\"",
        ):
            with self.assertRaises(HTTPException):
                run_command(Path("/tmp"), command)

    def test_workspace_path_is_rejected_before_filesystem_access(self):
        for path in ("../x", "/tmp/x", "C:/x", ".git/config"):
            with self.assertRaises(ValueError):
                safe_rel(path)

    def test_rejects_oversized_commands(self):
        with self.assertRaises(HTTPException):
            run_command(Path("/tmp"), "x" * (MAX_COMMAND + 1))


if __name__ == "__main__":
    unittest.main()

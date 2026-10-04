import unittest
from unittest.mock import patch
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
                run_command(__import__("pathlib").Path("/tmp"), command)


    def test_untrusted_execution_fails_closed_without_sandbox(self):
        with patch("main.shutil.which", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                _sandbox_command(__import__("pathlib").Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(ctx.exception.status_code, 503)

    def test_installer_can_request_network_without_disabling_sandbox(self):
        with patch("main.shutil.which", return_value="/usr/bin/bwrap"):
            command = _sandbox_command(__import__("pathlib").Path("/tmp"), "npm install react", allow_network=True)
            self.assertNotIn("--unshare-net", command)

    def test_runtime_info_has_stable_capability_shape(self):
        info = _runtime_info()
        for name in ("python", "node", "npm", "git", "bash"):
            self.assertIn(name, info)
            self.assertIn("available", info[name])
            self.assertIn("version", info[name])

    def test_rejects_oversized_commands(self):
        with self.assertRaises(HTTPException):
            run_command(__import__("pathlib").Path("/tmp"), "x" * (MAX_COMMAND + 1))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from package_engine import build_plan, capabilities


class PythonPackageManagerTests(unittest.TestCase):
    @patch("package_engine.shutil.which", return_value="/usr/local/bin/uv")
    def test_requirements_install_uses_uv_and_workspace_virtualenv(self, _which):
        plan = build_plan(
            {"requirements.txt": "fastapi==0.120.0\n"},
            action="install",
            manager="pip",
        )
        self.assertEqual(
            plan.command,
            "if [ ! -x .venv/bin/python ]; then uv venv .venv --python python3; fi && "
            "uv pip install --python .venv/bin/python -r requirements.txt",
        )

    @patch("package_engine.shutil.which", return_value="/usr/local/bin/uv")
    def test_add_and_remove_target_only_the_workspace_virtualenv(self, _which):
        add_plan = build_plan(
            {"requirements.txt": ""},
            action="add",
            manager="pip",
            package="fastapi",
        )
        remove_plan = build_plan(
            {"requirements.txt": ""},
            action="remove",
            manager="pip",
            package="fastapi",
        )
        self.assertIn("--python .venv/bin/python fastapi", add_plan.command)
        self.assertIn("uv pip uninstall --python .venv/bin/python fastapi", remove_plan.command)

    @patch("package_engine.shutil.which")
    def test_capabilities_only_advertise_installed_binaries(self, which):
        which.side_effect = lambda binary: f"/usr/bin/{binary}" if binary in {"npm", "uv"} else None
        ids = {manager["id"] for manager in capabilities()["managers"]}
        self.assertEqual(ids, {"npm", "pip", "uv"})

    @patch("package_engine.shutil.which", return_value=None)
    def test_unavailable_manager_cannot_generate_a_plan(self, _which):
        with self.assertRaisesRegex(ValueError, "not installed"):
            build_plan({"requirements.txt": ""}, action="install", manager="pip")

    @patch("package_engine.shutil.which", return_value="/usr/local/bin/uv")
    def test_requirements_install_requires_manifest_or_explicit_package(self, _which):
        with self.assertRaisesRegex(ValueError, "requirements.txt"):
            build_plan({"main.py": "print('hello')"}, action="install", manager="pip")


if __name__ == "__main__":
    unittest.main()

"""Production validation suite for Developer OS Runner contracts."""
from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def test_runner_modules_compile():
    modules = [
        "main.py","build_engine.py","environment_engine.py","debug_engine.py",
        "preview_engine.py","ai_engine.py","observability_engine.py",
        "performance_engine.py","recovery_engine.py",
    ]
    for name in modules:
        ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)

def test_security_sensitive_modules_are_present():
    assert (ROOT / "main.py").exists()
    assert (ROOT / "debug_engine.py").exists()
    assert (ROOT / "recovery_engine.py").exists()

def test_main_exposes_validation_surfaces():
    source=(ROOT / "main.py").read_text(encoding="utf-8")
    for route in ["/capabilities","/observability","/performance","/recovery/status","/ai/engineering/plan"]:
        assert route in source

def test_dependency_contract_has_debug_adapter():
    requirements=(ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "debugpy==" in requirements

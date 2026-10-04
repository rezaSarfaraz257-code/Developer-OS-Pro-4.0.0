from preview_engine import detect_preview, health_policy, plan

def test_detects_vite_preview():
    result = detect_preview({"package.json": '{"devDependencies":{"vite":"latest"}}'})
    assert result["framework"] == "vite"
    assert "0.0.0.0" in result["command"]

def test_detects_django_preview():
    result = detect_preview({"manage.py": ""})
    assert result["framework"] == "django"

def test_unknown_preview_is_unsupported():
    result = plan({"README.md": "# test"})
    assert result["status"] == "unsupported"
    assert result["target"] is None

def test_health_policy_is_bounded():
    policy = health_policy()
    assert policy["max_failures"] == 3
    assert policy["timeout_ms"] > 0

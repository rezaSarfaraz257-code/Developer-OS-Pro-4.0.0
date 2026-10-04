from ai_engine import build_context, plan, validate_patch

def test_context_is_bounded_and_active_file_first():
    result = build_context({"a.py":"a","b.py":"b"}, ["b.py"], "a.py")
    assert [item["path"] for item in result] == ["a.py","b.py"]

def test_plan_requires_known_action():
    result = plan({"action":"fix","goal":"repair bug","active_file":"a.py"}, {"a.py":"print(1)"})
    assert result["workflow"][-1] == "apply-or-reject"
    assert result["apply_policy"]["requires_explicit_approval"] is True

def test_patch_validation_blocks_missing_delete():
    try:
        validate_patch([{"path":"missing.py","operation":"delete"}], {})
        assert False
    except ValueError:
        assert True

def test_patch_validation_accepts_create():
    result = validate_patch([{"path":"new.py","operation":"create","content":"print(1)"}], {})
    assert result[0]["operation"] == "create"

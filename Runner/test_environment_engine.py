from environment_engine import plan, validate_runtime

def test_detects_node_and_python():
    result = plan({"package.json":"{}", "requirements.txt":""})
    assert result["primary"]["id"] == "node"
    assert {item["id"] for item in result["runtimes"]} == {"node", "python"}
    assert result["privileged"] is False
    assert result["network"] == "disabled-by-default"

def test_unknown_project_is_safe():
    result = plan({"README.md":"# test"})
    assert result["primary"] is None
    assert result["runtimes"] == []

def test_runtime_validation():
    assert validate_runtime("Node-22") == "node-22"

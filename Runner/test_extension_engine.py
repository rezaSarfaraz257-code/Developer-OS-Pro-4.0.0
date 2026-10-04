from extension_engine import manifest, validate_id

def test_manifest_is_capability_scoped():
    result = manifest()
    assert result["api_version"] == "1"
    assert "developer-os.core" in result["extensions"]

def test_extension_id_validation():
    assert validate_id("developer-os.test-explorer") == "developer-os.test-explorer"
    try:
        validate_id("../unsafe")
        assert False
    except ValueError:
        assert True

from recovery_engine import checkpoint, mark_verified, recover, status

def test_checkpoint_and_recover():
    checkpoint("s1", 4, {"main.py":"print(1)"}, {"active_file":"main.py"})
    result = recover("s1", "process_crash")
    assert result["status"] == "recovered"
    assert result["files"]["main.py"] == "print(1)"
    assert result["recovery"]["verified"] is False

def test_missing_checkpoint_is_safe():
    result = recover("missing", "crash")
    assert result["status"] == "unrecoverable"

def test_verify():
    checkpoint("s2", 5, {}, {})
    result = mark_verified("s2")
    assert result["status"] == "verified"

def test_status():
    result = status()
    assert result["status"] == "ready"

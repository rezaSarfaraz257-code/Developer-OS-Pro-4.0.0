from observability_engine import finish, record, snapshot, trace

def test_record_and_snapshot():
    record("build.completed", workspace_id=1, status="ok")
    result = snapshot()
    assert result["event_buffer"]["size"] >= 1
    assert result["counters"]["build.completed"] >= 1

def test_trace_finishes_with_duration():
    state = trace("test.run", workspace_id=2)
    result = finish(state, status="ok")
    assert result["event"] == "trace.finish"
    assert result["trace_id"] == state["trace_id"]
    assert result["duration_ms"] >= 0

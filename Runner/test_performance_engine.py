from performance_engine import finish, report, start

def test_profiler_records_wall_and_cpu_time():
    state = start("build", workspace_id=7)
    result = finish(state, status="ok")
    assert result["operation"] == "build"
    assert result["wall_ms"] >= 0
    assert result["cpu_ms"] >= 0

def test_report_has_bounded_summary():
    result = report()
    assert result["status"] == "ready"
    assert result["samples"] >= 0
    assert "wall_avg_ms" in result["summary"] or result["samples"] == 0

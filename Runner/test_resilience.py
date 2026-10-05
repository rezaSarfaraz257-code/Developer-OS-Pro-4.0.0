import time

import observability_engine as obs
from chaos_scenarios import scenario


def test_health_score_is_bounded_and_structured():
    result = obs.health_score()
    assert 0 <= result["score"] <= 100
    assert result["status"] in {"healthy", "degraded", "critical"}
    assert set(result["signals"]) == {
        "sync_conflict_rate",
        "reconnect_rate",
        "sync_latency_ms",
    }


def test_circuit_opens_after_repeated_failures():
    original = obs.circuit_state()
    try:
        with obs._CIRCUIT_LOCK:
            obs._CIRCUIT.update(state="closed", opened_at=0.0, failures=0)
        for _ in range(5):
            obs.circuit_record_failure()
        state = obs.circuit_state()
        assert state["state"] == "open"
        assert obs.circuit_allow("normal") is False
        assert obs.circuit_allow("critical") is True
    finally:
        with obs._CIRCUIT_LOCK:
            obs._CIRCUIT.update(original)


def test_circuit_recovers_after_success():
    with obs._CIRCUIT_LOCK:
        obs._CIRCUIT.update(state="open", opened_at=time.time(), failures=5)
    obs.circuit_record_success()
    assert obs.circuit_state()["state"] == "closed"
    assert obs.circuit_allow("normal") is True


def test_circuit_half_open_after_cooldown():
    with obs._CIRCUIT_LOCK:
        obs._CIRCUIT.update(state="open", opened_at=time.time() - 31, failures=5)
    assert obs.circuit_allow("normal") is True
    assert obs.circuit_state()["state"] == "half_open"


def test_health_telemetry_never_contains_source_payloads():
    payload = obs.snapshot()
    text = repr(payload).lower()
    assert "authorization" not in text
    assert "password" not in text
    assert "api_key" not in text


def test_chaos_scenarios_are_bounded_and_deterministic():
    assert scenario("network_flap")["expected"] == "outbox_replay"
    assert scenario("revision_conflict")["expected"] == "conflict_recovery"
    assert len(scenario("outbox_pressure")["events"]) == 1000
    assert scenario("runner_degraded")["expected"] == "circuit_open"

"""Deterministic chaos scenarios used by resilience tests and CI.

These scenarios model failures without spawning unbounded processes or touching
external infrastructure.
"""

SCENARIOS = {
    "network_flap": {
        "events": ["disconnect", "reconnect", "sync"],
        "expected": "outbox_replay",
    },
    "revision_conflict": {
        "events": ["stale_revision", "conflict", "refresh", "retry"],
        "expected": "conflict_recovery",
    },
    "outbox_pressure": {
        "events": ["enqueue"] * 1000,
        "expected": "bounded_queue",
    },
    "runner_degraded": {
        "events": ["timeout", "failure", "failure", "failure", "failure", "failure"],
        "expected": "circuit_open",
    },
}


def scenario(name):
    if name not in SCENARIOS:
        raise KeyError(name)
    return dict(SCENARIOS[name])

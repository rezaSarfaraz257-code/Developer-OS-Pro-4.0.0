import unittest
from concurrent.futures import ThreadPoolExecutor

from observability_engine import finish, record, snapshot, trace


class ObservabilityEngineTests(unittest.TestCase):
    def test_record_and_snapshot(self):
        record("build.completed", workspace_id=1, status="ok")
        result = snapshot()
        self.assertGreaterEqual(result["event_buffer"]["size"], 1)
        self.assertGreaterEqual(result["counters"]["build.completed"], 1)
        self.assertLessEqual(result["event_buffer"]["size"], result["event_buffer"]["capacity"])

    def test_trace_finishes_with_duration(self):
        state = trace("test.run", workspace_id=2)
        result = finish(state, status="ok")
        self.assertEqual(result["event"], "trace.finish")
        self.assertEqual(result["trace_id"], state["trace_id"])
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_sensitive_fields_are_redacted_recursively(self):
        item = record(
            "security.audit",
            authorization="Bearer top-secret",
            metadata={
                "api_key": "private-key",
                "nested": [{"refresh-token": "refresh-secret", "safe": "visible"}],
            },
        )
        self.assertEqual(item["authorization"], "[redacted]")
        self.assertEqual(item["metadata"]["api_key"], "[redacted]")
        self.assertEqual(item["metadata"]["nested"][0]["refresh-token"], "[redacted]")
        self.assertEqual(item["metadata"]["nested"][0]["safe"], "visible")

        recent = snapshot()["recent_events"]
        stored = next(event for event in reversed(recent) if event["event"] == "security.audit")
        self.assertNotIn("top-secret", repr(stored))
        self.assertNotIn("private-key", repr(stored))
        self.assertNotIn("refresh-secret", repr(stored))

    def test_returned_event_cannot_mutate_internal_snapshot(self):
        item = record("immutable.snapshot", details={"state": "original"})
        item["details"]["state"] = "tampered"
        stored = next(
            event for event in reversed(snapshot()["recent_events"])
            if event["event"] == "immutable.snapshot"
        )
        self.assertEqual(stored["details"]["state"], "original")

    def test_trace_ids_are_unique_under_concurrency(self):
        def create_trace(_):
            return trace("parallel.request")["trace_id"]

        with ThreadPoolExecutor(max_workers=12) as pool:
            trace_ids = list(pool.map(create_trace, range(200)))
        self.assertEqual(len(trace_ids), len(set(trace_ids)))


if __name__ == "__main__":
    unittest.main()

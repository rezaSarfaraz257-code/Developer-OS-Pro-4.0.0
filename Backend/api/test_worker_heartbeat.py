from datetime import datetime
from unittest.mock import Mock, patch

from django.db import OperationalError
from django.test import SimpleTestCase

from .management.commands.worker import _heartbeat_loop


class WorkerHeartbeatResilienceTests(SimpleTestCase):
    @patch("api.management.commands.worker.logger.exception")
    @patch("api.management.commands.worker.close_old_connections")
    @patch("api.management.commands.worker.WorkerHeartbeat.objects.update_or_create")
    def test_transient_database_failure_does_not_kill_heartbeat_loop(
        self, update_heartbeat, close_connections, log_exception
    ):
        update_heartbeat.side_effect = [
            OperationalError("temporary database disconnect"),
            (None, True),
        ]
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, False, True]
        stop_event.wait.side_effect = [False, True]

        _heartbeat_loop("worker-test:123", stop_event)

        self.assertEqual(update_heartbeat.call_count, 2)
        self.assertEqual(log_exception.call_count, 1)
        self.assertEqual(close_connections.call_count, 4)
        for heartbeat_call in update_heartbeat.call_args_list:
            self.assertEqual(heartbeat_call.kwargs["name"], "background-worker")
            self.assertEqual(
                heartbeat_call.kwargs["defaults"]["worker_id"],
                "worker-test:123",
            )
            self.assertIsInstance(
                heartbeat_call.kwargs["defaults"]["last_seen_at"],
                datetime,
            )
        self.assertEqual(stop_event.wait.call_args_list, [((15,),), ((15,),)])

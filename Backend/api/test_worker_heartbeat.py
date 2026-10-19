from unittest.mock import Mock, call, patch

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
        self.assertEqual(
            update_heartbeat.call_args_list,
            [
                call(
                    name="background-worker",
                    defaults={
                        "worker_id": "worker-test:123",
                        "last_seen_at": update_heartbeat.call_args_list[0].kwargs["defaults"]["last_seen_at"],
                    },
                ),
                call(
                    name="background-worker",
                    defaults={
                        "worker_id": "worker-test:123",
                        "last_seen_at": update_heartbeat.call_args_list[1].kwargs["defaults"]["last_seen_at"],
                    },
                ),
            ],
        )
        self.assertEqual(stop_event.wait.call_args_list, [call(15), call(15)])

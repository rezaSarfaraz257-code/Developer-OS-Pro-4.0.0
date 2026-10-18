from unittest.mock import Mock, patch

from django.test import SimpleTestCase

import entrypoint


class OptionalBackgroundWorkerSupervisorTests(SimpleTestCase):
    @patch.dict("os.environ", {}, clear=True)
    @patch("entrypoint.os.execvp")
    def test_default_keeps_existing_api_startup_path(self, execvp):
        with self.assertRaises(AssertionError):
            # execvp is mocked, so it returns; production execvp replaces the
            # process and never returns. Assert the legacy command is unchanged.
            entrypoint.run_api_with_optional_worker()
            execvp.assert_called_once_with(
                "gunicorn",
                ["gunicorn", "x.asgi:application", "-c", "gunicorn.conf.py"],
            )

    @patch.dict("os.environ", {"RUN_BACKGROUND_WORKER_IN_WEB": "true"})
    @patch("entrypoint.time.sleep", return_value=None)
    @patch("entrypoint.subprocess.Popen")
    def test_supervised_worker_exit_fails_closed_and_stops_api(self, popen, _sleep):
        worker = Mock()
        api = Mock()
        worker.poll.side_effect = [None, 7, 7]
        worker.returncode = 7
        api.poll.side_effect = [None, None, None]
        api.returncode = None
        popen.side_effect = [worker, api]

        result = entrypoint.run_api_with_optional_worker()

        self.assertNotEqual(result, 0)
        self.assertEqual(popen.call_count, 2)
        api.terminate.assert_called_once()

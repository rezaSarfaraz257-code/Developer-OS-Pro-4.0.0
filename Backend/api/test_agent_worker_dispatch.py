from unittest.mock import patch

from django.test import TestCase

from .management.commands.worker import execute
from .models import BackgroundJob


class AgentPlanWorkerDispatchTests(TestCase):
    @patch("api.agent_runtime.build_agent_plan")
    def test_agent_plan_job_dispatches_to_durable_planner(self, planner):
        planner.return_value = {"run_id": 41, "status": "awaiting_approval", "task_count": 3}
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload={"run_id": 41},
            idempotency_key="agent-worker-dispatch-41",
        )
        result = execute(job)
        self.assertEqual(result["run_id"], 41)
        planner.assert_called_once_with(41)

    def test_worker_rejects_unknown_job_kind(self):
        job = BackgroundJob.objects.create(
            kind="unknown_agent_job",
            payload={},
            idempotency_key="unknown-agent-job-41",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported job kind"):
            execute(job)

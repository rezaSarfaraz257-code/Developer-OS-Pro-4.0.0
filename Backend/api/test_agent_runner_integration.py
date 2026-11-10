"""Real backend-to-Runner integration coverage.

The dedicated CI job runs this test with a local strict Bubblewrap Runner.
It is skipped in the normal unit-test job, where Runner HTTP is mocked.
"""
import os
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .management.commands.worker import execute as execute_background_job
from .models import AgentRun, AgentTask, AuditLog, BackgroundJob, CodeWorkspace


@skipUnless(os.getenv("AGENT_RUNNER_INTEGRATION") == "1", "requires the strict CI Runner service")
class AgentRunnerIntegrationTests(TestCase):
    def setUp(self):
        rate_limit_patcher = patch("api.security.consume_rate_limit", return_value=(True, None))
        rate_limit_patcher.start()
        self.addCleanup(rate_limit_patcher.stop)

        self.user = User.objects.create_user(
            username="runner-integration-owner", password="StrongPassword123!",
        )
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Real Runner integration workspace",
            files={
                "test_feature.py": (
                    "import socket\n"
                    "import unittest\n\n"
                    "class FeatureTests(unittest.TestCase):\n"
                    "    def test_runner_executes_the_synced_revision(self):\n"
                    "        self.assertEqual(6 * 7, 42)\n\n"
                    "    def test_runner_network_namespace_cannot_reach_runner_api(self):\n"
                    "        probe = socket.socket()\n"
                    "        probe.settimeout(1)\n"
                    "        try:\n"
                    "            with self.assertRaises(OSError):\n"
                    "                probe.connect(('127.0.0.1', 8080))\n"
                    "        finally:\n"
                    "            probe.close()\n\n"
                    "    def test_workload_profile_has_no_capabilities_or_namespace_privilege(self):\n"
                    "        with open('/proc/self/attr/current', encoding='utf-8') as stream:\n"
                    "            profile = stream.read()\n"
                    "        self.assertIn('developer-os-runner-workload', profile)\n"
                    "        status = {}\n"
                    "        with open('/proc/self/status', encoding='utf-8') as stream:\n"
                    "            for line in stream:\n"
                    "                if ':' in line:\n"
                    "                    key, value = line.split(':', 1)\n"
                    "                    status[key] = value.strip()\n"
                    "        self.assertEqual(status.get('CapEff'), '0000000000000000')\n"
                    "        self.assertEqual(status.get('NoNewPrivs'), '1')\n"
                ),
            },
        )
        self.run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Verify a human-approved Python task against the real isolated Runner",
            status="approved",
            approved_at=timezone.now(),
            plan={"acceptance_criteria": ["The Python feature test passes in isolated Runner execution"]},
            idempotency_key="agent-runner-real-integration-001",
            workspace_revision=self.workspace.revision,
        )
        self.task = AgentTask.objects.create(
            run=self.run,
            key="runner_integration",
            title="Verify Python feature in isolated Runner",
            description="Execute the workspace test suite with the isolated Runner.",
            agent_role="testing",
            status="ready",
        )

        # Simulate the human-approved patch already committed to the
        # authoritative workspace at a newer revision.
        self.workspace.files = {
            "test_feature.py": (
                "import ctypes\n"
                    "import errno\n"
                    "import socket\n"
                    "import tempfile\n"
                "import unittest\n\n"
                "class FeatureTests(unittest.TestCase):\n"
                "    def test_runner_executes_the_synced_revision(self):\n"
                "        self.assertEqual(6 * 7, 42)\n\n"
                "    def test_runner_network_namespace_cannot_reach_runner_api(self):\n"
                "        probe = socket.socket()\n"
                "        probe.settimeout(1)\n"
                "        try:\n"
                "            with self.assertRaises(OSError):\n"
                "                probe.connect(('127.0.0.1', 8080))\n"
                "        finally:\n"
                "            probe.close()\n\n"
                    "    def test_workload_profile_has_no_capabilities_or_namespace_privilege(self):\n"
                    "        with open('/proc/self/attr/current', encoding='utf-8') as stream:\n"
                    "            profile = stream.read()\n"
                    "        self.assertIn('developer-os-runner//&developer-os-runner-workload', profile)\n"
                    "        status = {}\n"
                    "        with open('/proc/self/status', encoding='utf-8') as stream:\n"
                    "            for line in stream:\n"
                    "                if ':' in line:\n"
                    "                    key, value = line.split(':', 1)\n"
                    "                    status[key] = value.strip()\n"
                    "        self.assertEqual(status.get('CapEff'), '0000000000000000')\n"
                    "        self.assertEqual(status.get('NoNewPrivs'), '1')\n"
                    "    def test_workload_cannot_create_nested_user_namespace(self):\n"
                    "        libc = ctypes.CDLL(None, use_errno=True)\n"
                    "        libc.unshare.argtypes = [ctypes.c_int]\n"
                    "        libc.unshare.restype = ctypes.c_int\n"
                    "        result = libc.unshare(0x10000000)  # CLONE_NEWUSER\n"
                    "        self.assertEqual(result, -1)\n"
                    "        self.assertIn(ctypes.get_errno(), (errno.EPERM, errno.EACCES))\n\n"
                    "    def test_workload_cannot_mount_tmpfs(self):\n"
                    "        libc = ctypes.CDLL(None, use_errno=True)\n"
                    "        libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]\n"
                    "        libc.mount.restype = ctypes.c_int\n"
                    "        libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]\n"
                    "        libc.umount2.restype = ctypes.c_int\n"
                    "        with tempfile.TemporaryDirectory() as target:\n"
                    "            result = libc.mount(b'tmpfs', target.encode(), b'tmpfs', 0, None)\n"
                    "            if result == 0:\n"
                    "                libc.umount2(target.encode(), 0)\n"
                    "            self.assertEqual(result, -1)\n"
                    "            self.assertIn(ctypes.get_errno(), (errno.EPERM, errno.EACCES))\n"
            ),
            "README.md": "Runner integration revision two\n",
        }
        self.workspace.save(update_fields={"files"})

        self.sync_job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync",
            status="running",
            payload={
                "workspace_id": self.workspace.id,
                "owner_id": self.user.id,
                "requested_revision": self.workspace.revision,
            },
            idempotency_key=f"agent-runner-integration-sync:{self.workspace.id}:{self.workspace.revision}",
        )
        # This is a real HTTP call to Runner /sync, not a mocked response.
        sync_result = execute_background_job(self.sync_job)
        self.sync_job.status = "succeeded"
        self.sync_job.result = sync_result
        self.sync_job.save(update_fields={"status", "result"})
        AuditLog.objects.create(
            user=self.user,
            action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace",
            target_id=str(self.workspace.id),
            metadata={"job_id": self.sync_job.id, "revision": self.workspace.revision},
        )
        AuditLog.objects.create(
            user=self.user,
            action="ai_agent.patch_applied",
            target_type="CodeWorkspace",
            target_id=str(self.workspace.id),
            metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
            },
        )

        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_approved_revision_is_synced_executed_and_human_accepted(self):
        revision = self.workspace.revision
        queue_response = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/verify/",
            {
                "sync_job_id": self.sync_job.id,
                "revision": revision,
                "profile": "python_unittest",
            },
            format="json",
        )
        self.assertEqual(queue_response.status_code, 202)

        verification_job = BackgroundJob.objects.get(pk=queue_response.data["job_id"])
        verification_job.status = "running"
        verification_job.attempts = 1
        verification_job.save(update_fields=["status", "attempts"])

        # This call reaches the actual Runner /exec endpoint. The Runner uses
        # strict Bubblewrap isolation and returns its real execution job ID.
        result = execute_background_job(verification_job)
        self.assertEqual(result["status"], "verification_passed_pending_acceptance")
        self.assertTrue(result["evidence"]["runner_job_id"])
        self.assertEqual(result["evidence"]["runner_status"], "completed")
        self.assertEqual(result["evidence"]["exit_code"], 0)
        self.assertFalse(result["evidence"]["output_persisted"])

        verification_job.status = "succeeded"
        verification_job.result = result
        verification_job.save(update_fields=["status", "result"])

        acceptance = self.client.post(
            f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/accept/",
            {"accepted_criteria": self.run.plan["acceptance_criteria"]},
            format="json",
        )
        self.assertEqual(acceptance.status_code, 200)

        self.task.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.task.status, "succeeded")
        self.assertEqual(self.run.status, "succeeded")
        self.assertEqual(self.task.execution_evidence["human_acceptance"]["user_id"], self.user.id)

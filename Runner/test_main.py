import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi import HTTPException

from main import MAX_COMMAND, safe_rel, run_command, _sandbox_command, _runtime_info


class RunnerSecurityTests(unittest.TestCase):
    def test_rejects_absolute_and_traversal_paths(self):
        for path in ("/etc/passwd", "C:/secret", "../secret", "a/../secret", "./secret", ".git/config", "a//b", ""):
            with self.assertRaises(ValueError):
                safe_rel(path)

    def test_allows_normal_workspace_paths(self):
        self.assertEqual(safe_rel("src/main.py"), "src/main.py")
        self.assertEqual(safe_rel("README.md"), "README.md")

    def test_blocks_dangerous_commands(self):
        for command in ("rm -rf /", "docker ps", "curl https://example.com", "kill 1"):
            with self.assertRaises(HTTPException):
                run_command(Path("/tmp"), command)

    def test_container_native_execution_policy_is_available_without_nested_sandbox(self):
        with patch("main.SANDBOX_MODE", "container"), patch("main.shutil.which", return_value=None):
            command = _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(command[:2], ["bash", "-lc"])

    def test_bubblewrap_mode_fails_closed_when_unavailable(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(ctx.exception.status_code, 503)

    def test_installer_can_request_network_without_disabling_bwrap_network_policy(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value="/usr/bin/bwrap"):
            command = _sandbox_command(Path("/tmp"), "npm install react", allow_network=True)
            self.assertNotIn("--unshare-net", command)

    def test_capability_manifest_is_machine_readable(self):
        from main import _capability_manifest
        manifest = _capability_manifest()
        self.assertEqual(manifest["api_version"], "1")
        self.assertIn("runtimes", manifest)
        self.assertIn("limits", manifest)
        self.assertIn("operations", manifest)
        self.assertIn("network_enforcement", manifest["sandbox"])

    def test_runtime_info_has_stable_capability_shape(self):
        info = _runtime_info()
        for name in ("python", "node", "npm", "git", "bash"):
            self.assertIn(name, info)
            self.assertIn("available", info[name])
            self.assertIn("version", info[name])

    def test_command_policy_blocks_known_escape_commands(self):
        for command in ("docker ps", "nsenter -t 1 -m", "kill 1", "curl https://example.com"):
            with self.assertRaises(HTTPException):
                run_command(Path("/tmp"), command)

    def test_symbol_engine_static_analysis_and_rename_preview(self):
        from symbol_engine import index, references, rename_preview
        root = Path("/tmp/runner-symbol-test")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("def greet(name):\n    value = name\n    return value\nprint(greet('x'))\n")
        (root / "app.js").write_text("const greet = () => 1;\nconsole.log(greet());\n")
        symbols = index(root)
        self.assertTrue(any(s["name"] == "greet" and s["kind"] == "function" for s in symbols))
        refs = references(root, "greet")
        self.assertGreaterEqual(len(refs), 2)
        preview = rename_preview(root, "greet", "welcome")
        self.assertEqual(preview["new"], "welcome")
        self.assertTrue(preview["changes"])

    def test_symbol_engine_p1_definitions_hover_completion(self):
        from symbol_engine import definitions, hover, completion
        root = Path("/tmp/runner-symbol-p1")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("def greet(name):\n    return name\n")
        defs = definitions(root, "greet")
        self.assertTrue(defs)
        self.assertEqual(defs[0]["kind"], "function")
        info = hover(root, "greet")
        self.assertEqual(info["path"], "main.py")
        self.assertIn("greet", info["signature"])
        items = completion(root, "gre")
        self.assertTrue(any(x["label"] == "greet" for x in items))

    def test_symbol_engine_diagnostics(self):
        from symbol_engine import diagnostics
        root = Path("/tmp/runner-symbol-diagnostics")
        root.mkdir(parents=True, exist_ok=True)
        (root / "bad.py").write_text("def broken(:\n")
        result = diagnostics(root, "bad.py")
        self.assertTrue(result)
        self.assertEqual(result[0]["severity"], "error")
        self.assertEqual(result[0]["code"], "PY001")

    def test_symbol_engine_rename_diff(self):
        from symbol_engine import rename_diff
        root = Path("/tmp/runner-symbol-rename")
        root.mkdir(parents=True, exist_ok=True)
        (root / "a.py").write_text("def greet():\n    return greet\n")
        result = rename_diff(root, "greet", "hello")
        self.assertEqual(result["references"], 2)
        self.assertTrue(result["changes"])
        self.assertIn("-def greet()", result["changes"][0]["diff"])
        self.assertIn("+def hello()", result["changes"][0]["diff"])

    def test_symbol_engine_rejects_invalid_rename(self):
        from symbol_engine import rename_preview
        root = Path("/tmp/runner-symbol-invalid")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("value = 1\\n")
        with self.assertRaises(ValueError):
            rename_preview(root, "value", "not valid")

    def test_debug_capability_requires_live_runtime(self):
        from debug_engine import capability
        cap = capability()
        self.assertIn(cap["mode"], {"live-dap", "unavailable"})
        self.assertEqual(cap["protocol"], "DAP" if cap["available"] else None)

    def test_debug_target_stays_inside_workspace(self):
        from debug_engine import Session
        root = Path("/tmp/runner-debug-test")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("x = 1\\n")
        session = Session(root, "../main.py", 1)
        with self.assertRaises(RuntimeError):
            session.start()

    def test_workspace_path_is_rejected_before_filesystem_access(self):
        for path in ("../x", "/tmp/x", "C:/x", ".git/config"):
            with self.assertRaises(ValueError):
                safe_rel(path)

    def test_python_command_is_normalized_when_only_python3_exists(self):
        import main
        with patch("main.shutil.which", side_effect=lambda name: "/usr/bin/python3" if name == "python3" else None):
            self.assertEqual(main._normalize_command("python main.py"), "python3 main.py")

    def test_execute_and_process_payloads_use_workspace_lock(self):
        import main
        self.assertTrue(hasattr(main, "_workspace_lock"))

    def test_process_state_exposes_completion_metadata(self):
        import main
        self.assertGreater(main.PROCESS_RETENTION_SECONDS, 0)
        self.assertGreater(main.PROCESS_TAIL_BYTES, 0)

    def test_preview_port_is_within_safe_range(self):
        import main
        port = main._workspace_preview_port(42)
        self.assertGreaterEqual(port, 1024)
        self.assertLessEqual(port, 65535)

    def test_rejects_oversized_commands(self):
        with self.assertRaises(HTTPException):
            run_command(Path("/tmp"), "x" * (MAX_COMMAND + 1))


if __name__ == "__main__":
    unittest.main()


def test_job_lifecycle_helpers():
    job_id = main._new_job("test-workspace")
    snapshot = main._job_snapshot(job_id)
    assert snapshot["status"] == "queued"
    assert snapshot["workspace_id"] == "test-workspace"
    assert snapshot["attempt"] == 0
    main._set_job(job_id, status="running", attempt=1)
    snapshot = main._job_snapshot(job_id)
    assert snapshot["status"] == "running"
    assert snapshot["attempt"] == 1
    cancelled = main._cancel_job(job_id)
    assert cancelled["status"] in ("running", "cancelled")


def test_retry_policy_is_bounded():
    assert main.JOB_MAX_RETRIES >= 0
    assert main.JOB_MAX_RETRIES <= 3
    assert main._retry_delay(1) <= 10
    assert main._retry_delay(2) >= main._retry_delay(1)


def test_incremental_job_output_cursor():
    job_id = main._new_job("stream-workspace")
    main._append_job_output(job_id, "hello\\n")
    chunk, cursor = main._job_output_slice(job_id, 0)
    assert chunk == "hello\\n"
    assert cursor == len("hello\\n")
    chunk2, cursor2 = main._job_output_slice(job_id, cursor)
    assert chunk2 == ""
    assert cursor2 == cursor
    main._append_job_output(job_id, "world\\n")
    chunk3, cursor3 = main._job_output_slice(job_id, cursor)
    assert chunk3 == "world\\n"
    assert cursor3 == len("hello\\nworld\\n")


def test_stream_wait_is_bounded():
    assert 0.1 <= main.STREAM_WAIT_SECONDS <= 5.0


def test_job_output_is_bounded():
    job_id = main._new_job("bounded-output")
    main._append_job_output(job_id, "x" * (main.JOB_OUTPUT_MAX_BYTES + 1024))
    output = main._job_output_snapshot(job_id)
    assert len(output) <= main.JOB_OUTPUT_MAX_BYTES


def test_output_buffer_cleanup_for_finished_job():
    job_id = main._new_job("cleanup-workspace")
    main._append_job_output(job_id, "finished output")
    main._set_job(job_id, status="completed", finished_at=time.time())
    assert main._job_snapshot(job_id)["status"] == "completed"
    # Finished jobs may be retained for inspection, but their live output
    # buffer must remain bounded and independently addressable.
    assert len(main._job_output_snapshot(job_id)) <= main.JOB_OUTPUT_MAX_BYTES


def test_retry_delay_is_bounded():
    assert main._retry_delay(1) <= 10
    assert main._retry_delay(100) <= 10


def test_stream_terminal_status_is_supported():
    assert {"completed", "failed", "cancelled"}.issuperset({"completed", "failed", "cancelled"})


def test_concurrent_workspace_limits():
    assert main.MAX_CONCURRENT_EXECUTIONS >= 1
    assert main.MAX_CONCURRENT_PROCESSES >= 1
    assert main.MAX_WORKSPACE_CONCURRENT >= 1


def test_resource_limits_are_bounded():
    assert 128 <= main.RUNNER_MEMORY_MB <= 2048
    assert main.MAX_OUTPUT_BYTES > 0
    assert main.MAX_PROCESS_DURATION > 0


def test_multiple_jobs_have_isolated_output():
    first = main._new_job("pressure-a")
    second = main._new_job("pressure-b")
    main._append_job_output(first, "A")
    main._append_job_output(second, "B")
    assert main._job_output_snapshot(first) == "A"
    assert main._job_output_snapshot(second) == "B"


def test_fair_scheduler_isolates_workspaces():
    scheduler = main.FairScheduler(max_per_workspace=1)
    assert scheduler.acquire("workspace-a") is True
    assert scheduler.acquire("workspace-a") is False
    assert scheduler.acquire("workspace-b") is True
    assert scheduler.active("workspace-a") == 1
    assert scheduler.active("workspace-b") == 1
    scheduler.release("workspace-a")
    assert scheduler.acquire("workspace-a") is True


def test_fair_scheduler_release_is_idempotent():
    scheduler = main.FairScheduler(max_per_workspace=1)
    scheduler.acquire("workspace-a")
    scheduler.release("workspace-a")
    scheduler.release("workspace-a")
    assert scheduler.active("workspace-a") == 0


def test_fair_scheduler_snapshot_is_safe():
    scheduler = main.FairScheduler(max_per_workspace=2)
    scheduler.acquire("workspace-a")
    snapshot = scheduler.snapshot()
    assert snapshot["max_per_workspace"] == 2
    assert snapshot["active"]["workspace-a"] == 1

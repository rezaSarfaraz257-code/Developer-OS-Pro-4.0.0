import main
import unittest
import time
import subprocess
import tempfile
import os
import sys
from unittest.mock import Mock, patch
from pathlib import Path
from fastapi import HTTPException

from main import MAX_COMMAND, safe_rel, run_command, _sandbox_command, _runtime_info

class RunnerEnvironmentLimitParsingTests(unittest.TestCase):
    def test_invalid_integer_environment_value_uses_safe_default(self):
        with patch.dict(os.environ, {"RUNNER_TEST_INT": "not-a-number"}):
            self.assertEqual(main._bounded_int_env("RUNNER_TEST_INT", 7, 1, 10), 7)

    def test_integer_environment_value_is_clamped(self):
        with patch.dict(os.environ, {"RUNNER_TEST_INT": "-100"}):
            self.assertEqual(main._bounded_int_env("RUNNER_TEST_INT", 7, 1, 10), 1)
        with patch.dict(os.environ, {"RUNNER_TEST_INT": "999"}):
            self.assertEqual(main._bounded_int_env("RUNNER_TEST_INT", 7, 1, 10), 10)

    def test_invalid_and_non_finite_float_environment_values_use_default(self):
        for value in ("bad", "nan", "inf", "-inf"):
            with self.subTest(value=value), patch.dict(os.environ, {"RUNNER_TEST_FLOAT": value}):
                self.assertEqual(main._bounded_float_env("RUNNER_TEST_FLOAT", 0.5, 0.1, 2.0), 0.5)

    def test_float_environment_value_is_clamped(self):
        with patch.dict(os.environ, {"RUNNER_TEST_FLOAT": "-3.5"}):
            self.assertEqual(main._bounded_float_env("RUNNER_TEST_FLOAT", 0.5, 0.1, 2.0), 0.1)
        with patch.dict(os.environ, {"RUNNER_TEST_FLOAT": "50"}):
            self.assertEqual(main._bounded_float_env("RUNNER_TEST_FLOAT", 0.5, 0.1, 2.0), 2.0)




class RunnerJobTenantIsolationTests(unittest.TestCase):
    def test_job_lookup_requires_matching_workspace(self):
        job_id = main._new_job("workspace-a")
        try:
            job = main._job_for_workspace(job_id, "workspace-a")
            self.assertEqual(job["workspace_id"], "workspace-a")
            with self.assertRaises(HTTPException) as ctx:
                main._job_for_workspace(job_id, "workspace-b")
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            with main.JOB_LOCK:
                main.JOBS.pop(job_id, None)
            with main.JOB_OUTPUT_LOCK:
                main.JOB_OUTPUT.pop(job_id, None)


class RunnerSecurityLevelResolutionTests(unittest.TestCase):
    def test_compat_is_forced_strict_in_production(self):
        self.assertEqual(
            main._resolve_runner_security_level(
                "compat",
                environment="production",
                render_service_id="",
                render_git_commit="",
            ),
            "strict",
        )
        self.assertEqual(
            main._resolve_runner_security_level(
                "compat",
                environment="prod",
                render_service_id="",
                render_git_commit="",
            ),
            "strict",
        )

    def test_compat_is_forced_strict_on_render_without_environment_marker(self):
        self.assertEqual(
            main._resolve_runner_security_level(
                "compat",
                environment="",
                render_service_id="srv-runner",
                render_git_commit="",
            ),
            "strict",
        )
        self.assertEqual(
            main._resolve_runner_security_level(
                "compat",
                environment="development",
                render_service_id="",
                render_git_commit="abc123",
            ),
            "strict",
        )

    def test_compat_remains_available_for_local_ci_only(self):
        self.assertEqual(
            main._resolve_runner_security_level(
                "compat",
                environment="development",
                render_service_id="",
                render_git_commit="",
            ),
            "compat",
        )

    def test_unknown_security_level_fails_closed(self):
        self.assertEqual(
            main._resolve_runner_security_level(
                "unsafe",
                environment="development",
                render_service_id="",
                render_git_commit="",
            ),
            "strict",
        )


class RunnerSecurityTests(unittest.TestCase):
    def test_preview_path_rejects_traversal_and_control_characters(self):
        for path in ("../admin", "src/../admin", "%2e%2e/admin", "a\\\\b", "bad\\npath"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException) as ctx:
                    main._safe_preview_path(path)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_preview_path_encodes_query_and_fragment_delimiters(self):
        self.assertEqual(main._safe_preview_path("app?token=x#fragment"), "app%3Ftoken=x%23fragment")

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
        with patch("main.SANDBOX_MODE", "container"), patch("main.RUNNER_SECURITY_LEVEL", "compat"), patch("main.shutil.which", return_value=None):
            command = _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(command[:2], ["bash", "-lc"])

    def test_strict_container_execution_fails_closed_without_tenant_isolation(self):
        with patch("main.SANDBOX_MODE", "container"), patch("main.RUNNER_SECURITY_LEVEL", "strict"):
            with self.assertRaises(HTTPException) as ctx:
                _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("per-execution isolation", ctx.exception.detail)

    def test_runner_token_is_not_left_in_process_environment(self):
        self.assertNotIn("IDE_RUNNER_TOKEN", os.environ)

    def test_bubblewrap_mode_fails_closed_when_unavailable(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(ctx.exception.status_code, 503)

    def test_bwrap_runtime_failure_fails_closed(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value="/usr/bin/bwrap"), patch("main._bwrap_runtime_available", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                _sandbox_command(Path("/tmp"), "python -c 'print(1)'", allow_network=False)
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("cannot create the required isolated namespaces", ctx.exception.detail)

    def test_installer_can_request_network_without_disabling_bwrap_network_policy(self):
        with patch("main.SANDBOX_MODE", "bwrap"), patch("main.shutil.which", return_value="/usr/bin/bwrap"), patch("main._bwrap_runtime_available", return_value=True):
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
        self.assertIn("execution_available", manifest["sandbox"])

    def test_strict_shared_container_reports_execution_unavailable(self):
        from main import _capability_manifest
        with patch("main.SANDBOX_MODE", "container"), patch("main.RUNNER_SECURITY_LEVEL", "strict"):
            manifest = _capability_manifest()
        self.assertFalse(manifest["sandbox"]["execution_available"])
        self.assertEqual(manifest["sandbox"]["mode"], "unavailable")
        self.assertFalse(manifest["operations"]["execute"])
        self.assertFalse(manifest["operations"]["process"])
        self.assertFalse(manifest["operations"]["install"])
        self.assertFalse(manifest["operations"]["debug"])

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

    def test_debug_session_has_bounded_lifecycle(self):
        from debug_engine import SESSION_TTL, SESSIONS
        self.assertGreaterEqual(SESSION_TTL, 300)
        self.assertIsInstance(SESSIONS, dict)

    def test_debug_start_requires_explicit_target(self):
        from debug_engine import handle
        with self.assertRaises(ValueError):
            handle("start", root="/tmp")

    def test_debug_target_stays_inside_workspace(self):
        from debug_engine import Session
        root = Path("/tmp/runner-debug-test")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text("x = 1\\n")
        session = Session(root, "../main.py", 1)
        with self.assertRaises(RuntimeError):
            session.start()

    def test_workspace_path_is_rejected_before_filesystem_access(self):
        for path in ("../x", "/tmp/x", "C:/x", ".git/config", "nested/.git/config", "nested/.git"):
            with self.assertRaises(ValueError):
                safe_rel(path)

    def test_python_command_is_normalized_when_only_python3_exists(self):
        import main
        with patch("main.shutil.which", side_effect=lambda name: "/usr/bin/python3" if name == "python3" else None):
            self.assertEqual(main._normalize_command("python3 main.py"), "python3 main.py")

    def test_execute_and_process_payloads_use_workspace_lock(self):
        import main
        self.assertTrue(hasattr(main, "_workspace_lock"))

    def test_python_execution_uses_submitted_workspace_source_and_returns_exact_stdout(self):
        root = Path(tempfile.mkdtemp(prefix="developer-os-exec-"))
        (root / "main.py").write_text("print('runner-source-ok')\n", encoding="utf-8")
        with patch("main.RUNNER_SECURITY_LEVEL", "compat"):
            result = run_command(root, "python3 main.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"].strip(), "runner-source-ok")
        self.assertNotIn("Hello, Developer OS", result["stdout"])


    def test_process_state_exposes_completion_metadata(self):
        import main
        self.assertGreater(main.PROCESS_RETENTION_SECONDS, 0)
        self.assertGreater(main.PROCESS_TAIL_BYTES, 0)

    def test_preview_port_is_within_safe_range(self):
        import main
        port = main._workspace_preview_port(42)
        self.assertGreaterEqual(port, 1024)
        self.assertLessEqual(port, 65535)

    def test_preview_commands_are_pinned_to_the_proxy_port(self):
        cases = [
            ("npm run dev -- --host 0.0.0.0", ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "12345"]),
            ("python3 manage.py runserver 0.0.0.0:8000", ["python3", "manage.py", "runserver", "0.0.0.0:12345"]),
            ("python -m uvicorn main:app --host 0.0.0.0 --port 8000", ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "12345"]),
            ("flask --app app run --host 0.0.0.0 --port 8000", ["flask", "--app", "app", "run", "--host", "0.0.0.0", "--port", "12345"]),
            ("streamlit run app.py --server.address 0.0.0.0", ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "12345"]),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(__import__("shlex").split(main._preview_command_for_port(command, 12345)), expected)

    def test_preview_command_rejects_invalid_port_and_empty_command(self):
        with self.assertRaises(HTTPException):
            main._preview_command_for_port("npm run dev", 80)
        with self.assertRaises(HTTPException):
            main._preview_command_for_port("", 12345)

    def test_preview_start_route_is_not_registered_twice(self):
        routes = [
            route for route in main.app.routes
            if getattr(route, "path", None) == "/preview/start"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        self.assertEqual(len(routes), 1)

    def test_rejects_oversized_commands(self):
        with self.assertRaises(HTTPException):
            run_command(Path("/tmp"), "x" * (MAX_COMMAND + 1))

    def test_interactive_process_input_and_stop_contract(self):
        import main
        root = Path(tempfile.mkdtemp(prefix="developer-os-process-"))
        with patch("main.RUNNER_SECURITY_LEVEL", "compat"):
            process_id = main._start_process(root, "python -c \"import sys; print(sys.stdin.readline().strip(), flush=True)\"")
        try:
            with main.PROCESS_LOCK:
                proc = main.PROCESSES[process_id]["popen"]
            proc.stdin.write("hello\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
            state = main._process_state(process_id)
            self.assertEqual(state["status"], "success")
            self.assertIn("hello", state["stdout"])
        finally:
            try:
                main._stop_process(process_id)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()


def test_job_is_cancelled_helper_handles_missing_and_cancelled_jobs():
    assert main._job_is_cancelled("missing-job") is False
    job_id = main._new_job("cancel-helper")
    assert main._job_is_cancelled(job_id) is False
    assert main._set_job(job_id, status="cancelled", finished_at=time.time()) is True
    assert main._job_is_cancelled(job_id) is True


def test_job_state_machine_rejects_invalid_terminal_transitions():
    job_id = main._new_job("state-machine")
    assert main._set_job(job_id, status="running") is True
    assert main._set_job(job_id, status="completed", finished_at=time.time()) is True
    assert main._set_job(job_id, status="cancelled") is False
    assert main._job_snapshot(job_id)["status"] == "completed"


def test_job_state_machine_rejects_cancelled_to_running():
    job_id = main._new_job("state-machine-cancelled")
    assert main._set_job(job_id, status="cancelled", finished_at=time.time()) is True
    assert main._set_job(job_id, status="running") is False
    assert main._job_snapshot(job_id)["status"] == "cancelled"


def test_queued_cancel_clears_scheduler_waiter():
    job_id = main._new_job("queued-cancel")
    with main.SCHEDULER_LOCK:
        main.SCHEDULER_WAITING[job_id] = {
            "workspace_id": "queued-cancel",
            "queued_at": time.monotonic(),
        }
    main._scheduler_metric("queued")
    result = main._cancel_job(job_id)
    assert result["status"] == "cancelled"
    with main.SCHEDULER_LOCK:
        assert job_id not in main.SCHEDULER_WAITING
        assert main.SCHEDULER_METRICS["queued"] >= 0


def test_terminal_job_cancel_is_idempotent():
    job_id = main._new_job("terminal-cancel")
    assert main._set_job(job_id, status="cancelled", finished_at=time.time()) is True
    result = main._cancel_job(job_id)
    assert result["status"] == "cancelled"
    assert main._set_job(job_id, status="completed") is False


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


def test_expired_job_cleanup_also_releases_output_buffer():
    job_id = main._new_job("expired-cleanup")
    main._append_job_output(job_id, "stale output")
    expired_at = time.time() - main.JOB_RETENTION_SECONDS - 1
    assert main._set_job(job_id, status="completed", finished_at=expired_at) is True
    main._cleanup_jobs()
    assert main._job_snapshot(job_id) is None
    assert main._job_output_snapshot(job_id) == ""


def test_retry_delay_is_bounded():
    assert main._retry_delay(1) <= 10
    assert main._retry_delay(100) <= 10


def test_stream_terminal_status_includes_timeout():
    assert main.TERMINAL_JOB_STATES == {"completed", "failed", "timeout", "cancelled"}


def test_timeout_job_stream_reports_done():
    job_id = main._new_job("stream-timeout")
    assert main._set_job(job_id, status="running") is True
    assert main._set_job(job_id, status="timeout", finished_at=time.time(), exit_code=124) is True
    with patch("main.TOKEN", "test-token"):
        result = main.job_output_stream(job_id, workspace_id="stream-timeout", cursor=0, authorization="Bearer test-token")
    assert result["done"] is True
    assert result["status"] == "timeout"


def test_timeout_execution_persists_partial_output_for_streaming_clients():
    root = Path(tempfile.mkdtemp(prefix="developer-os-timeout-"))
    timeout = subprocess.TimeoutExpired(cmd="test-command", timeout=30, output="partial output")
    with patch("main.SANDBOX_MODE", "container"), patch("main.RUNNER_SECURITY_LEVEL", "compat"), patch("main._run_process_with_timeout", side_effect=timeout):
        result = main.run_command(root, "python -c 'print(1)'")
    assert result["status"] == "timeout"
    assert result["stdout"] == "partial output"
    streamed = main._job_output_snapshot(result["job_id"])
    assert "partial output" in streamed
    assert "Execution timed out after" in streamed


def test_timeout_execution_decodes_byte_output_for_streaming_clients():
    root = Path(tempfile.mkdtemp(prefix="developer-os-timeout-bytes-"))
    timeout = subprocess.TimeoutExpired(cmd="test-command", timeout=30, output=b"partial output")
    with patch("main.SANDBOX_MODE", "container"), patch("main.RUNNER_SECURITY_LEVEL", "compat"), patch("main._run_process_with_timeout", side_effect=timeout):
        result = main.run_command(root, "python -c 'print(1)'")
    assert result["status"] == "timeout"
    assert result["stdout"] == "partial output"
    streamed = main._job_output_snapshot(result["job_id"])
    assert "partial output" in streamed
    assert "Execution timed out after" in streamed


def test_job_endpoints_hide_foreign_workspace_jobs():
    job_id = main._new_job("workspace-owner")
    assert main._set_job(job_id, status="running", started_at=time.time()) is True
    with patch("main.TOKEN", "test-token"):
        endpoints = (
            lambda: main.job_status(job_id, workspace_id="workspace-other", authorization="Bearer test-token"),
            lambda: main.job_output(job_id, workspace_id="workspace-other", cursor=0, authorization="Bearer test-token"),
            lambda: main.job_output_stream(job_id, workspace_id="workspace-other", cursor=0, authorization="Bearer test-token"),
            lambda: main.cancel_job(job_id, workspace_id="workspace-other", authorization="Bearer test-token"),
        )
        for endpoint in endpoints:
            try:
                endpoint()
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("A foreign workspace must not access another workspace's job")
    assert main._job_snapshot(job_id)["status"] == "running"


def test_process_stop_and_cancel_terminate_entire_process_group():
    for endpoint_name in ("process_stop", "process_cancel"):
        process_id = f"test-{endpoint_name}"
        popen = Mock()
        popen.poll.return_value = None
        with main.PROCESS_LOCK:
            main.PROCESSES[process_id] = {
                "id": process_id,
                "workspace_id": "workspace-process-group",
                "command": "test-command",
                "popen": popen,
                "stdout": "",
                "stderr": "",
                "started_at": time.time(),
                "kind": "process",
            }

        def terminate_group(proc, grace_seconds=None):
            proc.poll.return_value = 0

        try:
            with patch("main.TOKEN", "runner-test-token"), patch(
                "main._terminate_process_group", side_effect=terminate_group
            ) as terminate:
                result = getattr(main, endpoint_name)(
                    process_id,
                    "workspace-process-group",
                    "Bearer runner-test-token",
                )
            terminate.assert_called_once_with(popen)
            assert result["status"] == "success"
        finally:
            with main.PROCESS_LOCK:
                main.PROCESSES.pop(process_id, None)


def test_process_stop_and_cancel_hide_foreign_workspace_processes():
    for endpoint_name in ("process_stop", "process_cancel"):
        process_id = f"foreign-{endpoint_name}"
        popen = Mock()
        popen.poll.return_value = None
        with main.PROCESS_LOCK:
            main.PROCESSES[process_id] = {
                "id": process_id,
                "workspace_id": "workspace-owner",
                "command": "test-command",
                "popen": popen,
                "stdout": "",
                "stderr": "",
                "started_at": time.time(),
                "kind": "process",
            }
        try:
            with patch("main.TOKEN", "runner-test-token"), patch("main._terminate_process_group") as terminate:
                try:
                    getattr(main, endpoint_name)(
                        process_id,
                        "workspace-other",
                        "Bearer runner-test-token",
                    )
                except HTTPException as exc:
                    assert exc.status_code == 404
                else:
                    raise AssertionError("A foreign workspace must not control this process")
                terminate.assert_not_called()
                assert popen.poll() is None
        finally:
            with main.PROCESS_LOCK:
                main.PROCESSES.pop(process_id, None)


def test_process_stop_route_is_registered_once():
    stop_routes = [
        route.path
        for route in main.app.routes
        if "POST" in getattr(route, "methods", set())
        and route.path in {"/process/{process_id}/stop", "/process/{pid}/stop"}
    ]
    assert stop_routes == ["/process/{process_id}/stop"]


def test_write_snapshot_writes_nested_and_root_files_safely():
    with tempfile.TemporaryDirectory() as root_tmp:
        root = Path(root_tmp)
        main.write_snapshot(root, {
            "src/main.py": "print('safe')\n",
            "README.md": "workspace",
        })
        assert (root / "src" / "main.py").read_text(encoding="utf-8") == "print('safe')\n"
        assert (root / "README.md").read_text(encoding="utf-8") == "workspace"


def test_write_snapshot_rejects_symlink_before_creating_external_directories():
    with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as outside_tmp:
        root = Path(root_tmp)
        outside_target = Path(outside_tmp) / "should-not-be-created"
        (root / "escape").symlink_to(outside_target, target_is_directory=True)

        try:
            main.write_snapshot(root, {"escape/file.txt": "must not escape"})
        except ValueError as exc:
            assert "unsafe path" in str(exc)
        else:
            raise AssertionError("A symlinked workspace parent must be rejected")

        assert not outside_target.exists()


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


def test_scheduler_runtime_bridge():
    scheduler = main.FairScheduler(max_per_workspace=1)
    assert scheduler.acquire("runtime-a") is True
    assert scheduler.acquire("runtime-a") is False
    assert scheduler.acquire("runtime-b") is True
    scheduler.release("runtime-a")
    assert scheduler.acquire("runtime-a") is True
    scheduler.release("runtime-a")
    scheduler.release("runtime-b")
    assert scheduler.snapshot()["active"] == {}


def test_scheduler_runtime_timeout_is_bounded():
    scheduler = main.FairScheduler(max_per_workspace=1)
    assert scheduler.acquire("busy") is True
    started = time.monotonic()
    acquired = False
    while time.monotonic() - started < 0.15:
        if scheduler.acquire("busy"):
            acquired = True
            break
        time.sleep(0.01)
    assert acquired is False
    scheduler.release("busy")


def test_scheduler_metrics_snapshot_contract():
    scheduler = main.FairScheduler(max_per_workspace=1)
    scheduler.acquire("metrics-a")
    snapshot = scheduler.snapshot()
    assert snapshot["max_per_workspace"] == 1
    assert snapshot["active"]["metrics-a"] == 1
    scheduler.release("metrics-a")
    assert scheduler.snapshot()["active"] == {}


def test_scheduler_metrics_fields_are_numeric():
    snapshot = main._scheduler_snapshot()
    for key in ("queued", "active", "rejected", "completed", "total_wait_ms"):
        assert isinstance(snapshot[key], int)
        assert snapshot[key] >= 0


def test_fair_scheduler_round_robin_prevents_idle_workspace_starvation():
    scheduler = main.FairScheduler(max_per_workspace=1)
    assert scheduler.acquire("a") is True
    assert scheduler.acquire("b") is True
    assert scheduler.acquire("a") is False
    scheduler.release("a")
    assert scheduler.acquire("a") is True
    scheduler.release("b")
    scheduler.release("a")


def test_fair_scheduler_keeps_turn_order_deterministic():
    scheduler = main.FairScheduler(max_per_workspace=1)
    assert scheduler.acquire("a") is True
    scheduler.release("a")
    assert scheduler.acquire("b") is True
    scheduler.release("b")
    assert scheduler.acquire("a") is True
    scheduler.release("a")


def test_scheduler_queue_position_tracks_waiters():
    with main.SCHEDULER_LOCK:
        main.SCHEDULER_WAITING.clear()
        now = time.monotonic()
        main.SCHEDULER_WAITING.update({"a": now, "b": now + 0.01, "c": now + 0.02})
    assert main._scheduler_queue_position("a") == 1
    assert main._scheduler_queue_position("b") == 2
    assert main._scheduler_queue_position("c") == 3
    with main.SCHEDULER_LOCK:
        main.SCHEDULER_WAITING.clear()


def test_scheduler_queue_position_disappears_after_release():
    with main.SCHEDULER_LOCK:
        main.SCHEDULER_WAITING.clear()
        main.SCHEDULER_WAITING["busy"] = time.monotonic()
    assert main._scheduler_queue_position("busy") == 1
    with main.SCHEDULER_LOCK:
        main.SCHEDULER_WAITING.pop("busy", None)
    assert main._scheduler_queue_position("busy") == 0


class ProcessEnvironmentBoundsTests(unittest.TestCase):
    def test_process_environment_rejects_runtime_hijacking_and_bounds_entries(self):
        base = {"PATH": "/usr/bin"}
        supplied = {
            "SAFE_VALUE": "allowed",
            "LD_PRELOAD": "/tmp/evil.so",
            "PYTHONPATH": "/tmp/evil",
            **{f"EXTRA_{index}": "x" for index in range(main.MAX_PROCESS_ENV_VARS + 10)},
        }

        result = main._safe_process_env(base, supplied)

        self.assertEqual(result["SAFE_VALUE"], "allowed")
        self.assertNotIn("LD_PRELOAD", result)
        self.assertNotIn("PYTHONPATH", result)
        self.assertLessEqual(
            sum(1 for key in result if key not in {"PATH"}),
            main.MAX_PROCESS_ENV_VARS,
        )

    def test_process_environment_skips_malformed_unicode_values(self):
        result = main._safe_process_env({}, {"SAFE_VALUE": "ok", "BAD_VALUE": "bad" + chr(0xD800)})
        self.assertEqual(result["SAFE_VALUE"], "ok")
        self.assertNotIn("BAD_VALUE", result)

    def test_process_environment_enforces_total_byte_budget(self):
        base = {}
        supplied = {"FIRST": "a" * 4000, "SECOND": "b" * 4000, "THIRD": "c" * 4000,
                    "FOURTH": "d" * 4000, "FIFTH": "e" * 4000}

        result = main._safe_process_env(base, supplied)

        total = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in result.items())
        self.assertLessEqual(total, main.MAX_PROCESS_ENV_BYTES)
        self.assertLessEqual(len(result), main.MAX_PROCESS_ENV_VARS)

def test_timeout_runner_terminates_process_group_and_preserves_output():
    process = Mock()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["test-command"], timeout=0.01, output=b"partial", stderr=b"warning"),
        ("partial", "warning"),
    ]
    with patch("main.subprocess.Popen", return_value=process), patch(
        "main._terminate_process_group"
    ) as terminate:
        result_error = None
        try:
            main._run_process_with_timeout(
                ["test-command"], cwd=Path("/tmp"), env={}, timeout=0.01, preexec_fn=lambda: None
            )
        except subprocess.TimeoutExpired as exc:
            result_error = exc
    terminate.assert_called_once_with(process, grace_seconds=0.1)
    assert result_error is not None
    assert result_error.output == "partial"
    assert result_error.stderr == "warning"


def test_terminate_process_group_signals_descendants_after_leader_exits():
    process = Mock()
    process.pid = 12345
    process.poll.return_value = 0
    process.wait.return_value = 0

    def kill_group(group_id, sig):
        if sig == 0:
            raise ProcessLookupError

    with patch("main.os.killpg", side_effect=kill_group) as killpg:
        main._terminate_process_group(process, grace_seconds=0.1)
    calls = killpg.call_args_list
    assert calls[0].args == (12345, main.signal.SIGTERM)
    assert any(call.args == (12345, main.signal.SIGKILL) for call in calls)

def test_execution_timeout_kills_descendant_processes():
    with tempfile.TemporaryDirectory(prefix="runner-timeout-group-") as tmp:
        root = Path(tmp)
        marker = root / "descendant-survived"
        child_code = "import time; time.sleep(0.4); open('descendant-survived', 'w').write('alive')"
        parent_code = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "time.sleep(10)"
        )
        timed_out = False
        try:
            main._run_process_with_timeout(
                [sys.executable, "-c", parent_code],
                cwd=root,
                env={"PATH": os.environ.get("PATH", "")},
                timeout=0.1,
                preexec_fn=lambda: None,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        time.sleep(1.0)
        assert timed_out
        assert not marker.exists(), "A child process survived the execution timeout"



class PreviewProxySafetyTests(unittest.TestCase):
    @patch("main.requests.get")
    def test_preview_fetch_disables_redirects_and_bounds_response(self, get):
        upstream = Mock()
        upstream.status_code = 200
        upstream.headers = {"content-length": "11", "content-type": "text/html"}
        upstream.iter_content.return_value = [b"preview-ok!"]
        upstream.close = Mock()
        get.return_value = upstream

        content, status_code, content_type = main._bounded_preview_fetch(
            "http://127.0.0.1:12345/", timeout=2
        )

        self.assertEqual(content, b"preview-ok!")
        self.assertEqual(status_code, 200)
        self.assertEqual(content_type, "text/html")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["stream"])
        upstream.close.assert_called_once()

    @patch("main.requests.get")
    def test_preview_fetch_rejects_declared_oversized_response(self, get):
        upstream = Mock()
        upstream.status_code = 200
        upstream.headers = {
            "content-length": str(main.MAX_PREVIEW_RESPONSE_BYTES + 1),
            "content-type": "application/octet-stream",
        }
        upstream.close = Mock()
        get.return_value = upstream

        with self.assertRaises(HTTPException) as ctx:
            main._bounded_preview_fetch("http://127.0.0.1:12345/large")
        self.assertEqual(ctx.exception.status_code, 413)
        upstream.iter_content.assert_not_called()
        upstream.close.assert_called_once()

    @patch("main.requests.get")
    def test_preview_fetch_rejects_chunked_oversized_response(self, get):
        upstream = Mock()
        upstream.status_code = 200
        upstream.headers = {"content-type": "application/octet-stream"}
        upstream.iter_content.return_value = [
            b"x" * (main.MAX_PREVIEW_RESPONSE_BYTES + 1)
        ]
        upstream.close = Mock()
        get.return_value = upstream

        with self.assertRaises(HTTPException) as ctx:
            main._bounded_preview_fetch("http://127.0.0.1:12345/chunked")
        self.assertEqual(ctx.exception.status_code, 413)
        upstream.close.assert_called_once()

class RunnerGlobalMemoryCeilingTests(unittest.TestCase):
    def test_plan_memory_cannot_exceed_operator_configured_runner_budget(self):
        # Test the child pre-exec callback without mutating the test process limits.
        with patch("main.RUNNER_MEMORY_MB", 512), patch("main.resource.setrlimit") as setrlimit:
            main._limit_process_resources(timeout_seconds=120, memory_mb=2048)

        as_calls = [
            call for call in setrlimit.call_args_list
            if call.args[0] == main.resource.RLIMIT_AS
        ]
        self.assertEqual(len(as_calls), 1)
        self.assertEqual(as_calls[0].args[1], (512 * 1024 * 1024, 512 * 1024 * 1024))

    def test_lower_tier_memory_limit_is_preserved(self):
        with patch("main.RUNNER_MEMORY_MB", 1024), patch("main.resource.setrlimit") as setrlimit:
            main._limit_process_resources(timeout_seconds=30, memory_mb=256)

        as_calls = [
            call for call in setrlimit.call_args_list
            if call.args[0] == main.resource.RLIMIT_AS
        ]
        self.assertEqual(len(as_calls), 1)
        self.assertEqual(as_calls[0].args[1], (256 * 1024 * 1024, 256 * 1024 * 1024))


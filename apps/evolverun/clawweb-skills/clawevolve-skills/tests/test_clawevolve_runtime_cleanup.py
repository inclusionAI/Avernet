import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "clawevolve_runtime_cleanup.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_runtime_cleanup", MODULE_PATH)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def _write_pid(results_root: Path, task_id: str, step_id: str, value: str) -> Path:
    path = results_root / task_id / "runner_state" / step_id / "pid"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


class RuntimeCleanupGuardTests(unittest.TestCase):
    def test_active_runner_requires_live_pid_and_matching_step_cmdline(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            results = base / "results"
            proc = base / "proc"
            _write_pid(results, "EV-OTHER", "STEP-OTHER", "4321\n")
            (proc / "4321").mkdir(parents=True)
            (proc / "4321" / "cmdline").write_bytes(b"python\0--step-id\0STEP-OTHER\0")

            with mock.patch.object(MOD.os, "kill", return_value=None):
                active = MOD._active_evolve_runners(
                    results,
                    current_task_id="EV-CLEAN",
                    current_step_id="STEP-CLEAN",
                    current_pid=9999,
                    proc_root=proc,
                )

            self.assertEqual(
                active,
                [{"taskId": "EV-OTHER", "stepId": "STEP-OTHER", "pid": 4321}],
            )

    def test_current_runner_is_the_only_excluded_pid(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            results = Path(root) / "results"
            _write_pid(results, "EV-CLEAN", "STEP-CLEAN", str(os.getpid()))

            with mock.patch.object(MOD.os, "kill") as kill:
                active = MOD._active_evolve_runners(
                    results,
                    current_task_id="EV-CLEAN",
                    current_step_id="STEP-CLEAN",
                    current_pid=os.getpid(),
                )

            self.assertEqual(active, [])
            kill.assert_not_called()

    def test_stale_reused_and_malformed_pids_do_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            results = base / "results"
            proc = base / "proc"
            _write_pid(results, "EV-STALE", "STEP-STALE", "1234")
            _write_pid(results, "EV-REUSED", "STEP-REUSED", "2345")
            _write_pid(results, "EV-BAD", "STEP-BAD", "12x3")
            (proc / "2345").mkdir(parents=True)
            (proc / "2345" / "cmdline").write_bytes(b"python\0--step-id\0STEP-SOMEONE-ELSE\0")

            def inspect(pid: int, signal: int) -> None:
                self.assertEqual(signal, 0)
                if pid == 1234:
                    raise ProcessLookupError

            with mock.patch.object(MOD.os, "kill", side_effect=inspect) as kill:
                active = MOD._active_evolve_runners(
                    results,
                    current_task_id="EV-CLEAN",
                    current_step_id="STEP-CLEAN",
                    current_pid=9999,
                    proc_root=proc,
                )

            self.assertEqual(active, [])
            self.assertEqual([call.args[0] for call in kill.call_args_list], [2345, 1234])

    def test_force_cleanup_does_not_bypass_active_runner(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            openclaw_home = Path(root) / ".openclaw"
            lock_state = {"held": False}
            reports = []

            @contextmanager
            def fake_lock(_home: Path):
                lock_state["held"] = True
                try:
                    yield
                finally:
                    lock_state["held"] = False

            def fake_report(_args, payload):
                self.assertFalse(lock_state["held"])
                reports.append(payload)

            active = [{"taskId": "EV-ACTIVE", "stepId": "STEP-ACTIVE", "pid": 4567}]
            argv = [
                "clawevolve_runtime_cleanup.py",
                "--task-id",
                "EV-CLEAN",
                "--step-id",
                "STEP-CLEAN",
                "--force-cleanup",
            ]
            with (
                mock.patch.object(MOD, "OPENCLAW_HOME", openclaw_home),
                mock.patch.object(MOD, "_environment_lock", fake_lock),
                mock.patch.object(MOD, "_active_evolve_runners", return_value=active),
                mock.patch.object(MOD, "_load_cleaner") as load_cleaner,
                mock.patch.object(MOD, "_report", side_effect=fake_report),
                mock.patch.object(sys, "argv", argv),
            ):
                exit_code = MOD.main()

            self.assertEqual(exit_code, 1)
            load_cleaner.assert_not_called()
            failure_path = openclaw_home / "workspace/clawevolve_results/EV-CLEAN/cleanup/output/failure.json"
            result_path = failure_path.with_name("cleanup_result.json")
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertFalse(result_path.exists())
            self.assertEqual(failure["error"]["code"], "ACTIVE_EVOLVE_TASK_EXISTS")
            self.assertEqual(failure["error"]["activeTasks"], active)
            self.assertEqual(reports[0]["status"], "failed")

    def test_idle_runtime_uses_cleaner_active_check_and_reports_after_unlock(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            openclaw_home = Path(root) / ".openclaw"
            lock_state = {"held": False}
            cleaner = mock.Mock()
            cleaner.cleanup_runtime.return_value = {"status": "ok", "listed": 1}

            @contextmanager
            def fake_lock(_home: Path):
                lock_state["held"] = True
                try:
                    yield
                finally:
                    lock_state["held"] = False

            def fake_report(_args, payload):
                self.assertFalse(lock_state["held"])
                self.assertEqual(payload["status"], "succeeded")

            argv = [
                "clawevolve_runtime_cleanup.py",
                "--task-id",
                "EV-CLEAN",
                "--step-id",
                "STEP-CLEAN",
            ]
            with (
                mock.patch.object(MOD, "OPENCLAW_HOME", openclaw_home),
                mock.patch.object(MOD, "_environment_lock", fake_lock),
                mock.patch.object(MOD, "_active_evolve_runners", return_value=[]),
                mock.patch.object(MOD, "_load_cleaner", return_value=cleaner),
                mock.patch.object(MOD, "_report", side_effect=fake_report),
                mock.patch.object(sys, "argv", argv),
            ):
                exit_code = MOD.main()

            self.assertEqual(exit_code, 0)
            kwargs = cleaner.cleanup_runtime.call_args.kwargs
            self.assertFalse(kwargs["skip_active_check"])
            self.assertTrue(kwargs["strict_markers"])
            self.assertEqual(kwargs["excluded_session_markers"], ("EV-CLEAN", "STEP-CLEAN"))

    def test_degraded_cleanup_reports_failed_after_all_candidates_are_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            openclaw_home = Path(root) / ".openclaw"
            reports = []
            cleaner = mock.Mock()
            cleaner.cleanup_runtime.return_value = {
                "status": "degraded",
                "listed": 2,
                "candidate_agent_count": 2,
                "deleted_agent_count": 1,
                "failures": [{"agent_id": "agent-1", "error": "permission denied"}],
            }

            argv = [
                "clawevolve_runtime_cleanup.py",
                "--task-id",
                "EV-CLEAN",
                "--step-id",
                "STEP-CLEAN",
            ]
            with (
                mock.patch.object(MOD, "OPENCLAW_HOME", openclaw_home),
                mock.patch.object(MOD, "_active_evolve_runners", return_value=[]),
                mock.patch.object(MOD, "_load_cleaner", return_value=cleaner),
                mock.patch.object(MOD, "_report", side_effect=lambda _args, payload: reports.append(payload)),
                mock.patch.object(sys, "argv", argv),
            ):
                exit_code = MOD.main()

            self.assertEqual(exit_code, 1)
            self.assertEqual(reports[0]["status"], "failed")
            self.assertEqual(
                reports[0]["error"]["code"],
                "RUNTIME_CLEANUP_PARTIAL_FAILURE",
            )
            self.assertEqual(reports[0]["output"]["deletedAgentCount"], 1)


class OpenVersionCleanupTests(unittest.TestCase):
    def test_explicit_openversion_uses_selected_bot_without_activity_scan(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / "bot"
            workspace = home / "workspace"
            workspace.mkdir(parents=True)
            (home / "openclaw.json").write_text(json.dumps({"agents": {"defaults": {"workspace": str(workspace)}}}))
            cleaner = mock.Mock()
            cleaner.cleanup_runtime.return_value = {"status": "ok", "listed": 1}
            argv = ["cleanup", "--version", "openversion", "--openclaw-home", str(home),
                    "--workspace", str(workspace), "--force-cleanup", "--task-id", "EV-LOCAL", "--step-id", "STEP-LOCAL"]
            with (mock.patch.object(sys, "argv", argv),
                  mock.patch.object(MOD, "_active_evolve_runners", side_effect=AssertionError("must not scan /proc")) as active,
                  mock.patch.object(MOD, "_load_cleaner", return_value=cleaner),
                  mock.patch.object(MOD, "_report") as report):
                self.assertEqual(MOD.main(), 0)
            active.assert_not_called()
            cleaner.cleanup_runtime.assert_called_once_with(
                openclaw_path="openclaw", openclaw_home=home.resolve(), strict_markers=True,
                skip_active_check=True, excluded_session_markers=("EV-LOCAL", "STEP-LOCAL"))
            self.assertEqual(report.call_args.args[1]["output"]["version"], "openversion")
            self.assertFalse(report.call_args.args[1]["output"]["gatewayRestart"])

    def test_internalversion_does_not_accept_local_path_overrides(self):
        argv = ["cleanup", "--task-id", "EV-LOCAL", "--step-id", "STEP-LOCAL", "--openclaw-home", "/tmp/bot"]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
            MOD.main()

    def test_openversion_requires_confirmation_and_paths(self):
        argv = ["cleanup", "--version", "openversion", "--task-id", "EV-LOCAL", "--step-id", "STEP-LOCAL"]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
            MOD.main()


if __name__ == "__main__":
    unittest.main()

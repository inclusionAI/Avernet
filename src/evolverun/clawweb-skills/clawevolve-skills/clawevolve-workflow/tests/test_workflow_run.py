import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_workflow_run", SCRIPT)
assert SPEC and SPEC.loader
workflow_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow_run)


class WorkflowRunTests(unittest.TestCase):
    def _exec_argv(self, *args: str) -> list[str]:
        with mock.patch.object(sys, "argv", [str(SCRIPT), *args]), mock.patch.object(
            workflow_run.os, "execv", side_effect=RuntimeError("captured")
        ) as execute:
            with self.assertRaisesRegex(RuntimeError, "captured"):
                workflow_run.main()
        return execute.call_args.args[1]

    def test_routes_bench_plan_to_its_handler(self):
        argv = self._exec_argv(
            "--stage", "bench-plan", "--task-id", "EV-1", "--step-id", "STEP-1",
            "--model", "antchat/GLM-5",
        )
        self.assertTrue(argv[3].endswith("clawevolve_bench_plan_run.py"))
        self.assertEqual(argv[-2:], ["--model", "antchat/GLM-5"])

    def test_routes_optimize_and_injects_action(self):
        argv = self._exec_argv(
            "--stage", "optimize", "--task-id", "EV-2", "--step-id", "STEP-2", "--round", "3",
        )
        self.assertTrue(argv[3].endswith("clawevolve_optimize_run.py"))
        self.assertEqual(argv[4:6], ["--action", "run-round"])
        self.assertEqual(argv[-2:], ["--round", "3"])

    def test_routes_timeout_overrides_to_optimize_handler(self):
        argv = self._exec_argv(
            "--stage", "optimize", "--task-id", "EV-2", "--step-id", "STEP-2",
            "--tune-timeout", "7200", "--agent-idle-timeout", "0", "--round-timeout", "0",
        )
        self.assertIn("--tune-timeout", argv)
        self.assertIn("--agent-idle-timeout", argv)
        self.assertEqual(argv[-2:], ["--round-timeout", "0"])

    def test_rejects_invalid_protocol_id(self):
        with mock.patch.object(sys, "argv", [str(SCRIPT), "--stage", "optimize", "--task-id", "../bad", "--step-id", "STEP-1"]):
            with self.assertRaisesRegex(SystemExit, "invalid task-id"):
                workflow_run.main()

    def test_rejects_action_override_for_optimize(self):
        with mock.patch.object(sys, "argv", [
            str(SCRIPT), "--stage", "optimize", "--task-id", "EV-2", "--step-id", "STEP-2",
            "--action", "prepare",
        ]):
            with self.assertRaisesRegex(SystemExit, "controlled by router: --action"):
                workflow_run.main()

    def test_rejects_action_equals_override_for_optimize(self):
        with mock.patch.object(sys, "argv", [
            str(SCRIPT), "--stage", "optimize", "--task-id", "EV-2", "--step-id", "STEP-2",
            "--action=prepare",
        ]):
            with self.assertRaisesRegex(SystemExit, "controlled by router: --action"):
                workflow_run.main()


if __name__ == "__main__":
    unittest.main()

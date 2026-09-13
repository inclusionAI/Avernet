import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_optimize_run_timeout_tests", SCRIPT)
assert SPEC and SPEC.loader
handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handler)


class TimeoutPolicyTests(unittest.TestCase):
    def test_zero_disables_elapsed_timeout(self):
        self.assertFalse(handler._elapsed_timeout_exceeded(1.0, 0, now=100000.0))
        self.assertFalse(handler._elapsed_timeout_exceeded(1.0, -1, now=100000.0))
        self.assertTrue(handler._elapsed_timeout_exceeded(1.0, 10, now=12.0))

    def test_openclaw_command_overrides_builtin_600_second_timeout(self):
        command = handler._build_openclaw_agent_command(
            "openclaw", "agent-a", "session-a", "do work", 7200,
        )
        self.assertEqual(command[-2:], ["--timeout", "7200"])
        self.assertIn("--json", command)
        self.assertIn("--local", command)

    def test_openclaw_command_can_leave_timeout_unset_when_disabled(self):
        command = handler._build_openclaw_agent_command(
            "openclaw", "agent-a", "session-a", "do work", 0,
        )
        self.assertNotIn("--timeout", command)
        self.assertIn("--local", command)

    def test_openclaw_command_can_explicitly_use_gateway(self):
        command = handler._build_openclaw_agent_command(
            "openclaw", "agent-a", "session-a", "do work", 7200, local=False,
        )
        self.assertNotIn("--local", command)

    def test_stdout_idle_timeout_can_be_disabled(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.05)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            result = handler._collect_agent_output_with_timeout(
                proc,
                timeout_seconds=0.3,
                idle_timeout=0,
            )
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
        returncode, _stdout, _stderr, timed_out_on, _marker, completed_by = result
        self.assertEqual(returncode, 0)
        self.assertIsNone(timed_out_on)
        self.assertEqual(completed_by, "process_exit")


if __name__ == "__main__":
    unittest.main()

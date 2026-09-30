from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lib_openclaw_cli  # noqa: E402


class OpenClawCliTests(unittest.TestCase):
    def test_default_mode_is_local(self) -> None:
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        with mock.patch.dict("os.environ", {}, clear=True):
            result = lib_openclaw_cli.run_openclaw_agent(
                agent_id="agent-a", session_id="session-a", message="hello",
                workspace=Path("/tmp/work"), timeout_seconds=30, runner=runner,
            )
        self.assertEqual(result.returncode, 0)
        command = runner.call_args.args[0]
        self.assertEqual(command[:3], ["openclaw", "agent", "--local"])
        self.assertEqual(command[command.index("--message") + 1], "hello")

    def test_gateway_mode_omits_local(self) -> None:
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        lib_openclaw_cli.run_openclaw_agent(
            agent_id="agent-a", session_id="session-a", message="hello",
            workspace=Path("/tmp/work"), timeout_seconds=30,
            mode="gateway", json_output=True, runner=runner,
        )
        command = runner.call_args.args[0]
        self.assertNotIn("--local", command)
        self.assertIn("--json", command)

    def test_gateway_retries_only_unknown_agent(self) -> None:
        runner = mock.Mock(side_effect=[
            subprocess.CompletedProcess([], 1, "", "Unknown agent id: agent-a"),
            subprocess.CompletedProcess([], 0, "ok", ""),
        ])
        sleep = mock.Mock()
        result = lib_openclaw_cli.run_openclaw_agent(
            agent_id="agent-a", session_id="session-a", message="hello",
            workspace=Path("/tmp/work"), timeout_seconds=30,
            mode="gateway", runner=runner, sleep=sleep,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(runner.call_count, 2)
        sleep.assert_called_once_with(10.0)

    def test_gateway_does_not_retry_other_failures(self) -> None:
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", "unauthorized"))
        sleep = mock.Mock()
        result = lib_openclaw_cli.run_openclaw_agent(
            agent_id="agent-a", session_id="session-a", message="hello",
            workspace=Path("/tmp/work"), timeout_seconds=30,
            mode="gateway", runner=runner, sleep=sleep,
        )
        self.assertEqual(result.returncode, 1)
        runner.assert_called_once()
        sleep.assert_not_called()

    def test_invalid_mode_fails_before_execution(self) -> None:
        runner = mock.Mock()
        with self.assertRaisesRegex(ValueError, "local.*gateway"):
            lib_openclaw_cli.run_openclaw_agent(
                agent_id="agent-a", session_id="session-a", message="hello",
                workspace=Path("/tmp/work"), timeout_seconds=30,
                mode="invalid", runner=runner,
            )
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()

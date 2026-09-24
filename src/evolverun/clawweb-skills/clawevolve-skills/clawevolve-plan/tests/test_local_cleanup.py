from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawevolve_plan.discovery.agent import _cleanup_registered_agent
from clawevolve_plan.discovery.cleanup import agent_cleanup_command


class LocalCleanupTests(unittest.TestCase):
    def test_local_unregisters_without_deleting_task_files(self):
        cmd = agent_cleanup_command("openclaw", "plan-test", {
            "SECBAAS_SANDBOX_BACKEND": "local_proc",
        })
        self.assertEqual(cmd[1:4], ["gateway", "call", "agents.delete"])
        self.assertEqual(json.loads(cmd[cmd.index("--params") + 1]), {
            "agentId": "plan-test", "deleteFiles": False,
        })

    def test_existing_container_and_openversion_commands_unchanged(self):
        for env in ({}, {"SECBAAS_SANDBOX_BACKEND": "arca"}, {
            "SECBAAS_SANDBOX_BACKEND": "local_proc", "CLAWWEB_VERSION": "openversion",
        }):
            with self.subTest(env=env):
                self.assertEqual(agent_cleanup_command("openclaw", "plan-test", env), [
                    "openclaw", "agents", "delete", "plan-test", "--force", "--json",
                ])

    def test_failed_local_cleanup_never_falls_back_to_destructive_cli(self):
        diagnostics = {}
        with patch("clawevolve_plan.discovery.agent._run_command", return_value={
            "returncode": 1, "stdout": "", "stderr": "gateway unavailable",
            "timed_out": False, "elapsed": 0.01,
        }) as run:
            _cleanup_registered_agent("openclaw", "plan-test", {
                "SECBAAS_SANDBOX_BACKEND": "local_proc",
            }, diagnostics)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(diagnostics["agentCleanup"], "delete-failed")


if __name__ == "__main__":
    unittest.main()

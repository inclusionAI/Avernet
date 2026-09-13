from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.discovery.agent import (  # noqa: E402
    DiscoveryAgentError,
    _json_response_text,
    _run_cli_agent_message,
)


def command_result(
    returncode: int,
    *,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> dict:
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "elapsed": 0.01,
    }


def registry_json(*agent_ids: str) -> str:
    return json.dumps({"agents": [{"id": agent_id} for agent_id in agent_ids]})


class DiscoveryAgentTransportTests(unittest.TestCase):
    def _run(self, workspace: Path, *, agent_id: str = "plan-agent"):
        return _run_cli_agent_message(
            agent_id=agent_id,
            session_id="session-1",
            message="discover",
            workspace=workspace,
            timeout_seconds=60,
            output_path=workspace / "discovery.json",
        )

    def test_registers_once_runs_local_and_deletes_owned_agent(self):
        agent_id = "clawevolve-plan-EV-1"
        commands: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands.append(cmd)
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0, stdout="valid")
            if cmd[1:4] == ["agents", "list", "--json"]:
                listed = agent_id if len([c for c in commands if c[1:4] == ["agents", "list", "--json"]]) > 1 else None
                return command_result(0, stdout=registry_json(*([listed] if listed else [])))
            if cmd[1:3] == ["agents", "add"]:
                return command_result(0, stdout=json.dumps({"id": agent_id}))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(0, stdout=json.dumps({"response": "done"}))
            if cmd[1:3] == ["agents", "delete"]:
                return command_result(0, stdout=json.dumps({"deleted": True}))
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-local-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.transport, "local")
        self.assertEqual(result.response_text, "done")
        self.assertEqual(result.diagnostics["agentRegistration"], "created-ok")
        self.assertEqual(result.diagnostics["agentExecution"], "local-success")
        self.assertEqual(result.diagnostics["agentCleanup"], "deleted")
        add_cmd = next(cmd for cmd in commands if cmd[1:3] == ["agents", "add"])
        self.assertIn("--json", add_cmd)
        self.assertNotIn("--api-key", add_cmd)
        self.assertNotIn("--base-url", add_cmd)
        local_cmd = next(cmd for cmd in commands if cmd[1:3] == ["agent", "--local"])
        self.assertIn("--timeout", local_cmd)

    def test_extracts_text_from_openclaw_payloads_stdout(self):
        agent_id = "clawevolve-plan-payloads"
        expected = '```json\n{"contracts": []}\n```'

        def fake_run(cmd, **_kwargs):
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(
                    0,
                    stdout=json.dumps(
                        {"payloads": [{"text": expected}], "meta": {}}
                    ),
                )
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-payloads-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.response_text, expected)
        self.assertEqual(result.agent_json["payloads"][0]["text"], expected)

    def test_extracts_payloads_nested_in_result_and_prefers_final_text(self):
        response = {
            "result": {
                "payloads": [
                    {"status": "running"},
                    {"text": "first answer"},
                    {"text": "final answer"},
                ]
            }
        }

        self.assertEqual(_json_response_text(response), "final answer")

    def test_existing_agent_runs_local_without_delete(self):
        agent_id = "clawevolve-plan-existing"
        commands: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands.append(cmd)
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(0, stdout=json.dumps({"response": "done"}))
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-existing-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.diagnostics["agentRegistration"], "already-exists")
        self.assertNotIn("agentCleanup", result.diagnostics)
        self.assertFalse(any(cmd[1:3] == ["agents", "delete"] for cmd in commands))

    def test_extracts_final_assistant_text_from_openclaw_payloads_envelope(self):
        agent_id = "clawevolve-plan-payloads"
        expected = json.dumps(
            {"contracts": [{"case_id": "case-1"}]}, ensure_ascii=False
        )

        def fake_run(cmd, **_kwargs):
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(
                    0,
                    stdout=json.dumps(
                        {
                            "payloads": [
                                {"role": "user", "text": "ignored prompt"},
                                {"role": "assistant", "text": "intermediate"},
                                {"role": "tool", "text": "ignored tool result"},
                                {"role": "assistant", "text": expected},
                            ],
                            "meta": {"status": "success"},
                        },
                        ensure_ascii=False,
                    ),
                )
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-payloads-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.response_text, expected)

    def test_payloads_envelope_falls_back_to_legacy_response(self):
        agent_id = "clawevolve-plan-empty-payloads"

        def fake_run(cmd, **_kwargs):
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(
                    0,
                    stdout=json.dumps(
                        {
                            "payloads": [{"role": "tool", "text": "ignored"}],
                            "response": "legacy response",
                        }
                    ),
                )
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-legacy-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.response_text, "legacy response")

    def test_invalid_config_fails_before_registry_or_add(self):
        with tempfile.TemporaryDirectory(prefix="plan-agent-invalid-config-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command",
            return_value=command_result(1, stderr="config is invalid"),
        ) as run_command:
            with self.assertRaisesRegex(DiscoveryAgentError, "config validation failed"):
                self._run(Path(td))

        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(
            run_command.call_args.args[0], ["openclaw", "config", "validate", "--json"]
        )

    def test_registry_command_failure_is_not_treated_as_agent_absence(self):
        responses = [
            command_result(0),
            command_result(1, stderr="registry unavailable"),
        ]
        with tempfile.TemporaryDirectory(prefix="plan-agent-registry-error-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=responses
        ) as run_command:
            with self.assertRaisesRegex(DiscoveryAgentError, "refusing to infer"):
                self._run(Path(td))

        self.assertEqual(run_command.call_count, 2)

    def test_local_auth_failure_waits_for_gateway_visibility_then_falls_back(self):
        agent_id = "clawevolve-plan-EV-2"
        gateway_list_calls = 0

        def fake_run(cmd, **_kwargs):
            nonlocal gateway_list_calls
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(1, stderr="missing provider API key")
            if cmd[1:4] == ["gateway", "call", "agents.list"]:
                gateway_list_calls += 1
                visible = gateway_list_calls >= 2
                return command_result(
                    0,
                    stdout=registry_json(*([agent_id] if visible else [])),
                )
            if cmd[1] == "agent" and "--local" not in cmd:
                return command_result(0, stdout=json.dumps({"response": "gateway done"}))
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-gateway-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ), patch(
            "clawevolve_plan.discovery.agent.time.sleep"
        ) as sleep:
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.transport, "gateway")
        self.assertEqual(result.response_text, "gateway done")
        self.assertTrue(result.diagnostics["gatewayAgentVisible"])
        self.assertEqual(result.diagnostics["agentExecution"], "gateway-success")
        sleep.assert_called_once_with(0.5)

    def test_gateway_not_synced_returns_precise_failure(self):
        agent_id = "clawevolve-plan-EV-3"

        def fake_run(cmd, **_kwargs):
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(1, stderr="no provider credentials")
            if cmd[1:4] == ["gateway", "call", "agents.list"]:
                return command_result(0, stdout=registry_json())
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-not-synced-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ), patch(
            "clawevolve_plan.discovery.agent._GATEWAY_VISIBILITY_DELAYS_SECONDS",
            (0.01,),
        ), patch("clawevolve_plan.discovery.agent.time.sleep"):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.diagnostics["failureCode"],
            "gateway_agent_registry_not_synced",
        )
        self.assertIn("did not expose", result.stderr)

    def test_non_auth_local_failure_does_not_mask_error_with_gateway(self):
        agent_id = "clawevolve-plan-EV-4"
        commands: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands.append(cmd)
            if cmd[1:3] == ["config", "validate"]:
                return command_result(0)
            if cmd[1:4] == ["agents", "list", "--json"]:
                return command_result(0, stdout=registry_json(agent_id))
            if cmd[1:3] == ["agent", "--local"]:
                return command_result(1, stderr="prompt contract validation failed")
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory(prefix="plan-agent-no-fallback-") as td, patch(
            "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
        ), patch(
            "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
        ):
            result = self._run(Path(td), agent_id=agent_id)

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.diagnostics["agentExecution"], "local-failed-no-fallback"
        )
        self.assertFalse(any(cmd[1:3] == ["gateway", "call"] for cmd in commands))

    def test_add_failure_but_listed_is_tolerated_and_bootstrap_is_restored(self):
        agent_id = "clawevolve-plan-EV-5"
        list_calls = 0

        with tempfile.TemporaryDirectory(prefix="plan-agent-bootstrap-") as td:
            workspace = Path(td)
            agents_md = workspace / "AGENTS.md"
            agents_md.write_text("original\n", encoding="utf-8")

            def fake_run(cmd, **_kwargs):
                nonlocal list_calls
                if cmd[1:3] == ["config", "validate"]:
                    return command_result(0)
                if cmd[1:4] == ["agents", "list", "--json"]:
                    list_calls += 1
                    return command_result(
                        0,
                        stdout=registry_json(*([agent_id] if list_calls > 1 else [])),
                    )
                if cmd[1:3] == ["agents", "add"]:
                    agents_md.write_text("overwritten\n", encoding="utf-8")
                    (workspace / "BOOTSTRAP.md").write_text(
                        "generated\n", encoding="utf-8"
                    )
                    return command_result(1, stderr="write raced")
                if cmd[1:3] == ["agent", "--local"]:
                    return command_result(0, stdout=json.dumps({"response": "done"}))
                if cmd[1:3] == ["agents", "delete"]:
                    return command_result(0)
                raise AssertionError(cmd)

            with patch(
                "clawevolve_plan.discovery.agent._openclaw_env", return_value={}
            ), patch(
                "clawevolve_plan.discovery.agent._run_command", side_effect=fake_run
            ):
                result = self._run(workspace, agent_id=agent_id)

            self.assertEqual(
                result.diagnostics["agentRegistration"],
                "created-tolerant(add-failed-but-listed)",
            )
            self.assertEqual(
                result.diagnostics["workspaceBootstrapCleanedAfterAdd"],
                ["restored-existing:AGENTS.md", "removed-new:BOOTSTRAP.md"],
            )
            self.assertEqual(agents_md.read_text(encoding="utf-8"), "original\n")
            self.assertFalse((workspace / "BOOTSTRAP.md").exists())


if __name__ == "__main__":
    unittest.main()

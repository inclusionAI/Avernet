"""Shared five-Bot coding execution double; workers report artifacts through G4.

Used by both the Swarm and Open + Swarm journeys. Only computation is simulated;
verification runs real unittest cases and protocol services own state changes.
"""

import json
import subprocess
import sys

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageExecutionReport,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from tests.community.core.task.context_collab.products.swarm_coding_support import (
    ARTIFACTS,
    ROLES,
)

AGENTS = tuple(AgentRef("bot", f"coding_{role}") for role in ROLES)


class LocalStageExecutionService:
    """Only the execution/evidence double knows coding artifacts and the filesystem."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.calls = []

    def execute(
        self, stage, participant, profile, allocation_ref, collet_id, binding_id
    ):
        assert profile.target_ref == participant.participant_id
        assert profile.supported_work_kinds == ("EXECUTE",)
        assert profile.input_contract_ref == "coding-input/1"
        assert profile.output_contract_ref == "coding-output/1"
        self.calls.append((stage, participant, profile))
        output = _simulate_agent(stage.name, participant, self.workspace)
        report = StageExecutionReport(
            collet_id,
            binding_id,
            stage.stage_id,
            allocation_ref,
            (f"artifact://{output.name}",),
        )
        actor = ActorContext(
            PrincipalRef(
                participant.agent_ref.agent_type, participant.agent_ref.agent_id
            ),
            source="local_agent",
        )
        self.reports.report(report, actor)

    def accepts(self, stage, output):
        expected = self.workspace / stage.completion_condition["required_artifact"]
        if (
            output.artifact_refs != (f"artifact://{expected.name}",)
            or not expected.is_file()
            or not expected.read_text()
        ):
            return False
        if stage.name == "verification":
            report = json.loads(expected.read_text())
            return report["exit_code"] == 0 and "Ran 4 tests" in report["output"]
        return True


def _simulate_agent(role, participant, workspace):
    """Agent 执行替身；显式读前序产物，测试 Agent 真正运行 unittest。"""
    index = ROLES.index(role)
    if index:
        assert (workspace / ARTIFACTS[index - 1]).read_text()
    if role == "requirements":
        text = "sum_numbers(values): 空列表为 0，支持负数与浮点数，不修改输入。\n"
    elif role == "design":
        assert "空列表" in (workspace / "requirements.md").read_text()
        text = "采用 Python sum(values)，使用 unittest 验证边界，无外部依赖。\n"
    elif role == "coding":
        assert "unittest" in (workspace / "design.md").read_text()
        text = "def sum_numbers(values):\n    return sum(values)\n"
    elif role == "verification":
        (workspace / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import sum_numbers\n"
            "class CalculatorTest(unittest.TestCase):\n"
            "    def test_empty(self): self.assertEqual(sum_numbers([]), 0)\n"
            "    def test_values(self): self.assertEqual(sum_numbers([1, 2, 3]), 6)\n"
            "    def test_negative_float(self): self.assertEqual(sum_numbers([-2, 0.5]), -1.5)\n"
            "    def test_unchanged(self):\n"
            "        values = [1, 2]\n        sum_numbers(values)\n        self.assertEqual(values, [1, 2])\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "test_calculator"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        text = json.dumps(
            {
                "exit_code": result.returncode,
                "output": result.stderr,
                "agent": participant.agent_ref.agent_id,
            }
        )
    else:
        report = json.loads((workspace / "test-report.json").read_text())
        assert report["exit_code"] == 0
        assert "Ran 4 tests" in report["output"]
        text = json.dumps(
            {
                "simulated": True,
                "title": "Add calculator sum",
                "files": ["calculator.py", "test_calculator.py"],
                "test_report": "test-report.json",
            }
        )
    output = workspace / ARTIFACTS[index]
    output.write_text(text)
    return output

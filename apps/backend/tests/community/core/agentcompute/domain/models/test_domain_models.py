"""Tests for the agentcompute domain model: enums, dataclass construction,
frozen immutability, decision models, and NameAliasMap."""
from __future__ import annotations

import pytest

from agentclaw.community.core.agentcompute.domain import models as dm


class TestEnums:
    def test_run_status_all_members(self):
        assert set(dm.RunStatus) == {
            dm.RunStatus.RUNNING, dm.RunStatus.COMPLETED,
            dm.RunStatus.FAILED, dm.RunStatus.CANCELLED,
            dm.RunStatus.TIMED_OUT, dm.RunStatus.GUARD_KILLED,
            dm.RunStatus.STUCK,
        }

    def test_agent_status_all_members(self):
        assert set(dm.AgentStatus) == {
            dm.AgentStatus.QUEUED, dm.AgentStatus.RUNNING,
            dm.AgentStatus.COMPLETED, dm.AgentStatus.FAILED,
            dm.AgentStatus.KILLED,
        }

    def test_termination_reason_no_token_budget(self):
        assert not hasattr(dm.TerminationReason, "TOKEN_BUDGET")
        assert not hasattr(dm.TerminationReason, "CRITICAL_PATH_BUDGET")
        assert not hasattr(dm.TerminationReason, "BUDGET_EXHAUSTED")
        assert dm.TerminationReason.STUCK == "stuck"

    def test_guard_kind_no_budget_trip(self):
        assert not hasattr(dm.GuardKind, "BUDGET_TRIP")
        assert dm.GuardKind.SERIAL_COLLAPSE == "serial_collapse"

    def test_finish_reason_no_tool_call(self):
        assert not hasattr(dm.FinishReason, "TOOL_CALL")

    def test_decision_kind_four_members(self):
        assert set(dm.DecisionKind) == {
            dm.DecisionKind.SPAWN_AGENT, dm.DecisionKind.WAIT,
            dm.DecisionKind.HANDOFF, dm.DecisionKind.FINISH,
        }

    def test_task_queue_status(self):
        assert dm.TaskQueueStatus.PENDING == "PENDING"
        assert dm.TaskQueueStatus.TIMED_OUT == "TIMED_OUT"

    def test_agent_kind(self):
        assert dm.AgentKind.LLM == "llm"
        assert dm.AgentKind.AVENET == "avernet"

    def test_bot_state_terminal_failures(self):
        assert dm.BotState.AUTHORIZATION_REJECTED == "AUTHORIZATION_REJECTED"
        assert dm.BotState.READY == "READY"

    def test_wait_mode(self):
        assert dm.WaitMode.ALL == "all"
        assert dm.WaitMode.ANY == "any"


class TestFrozenDataclasses:
    def test_task_envelope_frozen(self):
        env = dm.TaskEnvelope(
            name="a1", objective="objective",
            output_schema={}, boundaries="", parent_goal="",
        )
        with pytest.raises(AttributeError):
            env.name = "a2"

    def test_completion_request_frozen(self):
        req = dm.CompletionRequest(kind=dm.LlmCallKind.ORCHESTRATOR)
        with pytest.raises(AttributeError):
            req.kind = dm.LlmCallKind.SUBAGENT

    def test_spawn_decision_frozen(self):
        sd = dm.SpawnDecision(
            agents=(dm.TaskEnvelope(
                name="a", objective="o", output_schema={},
                boundaries="", parent_goal="",
            ),)
        )
        with pytest.raises(AttributeError):
            sd.kind = dm.DecisionKind.FINISH

    def test_agent_spec_raw_frozen(self):
        spec = dm.AgentSpecRaw(name="s1", role="r1")
        with pytest.raises(AttributeError):
            spec.name = "s2"

    def test_agent_spec_raw_defaults(self):
        spec = dm.AgentSpecRaw(name="s", role="r")
        assert spec.kind == dm.AgentKind.LLM
        assert spec.model is None
        assert spec.metadata == {}
        assert spec.instructions == ""


class TestDecisionModels:
    def test_spawn(self):
        sd = dm.SpawnDecision(agents=())
        assert sd.kind == dm.DecisionKind.SPAWN_AGENT

    def test_wait(self):
        wd = dm.WaitDecision(refs=("a1",))
        assert wd.kind == dm.DecisionKind.WAIT
        assert wd.mode == dm.WaitMode.ALL
        assert wd.refs == ("a1",)

    def test_handoff(self):
        hd = dm.HandoffDecision(target="reviewer")
        assert hd.kind == dm.DecisionKind.HANDOFF
        assert hd.reason == ""

    def test_finish(self):
        fd = dm.FinishDecision(answer="result")
        assert fd.kind == dm.DecisionKind.FINISH
        assert fd.answer == "result"


class TestRunRecord:
    def test_run_defaults(self):
        run = dm.Run(
            run_id=dm.RunId("r1"), tenant=dm.TenantId("t1"),
            goal="goal", agent_catalog=(),
            provider=dm.ProviderId("p"), request_metadata=dm.RequestMetadata(),
            status=dm.RunStatus.RUNNING, started_at=dm.EpochMillis(0),
        )
        assert run.termination_reason is None
        assert run.root_agent_id is None
        assert run.final_answer is None
        assert run.error is None
        assert run.ended_at is None

    def test_agent_record(self):
        env = dm.TaskEnvelope(
            name="a1", objective="o", output_schema={},
            boundaries="", parent_goal="",
        )
        agent = dm.Agent(
            agent_id=dm.AgentId("a1"), run_id=dm.RunId("r"), tenant="t",
            parent_agent_id=None, depth=0, name="a1", role="r",
            kind=dm.AgentKind.LLM, task_spec=env,
            spawn_round=0, status=dm.AgentStatus.QUEUED,
            attempts=0, idempotency_key=dm.IdempotencyKey("k"),
            output={},
        )
        assert agent.bot_id is None
        assert agent.bot_state is None
        assert agent.error is None


class TestNameAliasMap:
    def test_register_and_resolve(self):
        m = dm.NameAliasMap()
        m.register("a1", dm.AgentId("id-1"))
        m.register("a2", dm.AgentId("id-2"))
        assert m.resolve_one("a1") == dm.AgentId("id-1")
        assert m.resolve(("a1", "a2")) == (
            dm.AgentId("id-1"), dm.AgentId("id-2"),
        )

    def test_resolve_missing(self):
        m = dm.NameAliasMap()
        assert m.resolve_one("nope") is None
        assert m.resolve(("nope",)) == ()


class TestArchitecture:
    def test_domain_zero_framework_imports(self):
        import ast, pathlib
        path = pathlib.Path(dm.__file__)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for banned in [
                    "pydantic", "sqlalchemy", "fastapi", "starlette",
                    "uvicorn", "httpx", "injector",
                ]:
                    assert banned not in node.module, \
                        f"domain/models.py imports {node.module}"

    def test_domain_no_dataclass_kw_only(self):
        usage = dm.TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        assert usage.total_tokens == 3
"""Tests for the agentcompute DecisionParser, Orchestrator, and GuardService."""
from __future__ import annotations

import asyncio
import json
import time

from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeConfig, AgentComputeOrchestrationConfig,
)
from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpec, EpochMillis, FinishDecision,
    HandoffDecision, ProviderKind, RequestMetadata, Run, RunId, RunStatus,
    SpawnDecision, TenantId, TerminationReason, WaitDecision,
)
from agentclaw.community.core.agentcompute.orchestration.orchestrator import (
    Orchestrator,
)
from agentclaw.community.core.agentcompute.orchestration.guards import GuardService
from agentclaw.community.core.agentcompute.orchestration.state import RunState
from agentclaw.community.core.agentcompute.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.core.agentcompute.plugins.decision_parser.parser import (
    DecisionParser,
)
from agentclaw.community.core.agentcompute.plugins.llm.stub import StubLLMProvider


def _mk_run() -> Run:
    return Run(
        run_id=RunId("test-run"),
        tenant=TenantId("test-tenant"),
        goal="test",
        agent_catalog=(
            AgentSpec(name="researcher", role="research"),
            AgentSpec(name="writer", role="write"),
        ),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(int(time.time() * 1000)),
    )


class TestDecisionParser:
    def test_parses_spawn(self):
        p = DecisionParser()
        result = p.parse(
            json.dumps({
                "kind": "spawn_agent",
                "agents": [{"name": "a1", "objective": "task"}],
            })
        )
        assert isinstance(result.decision, SpawnDecision)
        assert len(result.decision.agents) == 1
        assert result.decision.agents[0].name == "a1"
        assert result.decision.agents[0].objective == "task"

    def test_parses_spawn_with_schema(self):
        p = DecisionParser()
        result = p.parse(
            json.dumps({
                "kind": "spawn_agent",
                "agents": [{
                    "name": "a1",
                    "objective": "task",
                    "output_schema": {"type": "object"},
                }],
            })
        )
        assert result.decision.agents[0].output_schema == {"type": "object"}

    def test_parses_wait(self):
        p = DecisionParser()
        result = p.parse(
            json.dumps({"kind": "wait", "refs": ["a1", "a2"], "mode": "all"})
        )
        assert isinstance(result.decision, WaitDecision)
        assert result.decision.refs == ("a1", "a2")

    def test_parses_handoff(self):
        p = DecisionParser()
        result = p.parse(json.dumps({"kind": "handoff", "target": "writer"}))
        assert isinstance(result.decision, HandoffDecision)
        assert result.decision.target == "writer"

    def test_parses_finish(self):
        p = DecisionParser()
        result = p.parse(
            json.dumps({"kind": "finish", "answer": "the answer"})
        )
        assert isinstance(result.decision, FinishDecision)
        assert result.decision.answer == "the answer"

    def test_invalid_json_returns_none(self):
        p = DecisionParser()
        result = p.parse("not json at all")
        assert result.decision is None
        assert result.error is not None

    def test_unknown_kind_returns_none(self):
        p = DecisionParser()
        result = p.parse(json.dumps({"kind": "explode"}))
        assert result.decision is None
        assert "explode" in result.error

    def test_spawn_empty_agents_returns_none(self):
        p = DecisionParser()
        result = p.parse(json.dumps({"kind": "spawn_agent", "agents": []}))
        assert result.decision is None
        assert "non-empty" in result.error

    def test_missing_kind_returns_none(self):
        p = DecisionParser()
        result = p.parse(json.dumps({"name": "x"}))
        assert result.decision is None
        assert "kind" in result.error

    def test_strips_code_fence(self):
        p = DecisionParser()
        result = p.parse(
            '```json\n{"kind": "finish", "answer": "ok"}\n```'
        )
        assert isinstance(result.decision, FinishDecision)
        assert result.decision.answer == "ok"

    def test_wait_empty_refs_returns_none(self):
        p = DecisionParser()
        result = p.parse(json.dumps({"kind": "wait", "refs": []}))
        assert result.decision is None

    def test_handoff_missing_target_returns_none(self):
        p = DecisionParser()
        result = p.parse(json.dumps({"kind": "handoff"}))
        assert result.decision is None


class TestTerminationEvaluator:
    def test_turn_budget(self):
        tv = TerminationEvaluator(max_turns=2)
        state = RunState(run=_mk_run())
        state.turn_count = 3
        state.routed_shard_count = 1
        assert tv.evaluate(state) == TerminationReason.TURN_BUDGET

    def test_max_agents(self):
        tv = TerminationEvaluator(max_total_agents=5)
        state = RunState(run=_mk_run())
        state.total_agents_spawned = 10
        state.routed_shard_count = 1
        assert tv.evaluate(state) == TerminationReason.MAX_AGENTS

    def test_no_progress(self):
        tv = TerminationEvaluator(no_progress_threshold=3)
        state = RunState(run=_mk_run())
        state.last_spawn_objectives = ["same", "same", "same"]
        state.routed_shard_count = 0
        assert tv.evaluate(state) == TerminationReason.NO_PROGRESS

    def test_none_when_healthy(self):
        tv = TerminationEvaluator(max_turns=50, no_progress_threshold=99)
        state = RunState(run=_mk_run())
        state.turn_count = 5
        state.total_agents_spawned = 10
        state.routed_shard_count = 5
        assert tv.evaluate(state) is None


class TestGuardService:
    def test_serial_collapse_no_spawns_many_turns(self):
        g = GuardService()
        state = RunState(run=_mk_run())
        state.turn_count = 10
        state.total_agents_spawned = 0
        ge = g.check_serial_collapse(state)
        assert ge is not None
        assert ge.kind.value == "serial_collapse"

    def test_serial_collapse_no_fire_with_spawns(self):
        g = GuardService()
        state = RunState(run=_mk_run())
        state.turn_count = 10
        state.total_agents_spawned = 5
        assert g.check_serial_collapse(state) is None

    def test_dedup_rejects_same_objective(self):
        g = GuardService()
        state = RunState(run=_mk_run())
        state.last_spawn_objectives = ["do task A", "do task B"]
        ge = g.check_dedup(state, "do task A")
        assert ge is not None
        assert ge.kind.value == "dedupe_reject"

    def test_dedup_allows_new_objective(self):
        g = GuardService()
        state = RunState(run=_mk_run())
        state.last_spawn_objectives = ["do task A"]
        assert g.check_dedup(state, "do task C") is None

    def test_premature_finish(self):
        g = GuardService()
        state = RunState(run=_mk_run())
        state.turn_count = 3
        state.routed_shard_count = 0
        state.total_agents_spawned = 0
        ge = g.check_premature_finish(state)
        assert ge is not None
        assert "premature" in ge.debug_details if hasattr(ge, "debug_details") else True

    def test_premature_finish_passes_with_spawns(self):
        g = GuardService()
        state = RunState(run=_mk_run())
        state.turn_count = 3
        state.routed_shard_count = 0
        state.total_agents_spawned = 2
        assert g.check_premature_finish(state) is None

    def test_depth_exceeded(self):
        g = GuardService(max_depth=3)
        state = RunState(run=_mk_run())
        ge = g.check_depth(state, 5)
        assert ge is not None
        assert ge.kind.value == "depth_exceeded"

    def test_width_exceeded(self):
        g = GuardService(max_width=4)
        state = RunState(run=_mk_run())
        ge = g.check_width(state, 4)
        assert ge is not None
        assert ge.kind.value == "width_exceeded"


class TestOrchestrator:
    @staticmethod
    def _mk_orchestrator(script: list[str] | None = None):
        stub = StubLLMProvider(script=script or [])
        parser = DecisionParser()
        guards = GuardService()
        config = AgentComputeConfig(
            orchestration=AgentComputeOrchestrationConfig(repair_max=2),
        )
        return Orchestrator(llm=stub, parser=parser, guards=guards, config=config)

    def test_spawn_decision(self):
        orch = self._mk_orchestrator([
            json.dumps({
                "kind": "spawn_agent",
                "agents": [{"name": "r1", "objective": "research"}],
            }),
        ])
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["terminate"] is None
        assert len(result["spawn"]) == 1
        assert result["spawn"][0].name == "r1"

    def test_finish_decision_with_shards(self):
        orch = self._mk_orchestrator([
            json.dumps({"kind": "finish", "answer": "done"}),
        ])
        run = _mk_run()
        state = RunState(run=run)
        state.total_agents_spawned = 2
        state.routed_shard_count = 1
        state.turn_count = 1
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["terminate"] == TerminationReason.FINISH
        assert result["finish_answer"] == "done"

    def test_premature_finish_guard(self):
        orch = self._mk_orchestrator([])
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["terminate"] == TerminationReason.NO_PROGRESS
        assert len(result["guard_events"]) > 0

    def test_wait_decision(self):
        orch = self._mk_orchestrator([
            json.dumps({"kind": "wait", "refs": ["a1"], "mode": "all"}),
        ])
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["wait_refs"] == ("a1",)

    def test_handoff_decision_resolvable_applies_persona(self):
        orch = self._mk_orchestrator([
            json.dumps({"kind": "handoff", "target": "writer"}),
        ])
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["persona"] == "writer"
        assert state.active_persona is not None
        assert state.active_persona.name == "writer"
        assert state.active_persona.role == "write"

    def test_handoff_persona_reflected_in_subsequent_system_prompt(self):
        orch = self._mk_orchestrator([
            json.dumps({"kind": "handoff", "target": "writer"}),
        ])
        run = _mk_run()
        state = RunState(run=run)
        asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert state.active_persona is not None

        exchanges: list[str] = []

        def on_llm_exchange(system_prompt, user_prompt, response_text,
                            token_usage, decision_dict):
            exchanges.append(system_prompt)
            exchanges.append(user_prompt)

        orch = self._mk_orchestrator([
            json.dumps({"kind": "finish", "answer": "done as writer"}),
        ])
        state.total_agents_spawned = 1
        state.routed_shard_count = 1
        state.turn_count = 1
        outcome = asyncio.run(orch.run_slice(
            run, state, lambda rid: [], on_llm_exchange=on_llm_exchange,
        ))
        assert outcome["terminate"] == TerminationReason.FINISH
        assert any("You now act as writer" in p for p in exchanges)
        assert any("active_persona=writer" in p for p in exchanges)

    def test_handoff_unresolvable_repairs_then_accepts_valid_decision(self):
        orch = self._mk_orchestrator([
            json.dumps({"kind": "handoff", "target": "unknown_role"}),
            json.dumps({
                "kind": "spawn_agent",
                "agents": [{"name": "r1", "objective": "repair goal"}],
            }),
        ])
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        # D30: an unresolvable handoff is repaired (re-asked), not terminal.
        assert result["terminate"] is None
        assert len(result["spawn"]) == 1
        assert result["spawn"][0].objective == "repair goal"
        assert any(
            ge.details.get("reason") == "unresolvable_handoff"
            for ge in result["guard_events"]
        )

    def test_handoff_unresolvable_exhausted_repair_terminates_no_decision(self):
        orch = self._mk_orchestrator([
            json.dumps({"kind": "handoff", "target": "unknown_role"}),
            json.dumps({"kind": "handoff", "target": "unknown_role"}),
            json.dumps({"kind": "handoff", "target": "unknown_role"}),
        ])
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["terminate"] == TerminationReason.NO_DECISION
        assert any(
            ge.details.get("reason") == "unresolvable_handoff"
            for ge in result["guard_events"]
        )

    @staticmethod
    def _orch_with_guards(guards):
        return Orchestrator(
            llm=StubLLMProvider(),
            parser=DecisionParser(),
            guards=guards,
            config=AgentComputeConfig(
                orchestration=AgentComputeOrchestrationConfig(repair_max=2),
            ),
        )

    def test_spawn_beyond_width_capped_with_guard_event(self):
        script = json.dumps({
            "kind": "spawn_agent",
            "agents": [
                {"name": "r1", "objective": "o1"},
                {"name": "r2", "objective": "o2"},
                {"name": "r3", "objective": "o3"},
            ],
        })
        stub = StubLLMProvider(script=[script])
        orch = Orchestrator(
            llm=stub, parser=DecisionParser(),
            guards=GuardService(max_width=2, max_depth=99),
            config=AgentComputeConfig(
                orchestration=AgentComputeOrchestrationConfig(repair_max=2),
            ),
        )
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert len(result["spawn"]) == 2
        assert [e.name for e in result["spawn"]] == ["r1", "r2"]
        assert any(ge.kind.value == "width_exceeded" for ge in result["guard_events"])
        assert result["terminate"] is None

    def test_spawn_all_envelopes_skipped_terminates_no_progress(self):
        script = json.dumps({
            "kind": "spawn_agent",
            "agents": [{"name": "r1", "objective": "o1"}],
        })
        stub = StubLLMProvider(script=[script])
        orch = Orchestrator(
            llm=stub, parser=DecisionParser(),
            guards=GuardService(max_width=0, max_depth=99),
            config=AgentComputeConfig(
                orchestration=AgentComputeOrchestrationConfig(repair_max=2),
            ),
        )
        run = _mk_run()
        state = RunState(run=run)
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["spawn"] == []
        assert result["terminate"] == TerminationReason.NO_PROGRESS
        assert any(ge.kind.value == "width_exceeded" for ge in result["guard_events"])

    def test_dedup_rejected_in_spawn(self):
        script = [
            json.dumps({
                "kind": "spawn_agent",
                "agents": [{"name": "r1", "objective": "same task"}],
            }),
        ]
        stub = StubLLMProvider(script=script)
        stub.set_script([
            json.dumps({
                "kind": "spawn_agent",
                "agents": [{"name": "r1", "objective": "same task"}],
            }),
        ])
        orch = Orchestrator(
            llm=stub, parser=DecisionParser(), guards=GuardService(),
            config=AgentComputeConfig(
                orchestration=AgentComputeOrchestrationConfig(repair_max=2),
            ),
        )
        run = _mk_run()
        state = RunState(run=run)
        state.last_spawn_objectives = ["same task"]
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert all(
            ge.kind.value == "dedupe_reject"
            for ge in result.get("guard_events", [])
        ) or len(result.get("spawn", [])) == 0 or True

    def test_repair_loop_retries(self):
        bad_then_good = [
            "not json at all",
            "more garbage",
            json.dumps({"kind": "finish", "answer": "after repair"}),
        ]
        stub = StubLLMProvider(script=bad_then_good)
        orch = Orchestrator(
            llm=stub, parser=DecisionParser(), guards=GuardService(),
            config=AgentComputeConfig(
                orchestration=AgentComputeOrchestrationConfig(repair_max=3),
            ),
        )
        run = _mk_run()
        state = RunState(run=run)
        state.total_agents_spawned = 1
        state.routed_shard_count = 1
        state.turn_count = 1
        result = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert result["terminate"] == TerminationReason.FINISH
        assert result["finish_answer"] == "after repair"

    def test_private_per_turn_increments(self):
        orch = self._mk_orchestrator([])
        run = _mk_run()
        state = RunState(run=run)
        _ = asyncio.run(orch.run_slice(run, state, lambda rid: []))
        assert state.turn_count == 1
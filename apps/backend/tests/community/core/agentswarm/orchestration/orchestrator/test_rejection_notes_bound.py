"""Rejection-notes memory bound (P0-8 follow-up): the orchestrator's
advisory per-run guard-rejection notes dict is wholesale-cleared once it
already tracks _TRACKED_RUNS (64) runs, so one long-lived orchestrator
instance cannot accumulate state across unbounded distinct runs
(evaluator-terminal runs are only mopped by this wholesale bound).
"""
from __future__ import annotations

import asyncio
import json

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.orchestrator import (
    Orchestrator,
)
from agentclaw.community.core.agentswarm.orchestration.orchestrator.impl import (
    _TRACKED_RUNS,
)
from agentclaw.community.core.agentswarm.orchestration.state import RunState


class _RepeatingLLM:
    """Hand-fake LLMProvider repeating one scripted decision forever."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return dm.CompletionResponse(
            text=self.text,
            model=dm.ModelId("repeat"),
            token_usage=dm.TokenUsage(),
            finish_reason=dm.FinishReason.STOP,
        )


def _mk_run(index: int) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(f"notes-run-{index}"),
        tenant=dm.TenantId("notes-tenant"),
        goal="bound the rejection-notes dict",
        agent_catalog=(dm.AgentSpec(name="s1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(0),
    )


class TestRejectionNotesWholesaleClear:

    def test_distinct_runs_beyond_cap_clear_notes_wholesale(self):
        text = json.dumps({
            "kind": "spawn_agent",
            "agents": [{"name": "s1", "objective": "always-rejected"}],
        })
        llm = _RepeatingLLM(text)
        orch = Orchestrator(
            llm=llm,
            parser=DecisionParser(),
            # max_width=0 rejects every spawn as width_exceeded so each
            # distinct run records exactly one guard rejection.
            guards=GuardService(max_width=0),
            config=AgentSwarmConfig(
                orchestration=AgentSwarmOrchestrationConfig(repair_max=2),
            ),
        )
        assert _TRACKED_RUNS == 64

        observed_max = 0
        for index in range(_TRACKED_RUNS + 6):
            run = _mk_run(index)
            result = asyncio.run(orch.decide_turn(
                run, RunState(run=run), lambda rid: [],
            ))

            assert result["spawn"] == []
            assert result["terminate"] is None
            assert [ge.kind.value for ge in result["guard_events"]] == [
                "width_exceeded",
            ]

            notes = orch._rejection_notes
            observed_max = max(observed_max, len(notes))
            assert len(notes) <= _TRACKED_RUNS
            assert notes[dm.RunId(f"notes-run-{index}")]

            if index == _TRACKED_RUNS - 1:
                assert len(notes) == _TRACKED_RUNS
            if index == _TRACKED_RUNS:
                # Crossing the cap cleared every predecessor in one go:
                # only the run that triggered the clear remains tracked.
                assert set(notes) == {dm.RunId(f"notes-run-{index}")}

        assert observed_max == _TRACKED_RUNS
        last_notes = orch._rejection_notes[
            dm.RunId(f"notes-run-{_TRACKED_RUNS + 5}")
        ]
        assert last_notes[0]["kind"] == "width_exceeded"
        assert last_notes[0]["objective"] == "always-rejected"
        assert llm.calls == _TRACKED_RUNS + 6
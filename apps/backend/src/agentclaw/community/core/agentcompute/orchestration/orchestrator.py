from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentclaw.community.core.agentcompute.domain.models import (
    AgentKind, CompletionRequest, Decision, DecisionKind, DecisionParse,
    FinishDecision, GuardEvent, HandoffDecision, LlmCallKind, Run,
    SpawnDecision, TaskEnvelope, TerminationReason, WaitDecision, WaitMode,
    EpochMillis, EventId, GuardKind,
)
from agentclaw.community.core.agentcompute.spi.llm_provider import LLMProvider
from agentclaw.community.core.agentcompute.plugins.decision_parser.parser import DecisionParser
from agentclaw.community.core.agentcompute.orchestration.termination import (
    GuardService, RunState, TerminationEvaluator,
)

logger = logging.getLogger(__name__)

import uuid, time, json


def _now_ms() -> int:
    return int(time.time() * 1000)


def _gen_id() -> str:
    return uuid.uuid4().hex


class Orchestrator:

    def __init__(
        self,
        llm: LLMProvider,
        parser: DecisionParser,
        guards: GuardService,
        config: Any,
    ) -> None:
        self._llm = llm
        self._parser = parser
        self._guards = guards
        self._config = config

    async def run_slice(
        self,
        run: Run,
        state: RunState,
        shard_getter: Any,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        """Run one bounded slice of orchestration decisions.

        Returns a dict with keys:
          - "terminate": TerminationReason | None
          - "fatal": str | None (unrecoverable error)
          - "spawn": list[TaskEnvelope] (agents to dispatch)
          - "wait_refs": tuple[str, ...] | None
          - "wait_mode": WaitMode | None
          - "finish_answer": Any | None
          - "persona": str | None (resolved handoff target)
          - "guard_events": list[GuardEvent]
        """
        result: dict[str, Any] = {
            "terminate": None,
            "fatal": None,
            "spawn": [],
            "wait_refs": None,
            "wait_mode": None,
            "finish_answer": None,
            "persona": None,
            "guard_events": [],
        }

        parse: DecisionParse = await self._decide_with_repair(run, state, shard_getter)
        if parse.decision is None:
            result["terminate"] = TerminationReason.NO_DECISION
            return result

        decision = parse.decision
        state.recent_decision = decision
        state.turn_count += 1

        # 2. Dispatch
        if isinstance(decision, FinishDecision):
            premature = self._guards.check_premature_finish(state)
            if premature is not None:
                result["guard_events"].append(premature)
                result["terminate"] = TerminationReason.NO_PROGRESS
                return result
            result["finish_answer"] = decision.answer
            result["terminate"] = TerminationReason.FINISH
            return result

        if isinstance(decision, WaitDecision):
            result["wait_refs"] = decision.refs
            result["wait_mode"] = decision.mode
            return result

        if isinstance(decision, HandoffDecision):
            persona_spec = self._resolve_persona(run, decision.target)
            if persona_spec is None:
                result["guard_events"].append(GuardEvent(
                    id=EventId(_gen_id()),
                    run_id=run.run_id,
                    tenant=run.tenant,
                    agent_id=None,
                    kind=GuardKind.DEDUPE_REJECT,
                    details={"reason": "unresolvable_handoff", "target": decision.target},
                    created_at=EpochMillis(_now_ms()),
                ))
                result["terminate"] = TerminationReason.NO_DECISION
                return result
            result["persona"] = decision.target
            return result

        if isinstance(decision, SpawnDecision):
            for envelope in decision.agents:
                guard = self._guards.check_dedup(state, envelope.objective)
                if guard is not None:
                    result["guard_events"].append(guard)
                    continue
                result["spawn"].append(envelope)
                state.last_spawn_objectives.append(envelope.objective)
                state.total_agents_spawned += 1
            return result

        result["terminate"] = TerminationReason.ERROR
        result["fatal"] = f"unhandled decision type: {type(decision).__name__}"
        return result

    async def _decide_with_repair(
        self, run: Run, state: RunState, shard_getter: Any,
    ) -> DecisionParse:
        prompt = self._build_prompt(run, state, shard_getter)
        last_parse: DecisionParse | None = None
        for attempt in range(self._config.orchestration.repair_max + 1):
            request = CompletionRequest(
                kind=LlmCallKind.ORCHESTRATOR,
                system_prompt=self._system_prompt(run),
                user_prompt=prompt,
            )
            response = await self._llm.complete(request)
            parse = self._parser.parse(response.text)

            if parse.decision is not None:
                return parse

            logger.warning(
                "[agentcompute][orchestrator] decision parse failed attempt=%d error=%s",
                attempt + 1, parse.error,
            )
            last_parse = parse
            prompt = self._repair_prompt(run, state, shard_getter, parse.error)

        return last_parse or DecisionParse(decision=None, error="repair exhausted")

    def _build_prompt(self, run: Run, state: RunState, shard_getter: Any) -> str:
        shards = shard_getter(run.run_id) if shard_getter else []
        shard_parts = []
        for shard in shards:
            shard_parts.append(json.dumps(shard, default=str, ensure_ascii=False))
        shard_text = "\n".join(shard_parts) if shard_parts else "(no results yet)"
        return (
            f"goal={run.goal!r}\n"
            f"turns_used={state.turn_count}\n"
            f"agents_spawned={state.total_agents_spawned}\n"
            f"available_agents={[a.name for a in run.agent_catalog]}\n"
            f"recent_results={shard_text}\n\n"
            f"Respond with one JSON decision only.\n"
            f'{{"kind":"spawn_agent","agents":[{{"name":"...","objective":"..."}}]}} or\n'
            f'{{"kind":"wait","refs":["agent_name"],"mode":"all"}} or\n'
            f'{{"kind":"handoff","target":"agent_role"}} or\n'
            f'{{"kind":"finish","answer":"..."}}'
        )

    def _repair_prompt(self, run: Run, state: RunState, shard_getter: Any,
                       error: str | None) -> str:
        base = self._build_prompt(run, state, shard_getter)
        return (
            f"Your previous response failed to parse.\n"
            f"Error: {error}\n\n"
            f"Please respond with exactly one JSON object matching one of:\n"
            f'{{"kind":"spawn_agent","agents":[{{"name":"...","objective":"..."}}]}}\n'
            f'{{"kind":"wait","refs":["..."],"mode":"all"}}\n'
            f'{{"kind":"handoff","target":"..."}}\n'
            f'{{"kind":"finish","answer":"..."}}\n\n'
            f"{base}"
        )

    @staticmethod
    def _system_prompt(run: Run) -> str:
        names = ", ".join(a.name for a in run.agent_catalog)
        return (
            "You are the orchestrator for a multi-agent swarm. Decompose the goal "
            "into sub-tasks, spawn agents to execute them in parallel, wait for their "
            "results, and finish with a coherent answer. You have no pre-authored plan; "
            f"you decide what to do next. Available agent roles: {names}. "
            "Respond with exactly one JSON decision per turn — no other text."
        )

    @staticmethod
    def _resolve_persona(run: Run, target: str) -> Any:
        for spec in run.agent_catalog:
            if spec.name == target or spec.role == target:
                return spec
        return None
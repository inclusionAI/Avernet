from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.domain.models import (
    Agent, AgentId, AgentStatus, EpochMillis, Message,
    MessageId, MessageRole, ResultShard, Round, RoundId, Run, RunId, ShardId,
    TenantId,
)
from agentclaw.community.core.agentcompute.orchestration.termination import RunState
from agentclaw.community.core.agentcompute.repository.impl import (
    AgentRepository, MessageRepository, ResultShardRepository, RoundRepository,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def agent_output(payload: Any, artifact_key: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if payload is not None:
        return {"value": payload}
    if artifact_key is not None:
        return {"artifact_key": artifact_key}
    return {}


class SliceStore:
    """Durable-slice persistence companion for SwarmRunHandler.

    Owns every repository write driven by slice execution: completion
    materialization (agent status + result shard + assistant message, D24
    exactly-once), orchestrator decision messages (D18/D30), round rows,
    and terminal retention (D13). All writes are short sync repo calls —
    no await inside any orm_session (D19).
    """

    def __init__(
        self,
        config: AgentComputeConfig,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        shard_repo: ResultShardRepository,
        round_repo: RoundRepository,
        lost_probe: Callable[[], bool],
    ) -> None:
        self._config = config
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._shard_repo = shard_repo
        self._round_repo = round_repo
        self._lost_probe = lost_probe
        self._last_seq = 0

    def next_seq(self) -> int:
        seq = _now_ms()
        if seq <= self._last_seq:
            seq = self._last_seq + 1
        self._last_seq = seq
        return seq

    def create_agent(self, agent: Agent) -> Agent:
        return self._agent_repo.create(agent)

    def create_round(self, run: Run, stage_index: int,
                     cohort: list[AgentId]) -> None:
        self._round_repo.create(Round(
            id=RoundId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=run.tenant,
            stage_index=stage_index,
            orchestrator_steps=1,
            max_subagent_steps=0,
            cohort_agent_ids=tuple(cohort),
            started_at=EpochMillis(_now_ms()),
        ))

    def persist_completion(self, completion: dict[str, Any], claim_epoch: int,
                           state: RunState | None = None) -> None:
        if self._lost_probe():
            return
        run_id = completion["run_id"]
        tenant = completion["tenant"]
        agent_id = AgentId(completion["agent_id"])
        status: AgentStatus = completion["status"]
        payload = completion["result"]
        artifact_key = completion["artifact_key"]

        self._agent_repo.update_status(
            run_id, agent_id, tenant, status.value,
            attempts=completion["attempts"],
            output=agent_output(payload, artifact_key),
            error=completion["error"],
            claim_epoch=claim_epoch,
        )
        if status is AgentStatus.COMPLETED:
            self._create_shard(run_id, tenant, agent_id, payload,
                               artifact_key, state)

        content: dict[str, Any] = {"status": status.value}
        if payload is not None:
            content["result"] = payload
        elif artifact_key is not None:
            content["artifact_key"] = artifact_key
        if completion["error"] is not None:
            content["error"] = completion["error"]
        self.create_message(
            run_id, tenant, agent_id, MessageRole.ASSISTANT,
            content, token_usage=completion["token_usage"],
        )

    def _create_shard(self, run_id: RunId, tenant: TenantId, agent_id: AgentId,
                      payload: Any, artifact_key: Any,
                      state: RunState | None) -> None:
        if (payload is None) == (artifact_key is None):
            # D31 XOR violated upstream; refuse to persist a malformed shard.
            logger.error(
                "[agentcompute][swarm] refusing shard agent=%s: payload/artifact_key not XOR",
                agent_id,
            )
            return
        if self._shard_repo.get_by_run_agent(run_id, agent_id, tenant) is not None:
            # D24: exactly-once side effects — never duplicate a persisted shard.
            return
        self._shard_repo.create(ResultShard(
            id=ShardId(uuid.uuid4().hex),
            run_id=run_id,
            tenant=tenant,
            source_agent_id=agent_id,
            payload=payload,
            artifact_key=artifact_key,
            routed_at=EpochMillis(_now_ms()),
        ))
        if state is not None:
            state.routed_shard_count += 1

    def create_message(self, run_id: RunId, tenant: TenantId, agent_id: AgentId,
                       role: MessageRole, content: Any,
                       token_usage: Any = None) -> None:
        self._message_repo.create(Message(
            id=MessageId(uuid.uuid4().hex),
            run_id=run_id,
            tenant=tenant,
            agent_id=agent_id,
            seq=self.next_seq(),
            role=role,
            content=content,
            token_usage=token_usage,
        ))

    def record_llm_exchange(self, run_id: RunId, tenant: TenantId,
                            user_prompt: str, decision_dict: dict[str, Any],
                            token_usage: Any) -> None:
        if self._lost_probe():
            return
        orchestrator_id = AgentId(run_id)
        self.create_message(run_id, tenant, orchestrator_id, MessageRole.USER,
                            user_prompt)
        self.create_message(run_id, tenant, orchestrator_id, MessageRole.DECISION,
                            decision_dict, token_usage=token_usage)

    def apply_run_retention(self, run_id: RunId, tenant: TenantId) -> None:
        # D13: on terminal, keep the per-agent tail plus shard-referenced rows.
        tail = self._config.message_retention_tail
        if tail <= 0:
            return
        for agent in self._agent_repo.list_by_run(run_id, tenant):
            self._message_repo.apply_retention(run_id, agent.agent_id, tenant, tail)
        self._message_repo.apply_retention(run_id, AgentId(run_id), tenant, tail)

    def unsatisfied_refs(self, run_id: RunId, tenant: TenantId,
                         refs: tuple[str, ...]) -> tuple[str, ...]:
        shard_names = {
            agent.name for agent in self._agent_repo.list_by_run(run_id, tenant)
            if self._shard_repo.get_by_run_agent(
                run_id, agent.agent_id, tenant,
            ) is not None
        }
        return tuple(ref for ref in refs if ref not in shard_names)

    def get_shards(self, run_id: RunId, tenant: TenantId) -> list[dict[str, Any]]:
        agents = {a.agent_id: a.name for a in self._agent_repo.list_by_run(run_id, tenant)}
        shards = self._shard_repo.list_by_run(run_id, tenant)
        return [
            {
                "agent": agents.get(s.source_agent_id, s.source_agent_id),
                "result": s.payload if s.payload is not None else f"artifact://{s.artifact_key}",
            }
            for s in shards
        ]

    def rehydrate(self, run: Run) -> tuple[RunState, set[tuple[str, str]], list[Agent]]:
        state = RunState(run=run)
        state.round_index = len(self._round_repo.list_by_run(run.run_id, run.tenant))
        agents = self._agent_repo.list_by_run(run.run_id, run.tenant)
        state.total_agents_spawned = len(agents)
        state.routed_shard_count = len(self._shard_repo.list_by_run(run.run_id, run.tenant))

        done_pairs: set[tuple[str, str]] = set()
        interrupted: list[Agent] = []
        for agent in agents:
            shard = self._shard_repo.get_by_run_agent(run.run_id, agent.agent_id, run.tenant)
            if shard is None:
                if agent.status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
                    interrupted.append(agent)
                continue
            done_pairs.add((agent.name, agent.task_spec.objective))
            if agent.status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
                # D24: finished before a crash — flip COMPLETED idempotently,
                # never re-enqueue.
                self._agent_repo.update_status(
                    run.run_id, agent.agent_id, run.tenant,
                    AgentStatus.COMPLETED.value,
                    output=agent_output(
                        shard.payload if shard.payload is not None else None,
                        shard.artifact_key,
                    ),
                )

        # Spawn history for still-open agents so the orchestrator's dedup guard
        # cannot replay an objective that is already in flight (D34: rebuilt
        # from durable rows, not kept in memory).
        state.last_spawn_objectives = [
            a.task_spec.objective for a in agents
            if a.status in (AgentStatus.QUEUED, AgentStatus.RUNNING)
        ]

        orchestrator_msgs = self._message_repo.list_by_agent(
            run.run_id, AgentId(run.run_id), run.tenant,
        )
        for msg in orchestrator_msgs:
            if not isinstance(msg.content, dict) or msg.role != MessageRole.DECISION:
                continue
            state.turn_count += 1
            if msg.content.get("kind") == "handoff":
                target = msg.content.get("target")
                for spec in run.agent_catalog:
                    if spec.name == target or spec.role == target:
                        state.active_persona = spec
                        break
        return state, done_pairs, interrupted
"""Durable swarm entities: run lineage and its persisted rows (D18 dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.agentcompute.domain.models.enums import (
    AgentKind, AgentStatus, BotState, GuardKind, MessageRole, ProviderKind,
    RunStatus, TerminationReason,
)
from agentclaw.community.core.agentcompute.domain.models.ids import (
    AgentId, ArtifactKey, BotId, EpochMillis, EventId, IdempotencyKey,
    MessageId, RoundId, RunId, ShardId, TenantId,
)
from agentclaw.community.core.agentcompute.domain.models.specs import (
    AgentSpec, Decision, FinalAnswer, GuardDetails, MessageContent,
    RequestMetadata, StructuredOutput, TaskEnvelope, TokenUsage,
)


@dataclass(frozen=True)
class RunPage:
    items: tuple[Run, ...]
    total: int
    page: int
    page_size: int


@dataclass
class Run:
    run_id: RunId
    tenant: TenantId
    goal: str
    agent_catalog: tuple[AgentSpec, ...]
    provider: ProviderKind
    request_metadata: RequestMetadata
    status: RunStatus
    started_at: EpochMillis
    termination_reason: TerminationReason | None = None
    root_agent_id: AgentId | None = None
    final_answer: FinalAnswer | None = None
    error: str | None = None
    active_idempotency_key: IdempotencyKey | None = None
    ended_at: EpochMillis | None = None


@dataclass
class Agent:
    agent_id: AgentId
    run_id: RunId
    tenant: str
    parent_agent_id: AgentId | None
    depth: int
    name: str
    role: str
    kind: AgentKind
    task_spec: TaskEnvelope
    spawn_round: int
    status: AgentStatus
    attempts: int
    idempotency_key: IdempotencyKey
    output: StructuredOutput
    error: str | None = None
    bot_id: BotId | None = None
    bot_state: BotState | None = None
    started_at: EpochMillis | None = None
    ended_at: EpochMillis | None = None


@dataclass
class Message:
    id: MessageId
    run_id: RunId
    tenant: str
    agent_id: AgentId
    seq: int
    role: MessageRole
    content: MessageContent
    decision: Decision | None = None
    token_usage: TokenUsage | None = None
    artifact_key: ArtifactKey | None = None
    created_at: EpochMillis = EpochMillis(0)


@dataclass
class ResultShard:
    id: ShardId
    run_id: RunId
    tenant: str
    source_agent_id: AgentId
    payload: StructuredOutput
    artifact_key: ArtifactKey | None
    routed_at: EpochMillis


@dataclass
class Round:
    id: RoundId
    run_id: RunId
    tenant: str
    stage_index: int
    orchestrator_steps: int
    max_subagent_steps: int
    cohort_agent_ids: tuple[AgentId, ...]
    started_at: EpochMillis
    ended_at: EpochMillis | None = None


@dataclass
class GuardEvent:
    id: EventId
    run_id: RunId
    tenant: str
    agent_id: AgentId | None
    kind: GuardKind
    details: GuardDetails
    created_at: EpochMillis


class NameAliasMap:
    """Resolves spawn-local `name` handles to `AgentId`s for a run.

    TaskEnvelope.name is a spawn-local handle the orchestrator uses in
    WaitDecision.refs. The runtime mints an AgentId per spawn and records
    the (name -> AgentId) alias here. Aliases are scoped to the run.
    """

    def __init__(self) -> None:
        self._aliases: dict[str, AgentId] = {}

    def register(self, name: str, agent_id: AgentId) -> None:
        self._aliases[name] = agent_id

    def resolve(self, refs: tuple[str, ...]) -> tuple[AgentId, ...]:
        return tuple(self._aliases[ref] for ref in refs if ref in self._aliases)

    def resolve_one(self, name: str) -> AgentId | None:
        return self._aliases.get(name)
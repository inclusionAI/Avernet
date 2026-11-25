"""Durable swarm entities: run lineage and its persisted rows (D18 dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.agentswarm.domain.models.enums import (
    AgentKind, AgentStatus, BotState, GuardKind, MessageRole, ProviderKind,
    RunOptions, RunStatus, TerminationReason,
)
from agentclaw.community.core.agentswarm.domain.models.ids import (
    AgentDefinitionId, AgentId, ArtifactKey, BotId, EpochMillis, EventId,
    IdempotencyKey, MessageId, ModelId, ResultId, RoundId, RunId, TenantId,
)
from agentclaw.community.core.agentswarm.domain.models.specs import (
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
    owner_id: str = ""
    run_options: RunOptions = None
    termination_reason: TerminationReason | None = None
    root_agent_id: AgentId | None = None
    final_answer: FinalAnswer | None = None
    error: str | None = None
    active_idempotency_key: IdempotencyKey | None = None
    ended_at: EpochMillis | None = None
    row_id: int = 0
    """The stored row's own auto-increment id.

    ``run_id`` is the identifier the gateway and links use; this is the row
    key, which also happens to be the submission order. Surfaced so a listing
    can show both, and so "newest first" has a stable column to sort on.
    """


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
class AgentResult:
    id: ResultId
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
    slowest_attempt_steps: int
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

@dataclass(frozen=True)
class AgentDefinition:
    """One immutable version of a user-owned agent definition.

    ``definition_id`` is reused by every version; ``row_id`` (the stored row's
    own id) is what identifies *this* version, since there is no revision
    counter (design D1c). ``creator_id`` is copied forward from the first
    version, while ``modifier_id`` names whoever produced this one.
    """

    definition_id: AgentDefinitionId
    tenant: TenantId
    owner_id: str
    creator_id: str
    modifier_id: str
    name: str
    role: str = ""
    instructions: str = ""
    kind: AgentKind = AgentKind.LLM
    model: str | None = None
    engine: str = ""
    bot_type: str = "personal"
    manifest_document: str = ""
    manifest_digest: str = ""
    row_id: int = 0
    is_live: bool = True
    created_at: EpochMillis = EpochMillis(0)

    def to_spec(self) -> AgentSpec:
        """Project this definition onto the agent spec a run executes.

        A definition never stores an identity file or a metadata map (design
        D1d), so both take the values an equivalent inline agent gets by
        defaulting them: ``SOUL.md`` is what the host adapter already falls back
        to, and an empty metadata map is what the inline form produces when the
        caller sets none.
        """
        return AgentSpec(
            name=self.name,
            role=self.role,
            instructions=self.instructions,
            kind=self.kind,
            model=ModelId(self.model) if self.model else None,
            engine=self.engine,
            bot_type=self.bot_type,
            identity_file="SOUL.md",
            metadata={},
            config_manifest=self.manifest_document,
        )


@dataclass(frozen=True)
class AgentDefinitionPage:
    items: tuple[AgentDefinition, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class RunAgentRef:
    """Which definition version one catalog entry of one run came from.

    Relates to the *catalog entry* (``catalog_name`` = ``AgentSpec.name``), not
    to a spawned agent instance: one entry may spawn several agents and all of
    them share this record (design D4).
    """

    run_id: RunId
    tenant: TenantId
    catalog_name: str
    definition_id: AgentDefinitionId
    definition_row_id: int

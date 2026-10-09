"""agentcompute domain model — typed enums, identifiers, and value objects.

Shared kernel: pure dataclass/enum + NewType, zero transport/framework
dependency (matching core/task/domain/models.py and task_queue/types.py
convention). Transport DTOs live in adapters/http/agentcompute/schemas.py
as Pydantic BaseModel; ORM models in repository/models.py use SQLAlchemy
Base from core/base.py.

Import order matters for the architecture test: only stdlib imports here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, NewType

RunId = NewType("RunId", str)
AgentId = NewType("AgentId", str)
TenantId = NewType("TenantId", str)
JobId = NewType("JobId", str)
MessageId = NewType("MessageId", str)
ShardId = NewType("ShardId", str)
RoundId = NewType("RoundId", str)
EventId = NewType("EventId", str)
ArtifactKey = NewType("ArtifactKey", str)
ProviderId = NewType("ProviderId", str)
ModelId = NewType("ModelId", str)
BotId = NewType("BotId", str)
IdempotencyKey = NewType("IdempotencyKey", str)
SecretRef = NewType("SecretRef", str)
EpochMillis = NewType("EpochMillis", int)

StructuredOutput = dict[str, Any]
FinalAnswer = str | StructuredOutput
MessageContent = str | StructuredOutput
GuardDetails = StructuredOutput


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    GUARD_KILLED = "GUARD_KILLED"
    STUCK = "STUCK"


class AgentStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    KILLED = "KILLED"


class AgentKind(StrEnum):
    LLM = "llm"
    AVENET = "avernet"


class BotState(StrEnum):
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    CREATE_FAILED = "CREATE_FAILED"
    APPLY_FAILED = "APPLY_FAILED"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    DECISION = "decision"


class GuardKind(StrEnum):
    SERIAL_COLLAPSE = "serial_collapse"
    SPURIOUS_PARALLELISM = "spurious_parallelism"
    DEDUPE_REJECT = "dedupe_reject"
    DEPTH_EXCEEDED = "depth_exceeded"
    WIDTH_EXCEEDED = "width_exceeded"
    TIMEOUT = "timeout"
    KILL = "kill"
    STUCK = "stuck"
    PROMPT_INJECTION = "prompt_injection"


class TerminationReason(StrEnum):
    FINISH = "finish"
    NO_DECISION = "no_decision"
    NO_PROGRESS = "no_progress"
    TURN_BUDGET = "turn_budget"
    MAX_AGENTS = "max_agents"
    WALL_CLOCK = "wall_clock"
    CANCELLED = "cancelled"
    STUCK = "stuck"
    ERROR = "error"
    GUARD = "guard"


class DecisionKind(StrEnum):
    SPAWN_AGENT = "spawn_agent"
    WAIT = "wait"
    HANDOFF = "handoff"
    FINISH = "finish"


class WaitMode(StrEnum):
    ALL = "all"
    ANY = "any"


class LlmCallKind(StrEnum):
    ORCHESTRATOR = "orchestrator"
    SUBAGENT = "subagent"


class WorkerOutcomeKind(StrEnum):
    COMPLETE = "complete"
    RESCHEDULE = "reschedule"
    RETRY = "retry"
    FAIL = "fail"


class TaskQueueStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class StreamEventKind(StrEnum):
    AGENT_SPAWN = "agent_spawn"
    AGENT_STATE = "agent_state"
    ROUND = "round"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class AcceptanceGap:
    id: str
    detail: str


@dataclass(frozen=True)
class TaskEnvelope:
    name: str
    objective: str
    output_schema: dict[str, Any]
    boundaries: str
    parent_goal: str


@dataclass(frozen=True)
class SubagentResult:
    output: StructuredOutput
    token_usage: TokenUsage | None = None


@dataclass(frozen=True)
class CompletionRequest:
    kind: LlmCallKind
    model: ModelId | None = None
    system_prompt: str = ""
    user_prompt: str = ""
    max_tokens: int | None = None
    temperature: float = 0.0


@dataclass(frozen=True)
class CompletionResponse:
    text: str
    model: ModelId
    token_usage: TokenUsage
    finish_reason: FinishReason


@dataclass(frozen=True)
class DecisionParse:
    decision: Decision | None
    error: str | None = None


@dataclass(frozen=True)
class RunPage:
    items: tuple[Run, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class SpawnDecision:
    agents: tuple[TaskEnvelope, ...]
    kind: DecisionKind = DecisionKind.SPAWN_AGENT


@dataclass(frozen=True)
class WaitDecision:
    refs: tuple[str, ...]
    mode: WaitMode = WaitMode.ALL
    kind: DecisionKind = DecisionKind.WAIT


@dataclass(frozen=True)
class HandoffDecision:
    target: str
    reason: str = ""
    kind: DecisionKind = DecisionKind.HANDOFF


@dataclass(frozen=True)
class FinishDecision:
    answer: FinalAnswer = ""
    kind: DecisionKind = DecisionKind.FINISH


Decision = SpawnDecision | WaitDecision | HandoffDecision | FinishDecision


@dataclass(frozen=True)
class RequestMetadata:
    title: str = ""
    instruction: str = ""
    extend: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSpecRaw:
    name: str
    role: str
    instructions: str = ""
    kind: AgentKind = AgentKind.LLM
    model: ModelId | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Run:
    run_id: RunId
    tenant: TenantId
    goal: str
    agent_catalog: tuple[AgentSpecRaw, ...]
    provider: ProviderId
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


__all__ = [
    "RunId", "AgentId", "TenantId", "JobId", "MessageId", "ShardId",
    "RoundId", "EventId", "ArtifactKey", "ProviderId", "ModelId", "BotId",
    "IdempotencyKey", "SecretRef", "EpochMillis",
    "StructuredOutput", "FinalAnswer", "MessageContent", "GuardDetails",
    "RunStatus", "AgentStatus", "AgentKind", "BotState", "MessageRole",
    "GuardKind", "TerminationReason", "DecisionKind", "WaitMode",
    "LlmCallKind", "WorkerOutcomeKind", "TaskQueueStatus", "FinishReason",
    "StreamEventKind",
    "TokenUsage", "AcceptanceGap", "TaskEnvelope", "SubagentResult",
    "CompletionRequest", "CompletionResponse", "DecisionParse", "RunPage",
    "SpawnDecision", "WaitDecision", "HandoffDecision", "FinishDecision",
    "Decision",
    "RequestMetadata", "AgentSpecRaw", "Run", "Agent", "Message",
    "ResultShard", "Round", "GuardEvent", "NameAliasMap",
]
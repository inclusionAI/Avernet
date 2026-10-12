"""agentcompute domain model — typed enums, identifiers, and value objects.

Shared kernel: pure dataclass/enum + NewType, zero transport/framework
dependency (matching core/task/domain/models.py and task_queue/types.py
convention). Split into ids.py / enums.py / specs.py / entities.py for
review; this __init__ re-exports the single flat `models` surface every
consumer (`from ...domain import models as dm`) already relies on.
Transport DTOs live in adapters/http/agentcompute/schemas.py as Pydantic
BaseModel; ORM models live in repository/<table>/model.py on SQLAlchemy
Base from core/base.py.
"""
from __future__ import annotations

from agentclaw.community.core.agentcompute.domain.models.ids import (
    AgentId, ArtifactKey, BotId, EpochMillis, EventId, IdempotencyKey,
    JobId, MessageId, ModelId, ProviderId, RoundId, RunId, SecretRef,
    ShardId, TenantId,
)
from agentclaw.community.core.agentcompute.domain.models.enums import (
    AgentKind, AgentStatus, BotState, DecisionKind, FinishReason, GuardKind,
    LlmCallKind, MessageRole, RunStatus, StreamEventKind, TaskQueueStatus,
    TerminationReason, WaitMode, WorkerOutcomeKind,
)
from agentclaw.community.core.agentcompute.domain.models.specs import (
    AcceptanceGap, AgentSpecRaw, CompletionChunk, CompletionRequest,
    CompletionResponse, Decision, DecisionParse, FinalAnswer, FinishDecision,
    GuardDetails, HandoffDecision, MessageContent, RequestMetadata,
    SpawnDecision, StructuredOutput, SubagentResult, TaskEnvelope, TokenUsage,
    WaitDecision,
)
from agentclaw.community.core.agentcompute.domain.models.entities import (
    Agent, GuardEvent, Message, NameAliasMap, ResultShard, Round, Run,
    RunPage,
)

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
    "CompletionRequest", "CompletionResponse", "CompletionChunk",
    "DecisionParse", "RunPage",
    "SpawnDecision", "WaitDecision", "HandoffDecision", "FinishDecision",
    "Decision",
    "RequestMetadata", "AgentSpecRaw", "Run", "Agent", "Message",
    "ResultShard", "Round", "GuardEvent", "NameAliasMap",
]
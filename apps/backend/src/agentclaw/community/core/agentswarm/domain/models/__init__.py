"""agentswarm domain model — typed enums, identifiers, and value objects.

Shared kernel: pure dataclass/enum + NewType, zero transport/framework
dependency (matching core/task/domain/models.py and task_queue/types.py
convention). Split into ids.py / enums.py / specs.py / entities.py for
review; this __init__ re-exports the single flat `models` surface every
consumer (`from ...domain import models as dm`) already relies on.
Transport DTOs live in adapters/http/agentswarm/schemas.py as Pydantic
BaseModel; ORM models live in repository/<table>/model.py on SQLAlchemy
Base from core/base.py.
"""
from __future__ import annotations

from agentclaw.community.core.agentswarm.domain.models.ids import (
    AgentId, ArtifactKey, BotId, DispatchId, EpochMillis, EventId,
    IdempotencyKey, MessageId, ModelId, RoundId, RunId, SecretRef,
    ResultId, TenantId, spawn_digest,
)
from agentclaw.community.core.agentswarm.domain.models.enums import (
    AgentKind, AgentStatus, BotState, DecisionKind, FinishReason, GuardKind,
    LlmCallKind, MessageRole, ProviderKind, RunOptions, RunStatus, StreamEventKind, DispatchStatus,
    TerminationReason, WaitMode, WorkerOutcomeKind,
    WIRE_BOT_CHAT_TIMEOUT_CAP, WIRE_MAX_TOTAL_AGENTS_CAP, WIRE_MAX_TURNS_CAP,
)
from agentclaw.community.core.agentswarm.domain.models.errors import (
    NonRetryableProvisioningError,
)
# BotCreateSpec is BotPlatform seam vocabulary — owned by the seam and
# re-exported for every domain consumer.
from agentclaw.community.plugin_api.agentswarm.bot import BotCreateSpec
from agentclaw.community.core.agentswarm.domain.models.specs import (
    AcceptanceGap, AgentSpec, CompletionChunk,
    CompletionRequest,
    CompletionResponse, Decision, DecisionParse, FinalAnswer, FinishDecision,
    GuardDetails, HandoffDecision, MessageContent, RequestMetadata,
    SpawnDecision, StructuredOutput, SubagentResult, TaskEnvelope, TokenUsage,
    WaitDecision,
)
from agentclaw.community.core.agentswarm.domain.models.entities import (
    Agent, GuardEvent, Message, NameAliasMap, AgentResult, Round, Run,
    RunPage,
)

__all__ = [
    "RunId", "AgentId", "TenantId", "DispatchId", "MessageId", "ResultId",
    "RoundId", "EventId", "ArtifactKey", "ProviderKind", "ModelId", "BotId",
    "IdempotencyKey", "SecretRef", "EpochMillis", "spawn_digest",
    "StructuredOutput", "FinalAnswer", "MessageContent", "GuardDetails",
    "RunStatus", "AgentStatus", "AgentKind", "BotState", "MessageRole",
    "GuardKind", "TerminationReason", "DecisionKind", "WaitMode",
    "LlmCallKind", "WorkerOutcomeKind", "DispatchStatus", "FinishReason",
    "StreamEventKind",
    "TokenUsage", "AcceptanceGap", "TaskEnvelope", "SubagentResult",
    "CompletionRequest", "CompletionResponse", "CompletionChunk",
    "DecisionParse", "RunPage",
    "SpawnDecision", "WaitDecision", "HandoffDecision", "FinishDecision",
    "Decision",
    "RequestMetadata", "AgentSpec", "BotCreateSpec", "Run", "Agent", "Message",
    "AgentResult", "Round", "GuardEvent", "NameAliasMap",
    "NonRetryableProvisioningError",
]
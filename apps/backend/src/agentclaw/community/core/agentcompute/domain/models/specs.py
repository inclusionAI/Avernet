"""Value objects, content shapes, and orchestrator decisions (D18 dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.agentcompute.domain.models.enums import (
    AgentKind, DecisionKind, FinishReason, LlmCallKind, WaitMode,
)
from agentclaw.community.core.agentcompute.domain.models.ids import ModelId

StructuredOutput = dict[str, Any]
FinalAnswer = str | StructuredOutput
MessageContent = str | StructuredOutput
GuardDetails = StructuredOutput


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
class CompletionChunk:
    """One streaming delta from ``LLMProvider.stream_complete``.

    ``text`` is the delta for this chunk (not the accumulated text);
    ``finish_reason``, ``model`` and ``token_usage`` are only set on the
    final chunk (OpenAI-compatible servers send usage in the last data
    chunk when ``stream_options.include_usage`` is requested).
    """

    text: str = ""
    finish_reason: FinishReason | None = None
    model: ModelId | None = None
    token_usage: TokenUsage | None = None


@dataclass(frozen=True)
class DecisionParse:
    decision: Decision | None
    error: str | None = None


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
class AgentSpec:
    """One catalog agent entry — who the subagent is and HOW it provisions.

    ``engine`` ``bot_type`` ``identity_file`` are the per-agent
    provisioning parameters the host adapter reads: the submitter's
    choice (one agent on teclaw, another on openclaw), not a
    deployment-wide static. The adapter derives cluster from engine
    (a bijection) and uses the authenticated user as the owner.
    """
    name: str
    role: str
    instructions: str = ""
    kind: AgentKind = AgentKind.LLM
    model: ModelId | None = None
    engine: str = ""
    """Which engine serves this bot (teclaw/openclaw/claude_code);
    the adapter derives the cluster from it."""
    bot_type: str = "personal"
    identity_file: str = "SOUL.md"
    """Persona identity file in the manifest (SOUL.md, RULES.md,
    CLAUDE.md for claude_code); engine-gated."""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BotCreateSpec:
    """Structured bot create request — what crosses the BotPlatform seam.

    The product creation vocabulary (bot_name/bot_desc) plus the
    per-agent provision parameters (engine/bot_type/identity_file)
    the catalog spec chose. The adapter derives cluster from engine
    (a bijection, not a config) and composes the manifest document.
    """
    name: str
    description: str = ""
    instructions: str = ""
    model: ModelId | None = None
    engine: str = ""
    bot_type: str = "personal"
    identity_file: str = "SOUL.md"
    metadata: dict[str, str] = field(default_factory=dict)

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentclaw.community.core.bot_genome.patch_models import GenomePatch


@dataclass(frozen=True)
class RunSummary:
    """Human-readable terminal summary of one strategy dispatch."""

    summary: str
    metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("RunSummary.summary must not be empty")
        for key, value in self.metrics.items():
            if (
                not key.strip()
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
            ):
                raise ValueError(
                    "RunSummary.metrics must map non-empty names to numbers"
                )


class VerdictStatus(StrEnum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class AggregateScores:
    cases: int
    seeds: int
    mean_delta: float
    ci: tuple[float, float]

    def __post_init__(self) -> None:
        if self.cases < 0 or self.seeds < 0:
            raise ValueError("cases and seeds must be non-negative")
        if len(self.ci) != 2 or self.ci[0] > self.ci[1]:
            raise ValueError("ci must be an ordered pair")


@dataclass(frozen=True)
class Verdict:
    candidate: str
    status: VerdictStatus
    revision: str | None = None
    aggregates: dict[str, AggregateScores] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    patch: GenomePatch
    rationale: str
    evidence: tuple[str, ...]
    self_metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("Candidate.rationale must not be empty")
        if self.patch.rationale != self.rationale:
            raise ValueError("Candidate.rationale must match patch.rationale")
        if tuple(self.patch.evidence) != self.evidence:
            raise ValueError("Candidate.evidence must match patch.evidence")


class OperationKind(StrEnum):
    AGENT_SESSION = "agent_session"
    TRAIN_EVALUATION = "train_evaluation"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExitStatus(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class AgentResult:
    exit_status: ExitStatus
    transcript_artifact: str


@dataclass(frozen=True)
class TrainResult:
    score: float
    cases: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Operation:
    id: str
    kind: OperationKind
    status: OperationStatus
    result: AgentResult | TrainResult | None = None
    error: str | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class Budget:
    max_usd: float
    max_wall_clock_s: int
    max_rollouts: int | None = None
    max_tokens: int | None = None
    spent_usd: float = 0.0
    wall_clock_s: int = 0
    rollouts: int = 0
    tokens: int = 0


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    cost_usd: float

"""Legacy Fuse retrieval DTOs and consumer contract.

TC implements this contract with the shared Card search pipeline. Mode labels
remain request context; the retired mode-specific scoring engine is removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.domain.models.retrieval_mode import RetrievalMode
from app.domain.models.scoring_signal import ScoringSignal
from app.domain.models.worker_profile import WorkerProfile


@dataclass
class RetrievalResult:
    profile: WorkerProfile
    total_score: float
    signals: list[ScoringSignal] = field(default_factory=list)
    rank: int = 0


@dataclass
class RetrievalResponse:
    results: list[RetrievalResult]
    question: str
    mode: RetrievalMode
    total_count: int = 0

    def __post_init__(self):
        if self.total_count == 0:
            self.total_count = len(self.results)


class WorkerProfileRetrievalService(Protocol):
    """Current profiles selected through shared retrieval for Fuse consumers."""

    def retrieve(
        self,
        question: str,
        mode: RetrievalMode,
        top_k: int | None = None,
        profile_keys: list[str] | None = None,
        skill_filter: list[str] | None = None,
        min_score: float = 0.0,
        strict_participants: bool | None = None,
    ) -> RetrievalResponse:
        """Read current participants and supplements; min_score is accepted only."""
        ...

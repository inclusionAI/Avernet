"""Data models shared between offline distillation and online extraction.

This package owns the experience-shaped records that ``distillation``
produces from bot conversation samples and that ``extraction`` queries at
run time. The fields here are intentionally minimal placeholders; persistence
and similarity-scoring details are deferred to the implementation step.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClarificationExperience:
    """One distilled lesson drawn from a bot conversation sample.

    Produced by offline :class:`DistillationService`; consumed by online
    :class:`ExtractionService`. Later steps add context tags, applicability
    bounds and signal metadata.
    """

    id: str
    summary: str
    source_session_id: str


@dataclass(frozen=True)
class DistillRequest:
    """Input for one offline distillation batch.

    Selects the bot conversation samples to read via
    :class:`BotSessionReader`. ``session_ids`` empty means "all available
    samples for this bot/owner since the last cursor".
    """

    bot_id: str
    owner_id: str
    session_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExperienceQuery:
    """Query used by online extraction to fetch relevant experiences."""

    goal_text: str
    context_text: str = ""
    top_k: int = 5


@dataclass(frozen=True)
class ExperiencePage:
    """Result of online extraction: an ordered list of experiences."""

    items: tuple[ClarificationExperience, ...] = ()

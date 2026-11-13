"""Read bot conversation/session samples for offline distillation.

Mirrors the multi-implementation pattern of
:mod:`agentclaw.community.core.task.task_discovery.task_reader`: a single
:class:`BotSessionReader` Protocol so the actual source can be swapped without
touching :class:`DistillationService`. The concrete readers (ORM-backed /
local stub / backfill from message pipeline) arrive at the implementation
step.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BotSessionSample:
    """One conversation/session sample loaded for distillation."""

    bot_id: str
    owner_id: str
    session_id: str
    # ``content`` shape is to be refined; the reader keeps it opaque to allow
    # raw transcript vs structured-summary inputs in later steps.
    content: str


@runtime_checkable
class BotSessionReader(Protocol):
    """Read bot conversation samples backing offline distillation."""

    @abstractmethod
    async def read(
        self,
        *,
        bot_id: str,
        owner_id: str,
        session_ids: tuple[str, ...] = (),
    ) -> tuple[BotSessionSample, ...]:
        """Return samples for the given bot/owner.

        Empty ``session_ids`` means "all available samples for this
        bot/owner"; concrete semantics (cursors, time bounds) are decided at
        the implementation step.
        """

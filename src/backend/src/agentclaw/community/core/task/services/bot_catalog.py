"""Bot catalog seam for the generalized搜推 (Phase 4.1, plan §2.4).

``BotDiscoverService`` needs a typed view of the available bots and their
capabilities to compute cover. The real bot-management service
(``BotServiceProtocol``) is a loose ``*args / **kwargs → Any`` surface not
suited to cover matching, so the task module owns this minimal typed seam.

This is a **task-internal** Port (not one of the 9 api Protocols) — like
``TaskRepo`` is a task-internal repository Port. Community default =
``LocalBotCatalog`` (a static configured list, non-bcsfuse); a prod adapter may
later wrap ``BotServiceProtocol`` into ``BotProfile``s.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class BotProfile:
    """Typed capability view of a bot for cover calculation."""

    bot_id: str
    summary: str = ""
    skills: list[str] = field(default_factory=list)


@runtime_checkable
class BotCatalogPort(Protocol):
    """Read-only catalog of bots available for搜推 (cover matching)."""

    def list_bots(self) -> list[BotProfile]:
        ...


class LocalBotCatalog:
    """Community default: a static list of bot profiles (non-bcsfuse, local).

    Defaults to an empty catalog — wire real/local bot profiles in the DI
    module (e.g., the singlebox 5-bot set or a configured list). Keeping the
    cover/route logic independent of the bot source is the point of the seam.
    """

    def __init__(self, bots: Optional[list[BotProfile]] = None) -> None:
        self._bots: list[BotProfile] = list(bots) if bots is not None else []

    def list_bots(self) -> list[BotProfile]:
        return list(self._bots)


__all__ = ["BotCatalogPort", "BotProfile", "LocalBotCatalog"]
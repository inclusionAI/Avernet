"""Per-engine dispatch seam for the Bot overview.

``BotOverviewService`` fills the identity and lifecycle fields itself and hands
everything else to the provider registered for the Bot's engine. Providers are
engine strategies (like ``core/bot_management/engines``), selected by the
Bot's persisted ``active_engine`` and registered at the composition root
(``di/modules/bot_overview_module.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .models import EngineBotOverview


@dataclass(frozen=True)
class BotOverviewQuery:
    """What a provider is asked about."""

    bot_id: str
    engine: str
    #: The Bot record as the repository returned it. Read-only; anything taken
    #: from it into the answer must be non-user data.
    bot: Mapping[str, Any]


@runtime_checkable
class EngineBotOverviewProvider(Protocol):
    """Fills the engine-reported part of a Bot overview."""

    def collect(self, query: BotOverviewQuery) -> EngineBotOverview:
        """Return what this engine can report about ``query.bot_id``."""
        ...


class BotOverviewProviderRegistry:
    """Maps a persisted engine name to its overview provider."""

    def __init__(self) -> None:
        self._providers: dict[str, EngineBotOverviewProvider] = {}

    def register(self, engine: str, provider: EngineBotOverviewProvider) -> None:
        if engine in self._providers:
            raise ValueError(f"overview provider already registered for engine {engine!r}")
        self._providers[engine] = provider

    def resolve(self, engine: str) -> EngineBotOverviewProvider | None:
        """The provider for ``engine``, or ``None`` when it has none."""
        return self._providers.get(engine)


__all__ = [
    "BotOverviewProviderRegistry",
    "BotOverviewQuery",
    "EngineBotOverviewProvider",
]

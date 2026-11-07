"""Assemble a Bot overview and dispatch the engine part to its provider."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

from .bot_overview_service_protocol import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)
from .models import BotOverview, EngineBotOverview
from .provider import BotOverviewProviderRegistry, BotOverviewQuery


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BotOverviewService(BotOverviewServiceProtocol):
    """Engine-neutral identity/status from the Bot record; the rest per engine."""

    def __init__(
        self,
        *,
        bot_repo: BotRepository,
        providers: BotOverviewProviderRegistry,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._bot_repo = bot_repo
        self._providers = providers
        self._clock = clock

    def get_overview(self, bot_id: str) -> BotOverview:
        bot = self._bot_repo.get_by_id(bot_id)
        if not bot:
            raise BotOverviewNotFoundError(bot_id)

        engine = str(bot.get("active_engine") or DEFAULT_ENGINE_TYPE)
        now = self._clock()
        provider = self._providers.resolve(engine)
        contribution = (
            provider.collect(BotOverviewQuery(bot_id=bot_id, engine=engine, bot=bot))
            if provider is not None
            else EngineBotOverview()
        )
        return BotOverview(
            bot_id=bot_id,
            engine=engine,
            status=str(bot.get("status") or ""),
            observed_at=now,
            default_model=contribution.default_model,
            skills=contribution.skills,
            mcps=contribution.mcps,
            sessions=contribution.sessions,
            payload=contribution.payload,
        )


__all__ = ["BotOverviewService"]

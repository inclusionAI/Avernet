"""Assemble a Bot overview: platform-owned fields here, the engine part per engine."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id
from agentclaw.community.core.mcp.effective_mcp_state_reader_protocol import (
    EffectiveMCPStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

from .bot_overview_service_protocol import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)
from .models import (
    BotOverview,
    EngineBotOverview,
    McpServerSummary,
    SessionActivity,
    SkillSummary,
)
from .provider import BotOverviewProviderRegistry, BotOverviewQuery
from .session_activity import BotSessionActivityReaderProtocol
from .skills import skill_summaries


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BotOverviewService(BotOverviewServiceProtocol):
    """Platform-owned fields from platform sources; the rest per engine.

    Skills and MCP servers come from the Skill Center's effective state and the
    last session time from the engine-neutral session route, so every engine
    reports them the same way. Engine providers only add ``default_model`` and
    the engine-specific payload.
    """

    def __init__(
        self,
        *,
        bot_repo: BotRepository,
        providers: BotOverviewProviderRegistry,
        capabilities: BotCapabilityStateReaderProtocol,
        mcps: EffectiveMCPStateReaderProtocol,
        sessions: BotSessionActivityReaderProtocol,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._bot_repo = bot_repo
        self._providers = providers
        self._capabilities = capabilities
        self._mcps = mcps
        self._sessions = sessions
        self._clock = clock

    async def get_overview(self, bot_id: str) -> BotOverview:
        # Repository and Skill Center reads are synchronous database work, so
        # they run in a worker thread, off the event loop.
        bot = await asyncio.to_thread(self._bot_repo.get_by_id, bot_id)
        if not bot:
            raise BotOverviewNotFoundError(bot_id)

        engine = str(bot.get("active_engine") or DEFAULT_ENGINE_TYPE)
        (contribution, skills, mcps), last_session_at = await asyncio.gather(
            asyncio.to_thread(self._collect_sync, bot_id, engine, bot),
            self._sessions.last_session_at(bot),
        )
        return BotOverview(
            bot_id=bot_id,
            engine=engine,
            status=str(bot.get("status") or ""),
            observed_at=self._clock(),
            default_model=contribution.default_model,
            skills=skills,
            mcps=mcps,
            sessions=SessionActivity(last_session_at=last_session_at),
            payload=contribution.payload,
        )

    def _collect_sync(
        self, bot_id: str, engine: str, bot: Mapping[str, Any]
    ) -> tuple[EngineBotOverview, list[SkillSummary], list[McpServerSummary]]:
        provider = self._providers.resolve(engine)
        contribution = (
            provider.collect(BotOverviewQuery(bot_id=bot_id, engine=engine, bot=bot))
            if provider is not None
            else EngineBotOverview()
        )
        owner_id = stable_resource_owner_id(bot)
        assets = self._capabilities.active_skill_assets(
            bot_id=bot_id, owner_id=owner_id, bot=bot
        )
        mcp_codes = self._mcps.effective_mcp_server_codes(
            bot_id=bot_id, owner_id=owner_id, bot=bot
        )
        return (
            contribution,
            skill_summaries(assets),
            [McpServerSummary(code=code) for code in sorted(mcp_codes)],
        )


__all__ = ["BotOverviewService"]

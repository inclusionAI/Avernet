"""BotOverviewService: neutral fields from the Bot record, the rest per engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_overview.bot_overview_service_protocol import (
    BotOverviewNotFoundError,
)
from agentclaw.community.core.bot_overview.engines.aicoding import (
    AICodingBotOverviewProvider,
)
from agentclaw.community.core.bot_overview.models import (
    DailySessionActivity,
    EngineBotOverview,
    McpServerSummary,
    SessionActivity,
    SkillSummary,
)
from agentclaw.community.core.bot_overview.payloads import AICodingEnginePayload
from agentclaw.community.core.bot_overview.provider import (
    BotOverviewProviderRegistry,
    BotOverviewQuery,
)
from agentclaw.community.core.bot_overview.service import BotOverviewService
from agentclaw.community.di.modules.bot_overview_module import BotOverviewModule

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _service(bot: dict | None, registry: BotOverviewProviderRegistry) -> BotOverviewService:
    repo = MagicMock()
    repo.get_by_id.return_value = bot
    return BotOverviewService(bot_repo=repo, providers=registry, clock=lambda: NOW)


def _bot(engine: str | None = "claude_code") -> dict:
    return {
        "bot_id": "b1",
        "active_engine": engine,
        "status": "ACTIVE",
        "owner_id": "u-secret",
    }


def test_missing_bot_raises_not_found() -> None:
    with pytest.raises(BotOverviewNotFoundError):
        _service(None, BotOverviewProviderRegistry()).get_overview("nope", activity_days=14)


def test_engine_without_provider_reports_only_neutral_identity() -> None:
    overview = _service(_bot("openclaw"), BotOverviewProviderRegistry()).get_overview(
        "b1", activity_days=14
    )

    assert overview.bot_id == "b1"
    assert overview.engine == "openclaw"
    assert overview.status == "ACTIVE"
    assert overview.observed_at == NOW
    assert overview.default_model is None
    assert overview.skills is None
    assert overview.mcps is None
    assert overview.sessions is None
    assert overview.payload is None


def test_missing_engine_falls_back_to_default_engine() -> None:
    overview = _service(_bot(None), BotOverviewProviderRegistry()).get_overview(
        "b1", activity_days=14
    )
    assert overview.engine == "openclaw"


def test_dispatches_to_the_bot_engines_provider_with_activity_window() -> None:
    contribution = EngineBotOverview(
        default_model="anthropic/model-x",
        skills=[SkillSummary(name="review", version="3")],
        mcps=[McpServerSummary(name="github", transport="stdio")],
        sessions=SessionActivity(
            window_start=NOW - timedelta(days=7),
            last_activity_at=NOW - timedelta(days=2),
            daily=[
                DailySessionActivity(
                    day=(NOW - timedelta(days=2)).date(), session_count=1, message_count=4
                )
            ],
        ),
        payload=AICodingEnginePayload(engine="claude_code"),
    )
    provider = MagicMock()
    provider.collect.return_value = contribution
    registry = BotOverviewProviderRegistry()
    registry.register("claude_code", provider)
    bot = _bot()

    overview = _service(bot, registry).get_overview("b1", activity_days=7)

    provider.collect.assert_called_once_with(
        BotOverviewQuery(
            bot_id="b1",
            engine="claude_code",
            bot=bot,
            activity_since=NOW - timedelta(days=7),
        )
    )
    assert overview.default_model == "anthropic/model-x"
    assert overview.skills == contribution.skills
    assert overview.mcps == contribution.mcps
    assert overview.sessions == contribution.sessions
    assert overview.payload == AICodingEnginePayload(engine="claude_code")


def test_overview_never_carries_the_owner() -> None:
    overview = _service(_bot("openclaw"), BotOverviewProviderRegistry()).get_overview(
        "b1", activity_days=14
    )
    assert "u-secret" not in overview.model_dump_json()


def test_registry_rejects_duplicate_engine() -> None:
    registry = BotOverviewProviderRegistry()
    registry.register("aicoding", AICodingBotOverviewProvider())
    with pytest.raises(ValueError):
        registry.register("aicoding", AICodingBotOverviewProvider())


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
def test_composition_routes_aicoding_family_to_draft_provider(engine: str) -> None:
    registry = BotOverviewModule().provide_bot_overview_provider_registry()

    overview = _service(_bot(engine), registry).get_overview("b1", activity_days=14)

    assert overview.payload == AICodingEnginePayload(engine=engine)
    assert overview.default_model is None
    assert overview.sessions is None

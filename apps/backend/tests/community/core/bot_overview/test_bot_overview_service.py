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
    EngineBotOverview,
    McpServerSummary,
    SessionActivity,
    SkillSummary,
)
from agentclaw.community.core.bot_overview.payloads import (
    AICodingEnginePayload,
    AICodingTemplateFactoryIdentity,
)
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


def _provider(template: dict | None = None) -> AICodingBotOverviewProvider:
    """The AICoding provider over a template repository answering ``template``."""
    template_repo = MagicMock()
    template_repo.get_by_bot_id.return_value = template
    return AICodingBotOverviewProvider(template_repo=template_repo)


def test_missing_bot_raises_not_found() -> None:
    with pytest.raises(BotOverviewNotFoundError):
        _service(None, BotOverviewProviderRegistry()).get_overview("nope")


def test_engine_without_provider_reports_only_neutral_identity() -> None:
    overview = _service(_bot("openclaw"), BotOverviewProviderRegistry()).get_overview("b1")

    assert overview.bot_id == "b1"
    assert overview.engine == "openclaw"
    assert overview.status == "ACTIVE"
    assert overview.observed_at == NOW
    assert overview.default_model is None
    assert overview.skills == []
    assert overview.mcps == []
    assert overview.sessions == SessionActivity(last_session_at=None)
    assert overview.payload is None


def test_missing_engine_falls_back_to_default_engine() -> None:
    overview = _service(_bot(None), BotOverviewProviderRegistry()).get_overview("b1")
    assert overview.engine == "openclaw"


def test_dispatches_to_the_bot_engines_provider() -> None:
    contribution = EngineBotOverview(
        default_model="anthropic/model-x",
        skills=[
            SkillSummary(
                skill_type="center",
                skill_id="skill-uuid-1",
                name="review",
                version="3",
            ),
            SkillSummary(skill_type="local", skill_id="notes", name="notes"),
        ],
        mcps=[McpServerSummary(code="mcp.example.github", transport="stdio")],
        sessions=SessionActivity(last_session_at=NOW - timedelta(days=2)),
        payload=AICodingEnginePayload(engine="claude_code"),
    )
    provider = MagicMock()
    provider.collect.return_value = contribution
    registry = BotOverviewProviderRegistry()
    registry.register("claude_code", provider)
    bot = _bot()

    overview = _service(bot, registry).get_overview("b1")

    provider.collect.assert_called_once_with(
        BotOverviewQuery(bot_id="b1", engine="claude_code", bot=bot)
    )
    assert overview.default_model == "anthropic/model-x"
    assert overview.skills == contribution.skills
    assert overview.mcps == contribution.mcps
    assert overview.sessions.last_session_at == NOW - timedelta(days=2)
    assert overview.payload == AICodingEnginePayload(engine="claude_code")


def test_overview_never_carries_the_owner() -> None:
    overview = _service(_bot("openclaw"), BotOverviewProviderRegistry()).get_overview("b1")
    assert "u-secret" not in overview.model_dump_json()


def test_registry_rejects_duplicate_engine() -> None:
    registry = BotOverviewProviderRegistry()
    registry.register("aicoding", _provider())
    with pytest.raises(ValueError):
        registry.register("aicoding", _provider())


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
def test_composition_routes_aicoding_family_to_its_provider(engine: str) -> None:
    template_repo = MagicMock()
    template_repo.get_by_bot_id.return_value = None
    registry = BotOverviewModule().provide_bot_overview_provider_registry(
        template_repo=template_repo
    )

    overview = _service(_bot(engine), registry).get_overview("b1")

    assert overview.payload == AICodingEnginePayload(engine=engine)
    assert overview.default_model is None
    assert overview.skills == [] and overview.mcps == []
    assert overview.sessions.last_session_at is None


def test_aicoding_payload_carries_the_bots_coding_template_fields() -> None:
    bot = _bot("claude_code")
    bot.update(
        {
            "bot_type": "service",
            "template_type": "applicationCoding",
            "template_config": {
                "template_key": "normalCC",
                "template_uid": "tpl-uid-1",
                "template_version": "3",
                "model": "claude-sonnet-4.5",
                "capabilities": {"dima_workspace": "true"},
                "devflow_workflow": {"name": "release-flow"},
                "repos": [
                    {"repo_url": "https://code.example.com/app.git"},
                    {"url": "https://code.example.com/lib.git"},
                ],
                "yuque_kb_repos": [
                    {
                        "url": "https://yuque.example.com/space/kb1",
                        "token": "team-token-secret",
                    },
                ],
                "business_wiki_spaces": [
                    "https://yuque.example.com/space/alias",
                ],
            },
        }
    )
    registry = BotOverviewProviderRegistry()
    registry.register("claude_code", _provider())

    overview = _service(bot, registry).get_overview("b1")

    assert overview.default_model == "claude-sonnet-4.5"
    assert overview.payload == AICodingEnginePayload(
        engine="claude_code",
        template_type="applicationCoding",
        template_version="3",
        bot_type="service",
        template_factory=AICodingTemplateFactoryIdentity(
            template_key="normalCC",
            template_uid="tpl-uid-1",
            template_version="3",
        ),
        hosted_workspace_enabled=True,
        devflow_workflow="release-flow",
        code_repos=[
            "https://code.example.com/app.git",
            "https://code.example.com/lib.git",
        ],
        yuque_kb_repos=[
            "https://yuque.example.com/space/kb1",
            "https://yuque.example.com/space/alias",
        ],
    )


def test_aicoding_payload_falls_back_to_the_stored_template_snapshot() -> None:
    bot = _bot("aicoding")
    bot["bot_type"] = "personal"
    template = {
        "bot_id": "b1",
        "ext": {
            "template_key": "architect",
            "template_uid": "tpl-uid-2",
            "template_version": "5",
            "model": "claude-opus-4.6",
            "devflow_workflow": "daily-build",
        },
    }
    registry = BotOverviewProviderRegistry()
    registry.register("aicoding", _provider(template))

    overview = _service(bot, registry).get_overview("b1")

    assert overview.default_model == "claude-opus-4.6"
    assert overview.payload is not None
    assert overview.payload.template_version == "5"
    assert overview.payload.template_factory == AICodingTemplateFactoryIdentity(
        template_key="architect", template_uid="tpl-uid-2", template_version="5"
    )
    assert overview.payload.devflow_workflow == "daily-build"
    assert overview.payload.bot_type == "personal"


def test_aicoding_payload_never_carries_template_credentials() -> None:
    bot = _bot("aicoding")
    bot["template_config"] = {
        "template_key": "normalCC",
        "template_uid": "tpl-uid-3",
        "token": "cf-token-secret",
        "yuque_kb_repos": [
            {"url": "https://yuque.example.com/space/kb", "token": "team-token-secret"},
        ],
    }
    registry = BotOverviewProviderRegistry()
    registry.register("aicoding", _provider())

    overview = _service(bot, registry).get_overview("b1")

    dumped = overview.model_dump_json()
    assert "cf-token-secret" not in dumped
    assert "team-token-secret" not in dumped
    assert overview.payload is not None
    assert overview.payload.yuque_kb_repos == ["https://yuque.example.com/space/kb"]

"""Unit tests for AICoding restart authorization refresh.

The restart path is best-effort and engine-owned:
  * refresh MCP scope;
  * project MCP details + allow-list to runtime;
  * resync ~/.claude/skills symlinks.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_authorization_listener import (
    AicodingRestartAuthorizationBaasPublishListener,
)
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.default import (
    DefaultProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import BaasPublishCompletedEvent

_AICODING_THREADING = (
    "agentclaw.community.core.bot_management.engines.aicoding.strategy.threading"
)


class _InlineThread:
    def __init__(self, target=None, **kwargs) -> None:
        self.target = target
        self.daemon = kwargs.get("daemon")

    def start(self) -> None:
        assert self.target is not None
        self.target()


def _ctx(active_engine: str = "claude_code") -> BotProvisioningContext:
    return BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=active_engine,
        template_type="architect",
    )


def _bot(entity_id: str = "ent-1", entity_type: str = "staff") -> dict:
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "bot_name": "my-bot",
        "bot_desc": "d",
        "id": 42,
        "owner_id": "owner-1",
        "binding_id": 7,
        "bot_type": "personal",
        "active_engine": "claude_code",
        "template_type": "architect",
    }


def test_aicoding_refresh_is_noop_without_opt_in() -> None:
    strategy = AicodingProvisioningStrategy("claude_code")
    mcp_sync = MagicMock()
    factory = MagicMock()

    assert (
        strategy.refresh_restart_authorization(
            _ctx(), _bot(), None, mcp_sync=mcp_sync, skill_set_factory=factory
        )
        is False
    )
    mcp_sync.refresh_mcp_scope.assert_not_called()
    factory.create.assert_not_called()


@pytest.mark.parametrize("flag", [True, 1, "yes"], ids=["true", "one", "truthy-str"])
def test_aicoding_refresh_projects_mcp_runtime_and_skills(flag) -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(return_value={"success": True})
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = ["mcp-a", "mcp-b"]
    skill_set_service.project_mcps = AsyncMock(return_value=True)
    skill_set_service.sync_runtime.return_value = True
    factory.create.return_value = skill_set_service
    template_service = MagicMock()
    template_service.get_template_config.return_value = {
        "template_version_id": 101,
        "_aicoding_restart": {"resync_authorization": True},
    }

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(entity_id="ent-9"),
            {"confirmed_template_update": flag},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
            template_service=template_service,
        ) is True

    mcp_sync.refresh_mcp_scope.assert_awaited_once_with(
        user_id="ent-9",
        entity_id="ent-9",
        bot_id="bot-1",
        entity_type="staff",
        engine_type="aicoding",
    )
    factory.create.assert_called_once_with(
        user_id="ent-9",
        entity_id="ent-9",
        bot_id="bot-1",
        entity_type="staff",
        engine_type="aicoding",
    )
    skill_set_service.get_bot_mcp_codes.assert_called_once_with(
        entity_id="ent-9",
        bot_id="bot-1",
        user_id="ent-9",
        entity_type="staff",
        engine_type="aicoding",
    )
    skill_set_service.project_mcps.assert_awaited_once_with(
        claimed=frozenset({"mcp-a", "mcp-b"}),
        released=frozenset(),
        declared={"mcp-a", "mcp-b"},
    )
    skill_set_service.sync_runtime.assert_called_once_with()
    template_service.update_template.assert_called_once()
    cleared = template_service.update_template.call_args.kwargs["template_config"]
    assert "_aicoding_restart" not in cleared


def test_aicoding_refresh_keeps_runtime_working_when_scope_fails() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(
        return_value={"success": False, "error": "scope denied"}
    )
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = ["mcp-a"]
    skill_set_service.project_mcps = AsyncMock(return_value=True)
    skill_set_service.sync_runtime.return_value = True
    factory.create.return_value = skill_set_service
    template_service = MagicMock()
    template_service.get_template_config.return_value = {
        "template_version_id": 101,
        "_aicoding_restart": {"resync_authorization": True},
    }

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
            template_service=template_service,
        ) is True

    skill_set_service.project_mcps.assert_awaited_once_with(
        claimed=frozenset({"mcp-a"}),
        released=frozenset(),
        declared={"mcp-a"},
    )
    skill_set_service.sync_runtime.assert_called_once_with()
    template_service.update_template.assert_not_called()


def test_aicoding_refresh_still_runs_skill_sync_when_projection_fails() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(return_value={"success": True})
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = ["mcp-a"]
    skill_set_service.project_mcps = AsyncMock(return_value=False)
    skill_set_service.sync_runtime.return_value = True
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
        ) is True

    skill_set_service.project_mcps.assert_awaited_once_with(
        claimed=frozenset({"mcp-a"}),
        released=frozenset(),
        declared={"mcp-a"},
    )
    skill_set_service.sync_runtime.assert_called_once_with()


def test_default_strategy_always_returns_false() -> None:
    strategy = DefaultProvisioningStrategy("openclaw")
    assert (
        strategy.refresh_restart_authorization(
            _ctx(),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=MagicMock(),
            skill_set_factory=MagicMock(),
            template_service=MagicMock(),
        )
        is False
    )


def test_baas_restart_publish_listener_dispatches_strategy_after_restart_event() -> None:
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "binding_id": 42,
        "bot_type": "personal",
        "active_engine": "claude_code",
        "template_type": "architect",
    }
    template_config = {
        "template_version_id": 101,
        "_aicoding_restart": {"resync_authorization": True},
    }
    template_service = MagicMock()
    template_service.get_template_config.return_value = template_config
    mcp_sync = object()
    factory = object()
    strategy = MagicMock()
    strategy.engine_type = "claude_code"
    strategy.refresh_restart_authorization.return_value = True

    listener = AicodingRestartAuthorizationBaasPublishListener(
        bot_repo=bot_repo,
        template_service=template_service,
        mcp_sync=mcp_sync,
        skill_set_factory=factory,
    )

    with patch(
        "agentclaw.community.core.bot_management.engines.aicoding.restart_authorization_listener.resolve_provisioning",
        return_value=("ctx", strategy),
    ) as resolve:
        listener.handle(
            BaasPublishCompletedEvent(
                binding_id=42,
                bot_id="bot-1",
                owner_id="owner-1",
                publish_id=1001,
                publish_kind="restart",
            )
        )

    resolve.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine="claude_code",
        template_type="architect",
        template_config=template_config,
    )
    strategy.refresh_restart_authorization.assert_called_once_with(
        "ctx",
        bot_repo.get_by_id_and_owner.return_value,
        None,
        mcp_sync=mcp_sync,
        skill_set_factory=factory,
        template_service=template_service,
    )


def test_baas_restart_publish_listener_ignores_non_restart_or_stale_binding() -> None:
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": 99}
    listener = AicodingRestartAuthorizationBaasPublishListener(
        bot_repo=bot_repo, template_service=MagicMock()
    )

    listener.handle(
        BaasPublishCompletedEvent(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            publish_id=1001,
            publish_kind="create",
        )
    )
    bot_repo.get_by_id_and_owner.assert_not_called()

    listener.handle(
        BaasPublishCompletedEvent(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            publish_id=1002,
            publish_kind="restart",
        )
    )
    bot_repo.get_by_id_and_owner.assert_called_once_with("bot-1", "owner-1")


def test_baas_restart_publish_listener_startup_is_idempotent() -> None:
    reset_event_bus()
    try:
        listener = AicodingRestartAuthorizationBaasPublishListener(
            bot_repo=MagicMock(), template_service=MagicMock()
        )
        handler = MagicMock()
        listener.handle = handler

        asyncio.run(listener.startup())
        asyncio.run(listener.startup())

        bus = get_event_bus()
        assert bus.is_subscribed(BaasPublishCompletedEvent, handler)
        bus.publish(
            BaasPublishCompletedEvent(
                binding_id=42,
                bot_id="bot-1",
                owner_id="owner-1",
                publish_id=1001,
                publish_kind="restart",
            )
        )
        handler.assert_called_once()
    finally:
        reset_event_bus()

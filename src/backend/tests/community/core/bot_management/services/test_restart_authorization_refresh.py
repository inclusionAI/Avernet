"""Unit tests for AICoding restart authorization refresh.

``refresh_restart_authorization`` now owns both the opt-in decision and the
AICoding-specific side effects:
  * project MCP/CLI through the runtime projector;
  * resync ~/.claude/skills symlinks via SkillSetService.project_skills().
All side effects are best-effort/fire-and-forget.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.bot_management.engines.default import (
    DefaultProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_authorization_listener import (
    AicodingRestartAuthorizationBaasPublishListener,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import BaasPublishCompletedEvent

_AICODING_THREADING = "agentclaw.community.core.bot_management.engines.aicoding.strategy.threading"


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
    }


@pytest.mark.parametrize(
    "extra_configs",
    [
        None,
        {},
        {"unrelated_key": True},
        {"confirmed_template_update": False},
        {"confirmed_template_update": None},
        "confirmed_template_update=true",
        ["confirmed"],
        True,
    ],
)
def test_aicoding_refresh_is_noop_without_opt_in(extra_configs) -> None:
    strategy = AicodingProvisioningStrategy("claude_code")
    factory = MagicMock()

    assert (
        strategy.refresh_restart_authorization(
            _ctx(), _bot(), extra_configs,
            skill_set_factory=factory,
        )
        is False
    )
    factory.create.assert_not_called()


@pytest.mark.parametrize("flag", [True, 1, "yes"], ids=["true", "one", "truthy-str"])
def test_aicoding_refresh_updates_mcp_and_skill_symlinks(flag) -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(return_value=None)
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(return_value=True)
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(entity_id="ent-9", entity_type="staff"),
            {"confirmed_template_update": flag},
            skill_set_factory=factory,
            runtime_reconciler=runtime_reconciler,
        ) is True

    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-9",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    factory.create.assert_called_once_with(
        user_id="ent-9",
        entity_id="ent-9",
        bot_id="bot-1",
        entity_type="staff",
        engine_type="aicoding",
    )
    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_is_best_effort_and_keeps_skill_sync_on_projection_error() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(side_effect=RuntimeError("projection down"))
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(return_value=True)
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
            runtime_reconciler=runtime_reconciler,
        ) is True
    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-1",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    factory.create.assert_called_once()
    skill_set_service.project_skills.assert_awaited_once_with()



def test_aicoding_refresh_skips_projection_when_runtime_not_ready_and_still_syncs_skills() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(return_value=True)
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
        ) is True

    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_logs_detail_and_skill_runtime_failures() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    skill_set_service = MagicMock()
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(return_value=None)
    skill_set_service.project_skills = AsyncMock(return_value=False)
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
            runtime_reconciler=runtime_reconciler,
        ) is True

    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-1",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_does_not_retry_mcp_projection_when_runtime_not_ready() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(return_value=True)
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
        ) is True

    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_does_not_retry_mcp_projection_exception() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(side_effect=RuntimeError("projection down"))
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(return_value=True)
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
            runtime_reconciler=runtime_reconciler,
        ) is True

    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-1",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_does_not_retry_skill_symlink_when_false() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(side_effect=[False, True])
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
        ) is True

    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_does_not_retry_skill_symlink_exception() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(
        side_effect=[RuntimeError("BaaS API error: NO_ACTIVE_DEVICES"), True]
    )
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
        ) is True

    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_swallows_non_transient_runtime_exceptions() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(side_effect=RuntimeError("detail down"))
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(side_effect=RuntimeError("skill down"))
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
            runtime_reconciler=runtime_reconciler,
        ) is True

    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-1",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    skill_set_service.project_skills.assert_awaited_once_with()


def test_aicoding_refresh_swallows_skill_sync_exception() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    factory.create.side_effect = RuntimeError("skill unavailable")

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=factory,
        ) is True

    factory.create.assert_called_once()


def test_default_strategy_always_returns_false() -> None:
    strategy = DefaultProvisioningStrategy("openclaw")
    assert (
        strategy.refresh_restart_authorization(
            _ctx(), _bot(), {"confirmed_template_update": True},
            skill_set_factory=MagicMock(),
            runtime_reconciler=MagicMock(),
        )
        is False
    )
    assert strategy.refresh_restart_authorization(_ctx(), _bot(), None) is False


def test_aicoding_refresh_clears_persisted_marker_after_confirmed_extra_success() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    stored_template_config = {
        "template_version_id": 101,
        "_aicoding_restart": {
            "resync_authorization": True,
            "template_version_id": 101,
        },
    }
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(return_value=None)
    template_service = MagicMock()
    template_service.get_template_config.return_value = stored_template_config

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            skill_set_factory=None,
            runtime_reconciler=runtime_reconciler,
            template_service=template_service,
        ) is True

    template_service.update_template.assert_called_once()
    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-1",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    cleared = template_service.update_template.call_args.kwargs["template_config"]
    assert "_aicoding_restart" not in cleared
    assert cleared["template_version_id"] == 101


def test_aicoding_refresh_uses_persisted_template_marker_and_clears_after_success() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    template_config = {
        "template_version_id": 101,
        "_aicoding_restart": {
            "resync_authorization": True,
            "template_version_id": 101,
        },
    }
    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine="aicoding",
        template_type="architect",
        template_config=template_config,
    )
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(return_value=None)
    template_service = MagicMock()
    template_service.get_template_config.return_value = template_config

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            ctx,
            _bot(),
            None,
            skill_set_factory=None,
            runtime_reconciler=runtime_reconciler,
            template_service=template_service,
        ) is True

    template_service.update_template.assert_called_once()
    cleared = template_service.update_template.call_args.kwargs["template_config"]
    assert "_aicoding_restart" not in cleared
    assert cleared["template_version_id"] == 101


def test_aicoding_refresh_keeps_succeeded_refresh_when_marker_clear_fails() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    template_config = {
        "template_version_id": 101,
        "_aicoding_restart": {
            "resync_authorization": True,
            "template_version_id": 101,
        },
    }
    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine="aicoding",
        template_type="architect",
        template_config=template_config,
    )
    runtime_reconciler = MagicMock()
    runtime_reconciler.project_mcp_and_cli = AsyncMock(return_value=None)
    skill_set_service = MagicMock()
    skill_set_service.project_skills = AsyncMock(return_value=True)
    factory = MagicMock()
    factory.create.return_value = skill_set_service
    template_service = MagicMock()
    template_service.get_template_config.return_value = template_config

    with patch.object(
        strategy,
        "_clear_restart_resync_marker",
        side_effect=RuntimeError("marker down"),
    ), patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            ctx,
            _bot(),
            None,
            skill_set_factory=factory,
            runtime_reconciler=runtime_reconciler,
            template_service=template_service,
        ) is True

    runtime_reconciler.project_mcp_and_cli.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="ent-1",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    skill_set_service.project_skills.assert_awaited_once_with()
    template_service.update_template.assert_not_called()


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
    runtime_reconciler = object()
    factory = object()
    strategy = MagicMock()
    strategy.engine_type = "claude_code"
    strategy.refresh_restart_authorization.return_value = True

    listener = AicodingRestartAuthorizationBaasPublishListener(
        bot_repo=bot_repo,
        template_service=template_service,
        skill_set_factory=factory,
        runtime_reconciler=runtime_reconciler,
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
        skill_set_factory=factory,
        runtime_reconciler=runtime_reconciler,
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

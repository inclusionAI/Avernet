"""Unit tests for AICoding restart authorization refresh.

``refresh_restart_authorization`` now owns both the opt-in decision and the
AICoding-specific side effects:
  * refresh MCP scope;
  * push MCP details to runtime;
  * resync ~/.claude/skills symlinks via SkillSetService.project_skills().
All side effects are best-effort/fire-and-forget.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.default import (
    DefaultProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)

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
    mcp_sync = MagicMock()
    factory = MagicMock()

    assert (
        strategy.refresh_restart_authorization(
            _ctx(), _bot(), extra_configs,
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
        )
        is False
    )
    mcp_sync.refresh_mcp_scope.assert_not_called()
    factory.create.assert_not_called()


@pytest.mark.parametrize("flag", [True, 1, "yes"], ids=["true", "one", "truthy-str"])
def test_aicoding_refresh_updates_mcp_and_skill_symlinks(flag) -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(return_value={"success": True})
    mcp_sync.sync_mcp_details = AsyncMock(return_value={"success": True})
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills.return_value = True
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(entity_id="ent-9", entity_type="staff"),
            {"confirmed_template_update": flag},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
        ) is True

    mcp_sync.refresh_mcp_scope.assert_awaited_once()
    scope_kwargs = mcp_sync.refresh_mcp_scope.await_args.kwargs
    assert scope_kwargs["user_id"] == "ent-9"
    assert scope_kwargs["entity_id"] == "ent-9"
    assert scope_kwargs["bot_id"] == "bot-1"
    assert scope_kwargs["entity_type"] == "staff"
    assert scope_kwargs["engine_type"] == "aicoding"
    assert "extra_cli_items" not in scope_kwargs
    mcp_sync.sync_mcp_details.assert_awaited_once_with(
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
    skill_set_service.project_skills.assert_called_once_with()


def test_aicoding_refresh_is_best_effort_and_keeps_skill_sync_on_mcp_error() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(side_effect=RuntimeError("scope down"))
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills.return_value = True
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
        ) is True
    factory.create.assert_called_once()
    skill_set_service.project_skills.assert_called_once_with()



def test_aicoding_refresh_logs_scope_failure_and_still_syncs_skills() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(
        return_value={"success": False, "error": "scope denied"}
    )
    mcp_sync.sync_mcp_details = AsyncMock()
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills.return_value = True
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
        ) is True

    mcp_sync.sync_mcp_details.assert_not_called()
    skill_set_service.project_skills.assert_called_once_with()


def test_aicoding_refresh_logs_detail_and_skill_runtime_failures() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    mcp_sync = MagicMock()
    mcp_sync.refresh_mcp_scope = AsyncMock(return_value={"success": True})
    mcp_sync.sync_mcp_details = AsyncMock(
        return_value={"success": False, "error": "detail down"}
    )
    factory = MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.project_skills.return_value = False
    factory.create.return_value = skill_set_service

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=mcp_sync,
            skill_set_factory=factory,
        ) is True

    mcp_sync.sync_mcp_details.assert_awaited_once()
    skill_set_service.project_skills.assert_called_once_with()


def test_aicoding_refresh_swallows_skill_sync_exception() -> None:
    strategy = AicodingProvisioningStrategy("aicoding")
    factory = MagicMock()
    factory.create.side_effect = RuntimeError("skill unavailable")

    with patch(_AICODING_THREADING, SimpleNamespace(Thread=_InlineThread)):
        assert strategy.refresh_restart_authorization(
            _ctx(active_engine="aicoding"),
            _bot(),
            {"confirmed_template_update": True},
            mcp_sync=None,
            skill_set_factory=factory,
        ) is True

    factory.create.assert_called_once()

def test_default_strategy_always_returns_false() -> None:
    strategy = DefaultProvisioningStrategy("openclaw")
    assert (
        strategy.refresh_restart_authorization(
            _ctx(), _bot(), {"confirmed_template_update": True},
            mcp_sync=MagicMock(),
            skill_set_factory=MagicMock(),
        )
        is False
    )
    assert strategy.refresh_restart_authorization(_ctx(), _bot(), None) is False

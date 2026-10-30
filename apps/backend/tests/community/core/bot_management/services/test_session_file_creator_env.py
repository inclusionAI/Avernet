"""Platform Bot names reach both personal and service runtime provisioning."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.services.sandbox_overrides import SandboxOverrides
from agentclaw.community.core.service_bot.services.deploy.service_publish_env import (
    _engine_extra_envs_from_bot,
)


@pytest.mark.parametrize("name", ["  报告助手  ", None, ""])
def test_personal_provisioning_preserves_engine_env_and_adds_known_name(name):
    strategy = SimpleNamespace(build_extra_envs=Mock(return_value={"EXISTING": "value"}))
    with patch("agentclaw.community.core.bot_management.engines.resolve_provisioning", return_value=(Mock(), strategy)):
        envs = BotService.__new__(BotService)._build_engine_extra_envs(
            bot_id="bot", owner_id="owner", active_engine="openclaw", bot_type="personal",
            template_type=None, template_config=None, log_context="test", bot_name=name,
        )
    expected = {"EXISTING": "value"}
    if name:
        expected["TEAMCLAW_BOT_NAME"] = name.strip()
    assert envs == expected


def test_service_provisioning_adds_name_when_engine_has_no_extra_envs():
    strategy = SimpleNamespace(build_extra_envs=Mock(return_value=None))
    with patch("agentclaw.community.core.service_bot.services.deploy.service_publish_env.resolve_provisioning", return_value=(Mock(), strategy)):
        assert _engine_extra_envs_from_bot({"bot_id": "bot", "bot_name": "报告助手"}) == {
            "TEAMCLAW_BOT_NAME": "报告助手",
        }


@pytest.mark.parametrize("base", [{}, {"TEAMCLAW_BOT_NAME": "报告助手"}])
def test_template_cannot_supply_or_replace_platform_name(base):
    overrides = SandboxOverrides(envs={"TEAMCLAW_BOT_NAME": "Other Bot", "CUSTOM": "value"})
    assert overrides.merged_envs(base) == {**base, "CUSTOM": "value"}

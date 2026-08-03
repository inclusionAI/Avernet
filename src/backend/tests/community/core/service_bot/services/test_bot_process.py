"""Tests for bot-type-specific binding response processors."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.services.bot_process import (
    BotProcessRegistry,
    EmptyBotProcess,
    PersonalBotProcess,
)


@pytest.mark.parametrize(
    ("template_config", "expected"),
    [
        ({"active_runtime_engine_type": "aicoding"}, "aicoding"),
        ({"active_runtime_engine_type": "  claude_code  "}, "claude_code"),
        ({"active_runtime_engine_type": ""}, ""),
        ({"active_runtime_engine_type": "   "}, ""),
        ({"active_runtime_engine_type": None}, ""),
        ({"active_runtime_engine_type": 1}, ""),
        ({"runtime": "codefuse-antcc"}, ""),
        (None, ""),
        ("not-a-dict", ""),
    ],
)
def test_personal_bot_process_reads_only_explicit_runtime_engine(
    template_config, expected
):
    template_service = Mock()
    template_service.get_template_config.return_value = template_config
    process = PersonalBotProcess(template_service)

    assert process.get_active_runtime_engine_type("bot_001") == expected
    template_service.get_template_config.assert_called_once_with("bot_001")


def test_empty_bot_process_returns_empty_runtime_engine():
    assert EmptyBotProcess().get_active_runtime_engine_type("bot_001") == ""


def test_registry_selects_personal_process_and_defaults_other_types():
    personal = Mock()
    default = Mock()
    registry = BotProcessRegistry(personal, default)

    assert registry.get("personal") is personal
    assert registry.get("service") is default
    assert registry.get("desktop") is default
    assert registry.get("") is default

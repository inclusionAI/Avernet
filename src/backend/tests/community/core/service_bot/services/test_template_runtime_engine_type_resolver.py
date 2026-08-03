"""Tests for bot-type-specific template runtime engine resolution."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.services.template_runtime_engine_type_resolver import (
    BotTypeTemplateRuntimeEngineTypeResolver,
    EmptyTemplateRuntimeEngineTypeResolver,
    PersonalTemplateRuntimeEngineTypeResolver,
)


@pytest.mark.parametrize(
    ("template_config", "expected"),
    [
        ({"template_runtime_engine_type": " claude_code "}, "claude_code"),
        ({}, ""),
        ({"template_runtime_engine_type": None}, ""),
        ({"template_runtime_engine_type": 123}, ""),
        (None, ""),
    ],
)
def test_personal_resolver_reads_explicit_template_runtime_engine_type(
    template_config, expected
):
    template_service = Mock()
    template_service.get_template_config.return_value = template_config
    resolver = PersonalTemplateRuntimeEngineTypeResolver(template_service)

    result = resolver.resolve(bot_type="personal", bot_id="bot-1")

    assert result == expected
    template_service.get_template_config.assert_called_once_with("bot-1")


def test_empty_resolver_returns_empty_string():
    resolver = EmptyTemplateRuntimeEngineTypeResolver()

    assert resolver.resolve(bot_type="service", bot_id="bot-1") == ""


def test_bot_type_resolver_uses_empty_resolver_for_non_personal_bot():
    template_service = Mock()
    resolver = BotTypeTemplateRuntimeEngineTypeResolver(
        resolvers={
            "personal": PersonalTemplateRuntimeEngineTypeResolver(template_service)
        },
        default_resolver=EmptyTemplateRuntimeEngineTypeResolver(),
    )

    result = resolver.resolve(bot_type="service", bot_id="bot-1")

    assert result == ""
    template_service.get_template_config.assert_not_called()

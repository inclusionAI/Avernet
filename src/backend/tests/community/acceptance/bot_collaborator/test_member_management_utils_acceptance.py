"""Acceptance coverage for bot collaborator member-management helpers."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.services.aicoding.utils.member_management import (
    get_template_ext,
    has_member_management_enabled,
    is_coding_app_bot,
    is_member_management_enabled_bot,
)


def _enabled_template_ext() -> dict:
    return {"bot_template_config": {"advanced_config": {"member_management": True}}}


@pytest.mark.acceptance
def test_member_management_acceptance_allows_coding_app_bot_or_template_switch():
    """Member-management semantics allow coding-app bots and template-ext switch bots."""
    assert is_coding_app_bot(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}
    ) is True
    assert is_member_management_enabled_bot(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}
    ) is True
    assert is_member_management_enabled_bot(
        {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": _enabled_template_ext(),
        }
    ) is True


@pytest.mark.acceptance
def test_member_management_acceptance_rejects_malformed_or_truthy_values():
    """Only ac_templates.ext boolean True enables collaborator member management."""
    assert is_coding_app_bot(None) is False
    assert is_member_management_enabled_bot(None) is False
    assert is_coding_app_bot(
        {"active_engine": "claude_code", "template_type": "chat"}
    ) is False
    assert has_member_management_enabled(None) is False
    assert has_member_management_enabled({"bot_template_config": None}) is False
    assert has_member_management_enabled(
        {"bot_template_config": {"advanced_config": None}}
    ) is False
    assert has_member_management_enabled(
        {"bot_template_config": {"advanced_config": {"member_management": "true"}}}
    ) is False
    assert is_member_management_enabled_bot(
        {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {
                    "advanced_config": {"member_management": "true"}
                }
            },
        }
    ) is False


@pytest.mark.acceptance
def test_template_ext_acceptance_reads_ac_templates_ext_from_template_service():
    """Service path reads template ext/config instead of relying on ac_bots.ext."""
    template_service = Mock()
    template_service.get_template_config.return_value = _enabled_template_ext()

    assert get_template_ext(template_service, "bot-123") == _enabled_template_ext()
    template_service.get_template_config.assert_called_once_with("bot-123")


@pytest.mark.acceptance
def test_template_ext_acceptance_fallback_and_safe_failures():
    """Template ext lookup safely falls back and fails closed."""

    class TemplateServiceWithoutConfig:
        def get_template(self, bot_id: str) -> dict:
            assert bot_id == "bot-123"
            return {"ext": _enabled_template_ext()}

    assert (
        get_template_ext(TemplateServiceWithoutConfig(), "bot-123")
        == _enabled_template_ext()
    )
    assert get_template_ext(None, "bot-123") is None

    non_dict_config_service = Mock()
    non_dict_config_service.get_template_config.return_value = "not-a-dict"
    assert get_template_ext(non_dict_config_service, "bot-123") is None

    failing_service = Mock()
    failing_service.get_template_config.side_effect = RuntimeError("db down")
    assert get_template_ext(failing_service, "bot-123") is None

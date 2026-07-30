"""Tests for AICoding collaborator/member-management utility helpers."""
from unittest.mock import Mock

from agentclaw.community.core.bot_collaborator.services.aicoding.utils.member_management import (
    get_template_ext,
    has_member_management_enabled,
    is_coding_app_bot,
    is_member_management_enabled_bot,
)


def test_is_coding_app_bot_matches_only_application_coding():
    assert is_coding_app_bot(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}
    ) is True
    assert is_coding_app_bot(
        {"active_engine": "claude_code", "template_type": "chat"}
    ) is False
    assert is_coding_app_bot(None) is False


def test_has_member_management_enabled_requires_boolean_true():
    assert has_member_management_enabled(
        {"bot_template_config": {"advanced_config": {"member_management": True}}}
    ) is True
    assert has_member_management_enabled(
        {"bot_template_config": {"advanced_config": {"member_management": "true"}}}
    ) is False


def test_has_member_management_enabled_handles_malformed_template_ext():
    assert has_member_management_enabled(None) is False
    assert has_member_management_enabled({"bot_template_config": None}) is False
    assert has_member_management_enabled({"bot_template_config": {"advanced_config": None}}) is False


def test_get_template_ext_returns_config_from_get_template_config():
    template_service = Mock()
    template_service.get_template_config.return_value = {"foo": "bar"}

    assert get_template_ext(template_service, "bot-123") == {"foo": "bar"}
    template_service.get_template_config.assert_called_once_with("bot-123")


def test_get_template_ext_ignores_non_dict_config():
    template_service = Mock()
    template_service.get_template_config.return_value = "not-a-dict"

    assert get_template_ext(template_service, "bot-123") is None


def test_get_template_ext_falls_back_to_get_template_ext_field():
    class TemplateServiceWithoutConfig:
        def get_template(self, bot_id):
            assert bot_id == "bot-123"
            return {"ext": {"foo": "bar"}}

    assert get_template_ext(TemplateServiceWithoutConfig(), "bot-123") == {"foo": "bar"}


def test_get_template_ext_handles_missing_service_and_query_failure():
    assert get_template_ext(None, "bot-123") is None

    template_service = Mock()
    template_service.get_template_config.side_effect = RuntimeError("db down")

    assert get_template_ext(template_service, "bot-123") is None


def test_is_member_management_enabled_bot_uses_coding_or_template_config():
    assert is_member_management_enabled_bot(None) is False
    assert is_member_management_enabled_bot(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}
    ) is True
    assert is_member_management_enabled_bot(
        {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {"advanced_config": {"member_management": True}}
            },
        }
    ) is True
    assert is_member_management_enabled_bot(
        {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {"advanced_config": {"member_management": "true"}}
            },
        }
    ) is False

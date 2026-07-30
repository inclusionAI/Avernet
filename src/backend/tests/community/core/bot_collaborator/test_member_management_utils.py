"""Tests for member-management capability extension points."""
from unittest.mock import Mock

from agentclaw.community.core.bot_collaborator.services.aicoding.member_management_capability import (
    AICodingMemberManagementCapability,
    get_template_ext,
    has_member_management_enabled,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)


def _capability_service(template_service=None):
    return MemberManagementCapabilityService(
        engine_capabilities=(AICodingMemberManagementCapability(template_service),),
    )


def test_aicoding_capability_matches_only_application_coding():
    capability = AICodingMemberManagementCapability()

    assert capability.is_member_management_enabled(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}, None
    ) is True
    assert capability.is_member_management_enabled(
        {"active_engine": "claude_code", "template_type": "chat"}, None
    ) is False


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
    assert has_member_management_enabled(
        {"bot_template_config": {"advanced_config": None}}
    ) is False


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


def test_capability_service_uses_engine_or_template_config():
    service = _capability_service()

    assert service.uses_member_management_semantics(None) is False
    assert service.uses_member_management_semantics(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}
    ) is True
    assert service.uses_member_management_semantics(
        {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {"advanced_config": {"member_management": True}}
            },
        }
    ) is True
    assert service.uses_member_management_semantics(
        {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {"advanced_config": {"member_management": "true"}}
            },
        }
    ) is False


def test_capability_service_can_manage_collaborators_keeps_service_bot_behavior():
    service = _capability_service()

    assert service.can_manage_collaborators({"bot_type": "service"}, "bot-123") is True
    assert service.can_manage_collaborators(
        {
            "bot_type": "personal",
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
        },
        "bot-123",
    ) is True
    assert service.can_manage_collaborators({"bot_type": "personal"}, "bot-123") is False

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


def test_aicoding_capability_matches_coding_bots():
    capability = AICodingMemberManagementCapability()

    # 应用 Coding Bot 与个人 Coding Bot 放行;引擎拼写按 engine/form 词汇
    # 分裂前后两半皆认(post-split ``claude_code`` 与 legacy ``aicoding`` 字面值)。
    assert capability.is_member_management_enabled(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}, None
    ) is True
    assert capability.is_member_management_enabled(
        {"active_engine": "claude_code", "template_type": "personalCoding"}, None
    ) is True
    assert capability.is_member_management_enabled(
        {"active_engine": "aicoding", "template_type": "applicationCoding"}, None
    ) is True
    assert capability.is_member_management_enabled(
        {"active_engine": "aicoding", "template_type": "personalCoding"}, None
    ) is True
    # 非 coding 模板仍拒绝;member 语义不随 runtime 谓词放宽到 architect 等
    # 其它 coding 形态模板。
    assert capability.is_member_management_enabled(
        {"active_engine": "claude_code", "template_type": "chat"}, None
    ) is False
    assert capability.is_member_management_enabled(
        {"active_engine": "claude_code", "template_type": "architect"}, None
    ) is False
    # personalCoding 的无关引擎(未参与词汇分裂的引擎)拒绝
    assert capability.is_member_management_enabled(
        {"active_engine": "openclaw", "template_type": "personalCoding"}, None
    ) is False


def test_capability_service_can_manage_collaborators_allows_personal_coding():
    service = _capability_service()

    # bot_type 非 service 的个人 Coding Bot（claude_code 引擎）应允许成员管理
    assert service.can_manage_collaborators(
        {"bot_type": "personal", "active_engine": "claude_code", "template_type": "personalCoding"},
        "bot-123",
    ) is True


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


def test_is_coding_app_bot_matches_both_engine_spells():
    from agentclaw.community.core.bot_collaborator.services.aicoding.utils.member_management import (
        is_coding_app_bot,
    )

    # 两半拼写皆认:post-split claude_code 与 legacy aicoding 字面值。
    assert is_coding_app_bot(
        {"active_engine": "claude_code", "template_type": "applicationCoding"}
    ) is True
    assert is_coding_app_bot(
        {"active_engine": "aicoding", "template_type": "applicationCoding"}
    ) is True
    # personalCoding 不是应用 coding;无关引擎字典拼写拒绝。
    assert is_coding_app_bot(
        {"active_engine": "claude_code", "template_type": "personalCoding"}
    ) is False
    assert is_coding_app_bot(
        {"active_engine": "openclaw", "template_type": "applicationCoding"}
    ) is False
    assert is_coding_app_bot("not-a-bot") is False

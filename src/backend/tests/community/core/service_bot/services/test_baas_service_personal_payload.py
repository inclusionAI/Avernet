"""Tests for ``BaasService._build_personal_bot_payload``.

The payload schema must match BaaS ``PoolabCreateConfig`` field names
(``poolab_user_id``, ``poolab_envs``, ``poolab_image_id``, ``poolab_tenant_id``).
Fields without the ``poolab_`` prefix are filtered by BaaS's
``_POOLAB_ALLOWED_OVERRIDE_FIELDS`` whitelist and silently dropped.
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService, BaasServiceError


def _make_service(personal_bot_template_uuid: str = "TEMPLATE-poolab") -> BaasService:
    return BaasService(
        baas_api_base="http://test",
        tenant="test",
        template_uuid="legacy-uuid",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=MagicMock(),
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
        personal_bot_template_uuid=personal_bot_template_uuid,
    )


class TestBuildPersonalBotPayloadShape:
    """Verify the on-wire shape matches BaaS PoolabCreateConfig."""

    def test_minimal_required_fields(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
        )

        assert payload["name"] == "my-bot"
        assert payload["template_uuid"] == "TEMPLATE-poolab"
        assert payload["device_count"] == 1
        assert payload["operator"] == "u001"
        assert payload["request_id"] == "req-" + "x" * 30
        assert "description" not in payload

        config = payload["config"]
        assert config["entity_id"] == "staff_u001"
        assert config["entity_type"] == "staff"

        deploy = config["deploy_config"]
        assert deploy == {"poolab_user_id": "u001"}

    def test_optional_envs_included_when_provided(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            envs={"BOT_TYPE": "personalCoding", "AIX_DEVFLOW_INFO": "..."},
        )

        deploy = payload["config"]["deploy_config"]
        assert deploy["poolab_envs"] == {"BOT_TYPE": "personalCoding", "AIX_DEVFLOW_INFO": "..."}

    def test_optional_image_id_included_when_provided(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            image_id="img-aaa",
        )

        deploy = payload["config"]["deploy_config"]
        assert deploy["poolab_image_id"] == "img-aaa"

    def test_optional_tenant_id_included_when_provided(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            tenant_id="tenant-123",
        )

        deploy = payload["config"]["deploy_config"]
        assert deploy["poolab_tenant_id"] == "tenant-123"

    def test_all_optional_fields_together(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc="desc",
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            envs={"K": "V"},
            image_id="img-x",
            tenant_id="t-1",
        )
        deploy = payload["config"]["deploy_config"]
        assert deploy["poolab_user_id"] == "u001"
        assert deploy["poolab_envs"] == {"K": "V"}
        assert deploy["poolab_image_id"] == "img-x"
        assert deploy["poolab_tenant_id"] == "t-1"

    def test_empty_envs_not_emitted(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            envs={},
        )
        assert "poolab_envs" not in payload["config"]["deploy_config"]

    def test_explicit_template_uuid_overrides_default(self):
        svc = _make_service(personal_bot_template_uuid="TEMPLATE-default")
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            template_uuid="TEMPLATE-override",
        )
        assert payload["template_uuid"] == "TEMPLATE-override"

    def test_description_passed_through(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc="A helpful bot",
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
        )
        assert payload["description"] == "A helpful bot"

    def test_name_falls_back_to_bot_id_when_name_empty(self):
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
        )
        assert payload["name"] == "default"

    def test_excludes_desktop_or_service_only_fields(self):
        """Fields from desktop/service builder must NOT leak — they are
        not in BaaS _POOLAB_ALLOWED_OVERRIDE_FIELDS and would be dropped."""
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            envs={"a": "b"},
            image_id="img-x",
        )
        deploy = payload["config"]["deploy_config"]
        for forbidden in (
            "after_create_cmd_hook",
            "before_destroy_cmd_hook",
            "after_create_hook_wait_seconds",
            "before_destroy_hook_wait_seconds",
            "mount_points",
            "ttl_in_minutes",
            "outbound_operation_rule",
            "machine_id",
            "mount_path",
            "agent_code",
            "tc_bot_id",
            # old unprefixed names must not appear
            "user_id",
            "envs",
            "image_id",
        ):
            assert forbidden not in deploy, f"{forbidden!r} leaked into personal payload"

    def test_uses_poolab_prefixed_field_names(self):
        """All deploy_config keys must use poolab_ prefix to pass
        BaaS _POOLAB_ALLOWED_OVERRIDE_FIELDS whitelist."""
        svc = _make_service()
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            envs={"K": "V"},
            image_id="img-1",
            tenant_id="t-1",
        )
        deploy = payload["config"]["deploy_config"]
        allowed_prefixes = {"poolab_"}
        for key in deploy:
            assert any(
                key.startswith(p) for p in allowed_prefixes
            ), f"deploy_config key {key!r} missing poolab_ prefix"


class TestTemplateUuidConfiguration:
    def test_raises_when_template_uuid_not_configured(self):
        svc = _make_service(personal_bot_template_uuid="")
        with pytest.raises(BaasServiceError, match="personal_bot_template_uuid"):
            svc._build_personal_bot_payload(
                bot_id="default",
                bot_name="my-bot",
                bot_desc=None,
                entity_id="staff_u001",
                entity_type="staff",
                owner_id="u001",
                request_id="req-" + "x" * 30,
            )

    def test_explicit_template_uuid_works_without_default(self):
        svc = _make_service(personal_bot_template_uuid="")
        payload = svc._build_personal_bot_payload(
            bot_id="default",
            bot_name="my-bot",
            bot_desc=None,
            entity_id="staff_u001",
            entity_type="staff",
            owner_id="u001",
            request_id="req-" + "x" * 30,
            template_uuid="TEMPLATE-override",
        )
        assert payload["template_uuid"] == "TEMPLATE-override"


class TestUpgradeBotMigrationPathRequirement:
    """普通重启可不传 migration_path；发布态 service upgrade 仍必填。"""

    def test_personal_bot_allows_none_migration_path(self):
        svc = _make_service()
        svc._build_create_bot_payload = MagicMock(return_value={"k": "v"})
        svc._post_bots_api = MagicMock(return_value={"ok": True})

        result = svc.upgrade_bot(
            bot_uuid="bot-uuid",
            bot={"bot_id": "default", "bot_type": "personal"},
            owner_id="u001",
            request_id="req-" + "x" * 30,
            migration_path=None,
            mount_home_dir_storage=True,
        )

        assert result == {"ok": True}
        # None 归一为 "" 进 builder，避免 None 进 normalize 崩溃
        assert svc._build_create_bot_payload.call_args.kwargs["migration_path"] == ""
        assert svc._build_create_bot_payload.call_args.kwargs["mount_home_dir_storage"] is True

    def test_service_draft_bot_allows_none_migration_path(self):
        svc = _make_service()
        svc._build_create_bot_payload = MagicMock(return_value={"k": "v"})
        svc._post_bots_api = MagicMock(return_value={"ok": True})

        result = svc.upgrade_bot(
            bot_uuid="bot-uuid",
            bot={"bot_id": "default", "bot_type": "service"},
            owner_id="u001",
            request_id="req-" + "x" * 30,
            migration_path=None,
            stage="draft",
            mount_home_dir_storage=True,
        )

        assert result == {"ok": True}
        assert svc._build_create_bot_payload.call_args.kwargs["migration_path"] == ""
        assert svc._build_create_bot_payload.call_args.kwargs["stage"] == "draft"
        assert svc._build_create_bot_payload.call_args.kwargs["mount_home_dir_storage"] is True

    def test_service_release_empty_migration_path_raises(self):
        svc = _make_service()
        with pytest.raises(BaasServiceError, match="migration_path is required"):
            svc.upgrade_bot(
                bot_uuid="bot-uuid",
                bot={"bot_id": "default", "bot_type": "service"},
                owner_id="u001",
                request_id="req-" + "x" * 30,
                migration_path="",
                stage="online",
            )


def test_baas_wrapper_injects_bot_type_resolver_from_bot_repo():
    """BaasService 透传给 OutboundRuleProvider 的 resolver 从 _bot_repo 取 bot_type。"""
    from unittest.mock import MagicMock

    svc = _make_service()
    svc._outbound_rule_provider = MagicMock()
    svc._bot_repo.get_by_id_and_owner.return_value = {"bot_type": "service"}

    svc._build_outbound_operation_rule(bot_id="b1", owner_id="o1")

    resolver = svc._outbound_rule_provider.build_rule.call_args.kwargs["bot_type_resolver"]
    assert resolver("b1", "o1") == "service"
    svc._bot_repo.get_by_id_and_owner.assert_called_with(bot_id="b1", owner_id="o1")


def test_baas_wrapper_resolver_returns_none_when_bot_missing():
    """_bot_repo 返回 None 时 resolver 安全返回 None（不抛）。"""
    from unittest.mock import MagicMock

    svc = _make_service()
    svc._outbound_rule_provider = MagicMock()
    svc._bot_repo.get_by_id_and_owner.return_value = None

    svc._build_outbound_operation_rule(bot_id="b1", owner_id="o1")

    resolver = svc._outbound_rule_provider.build_rule.call_args.kwargs["bot_type_resolver"]
    assert resolver("b1", "o1") is None

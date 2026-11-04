"""Unit tests for create_bot_instances reading template_config from ac_templates.

回归 b2531b570：``create_bot_instances`` 必须通过
``TemplateService.get_template_config(source_bot_id)`` 读取沙箱覆写参数
（image / command / envs / resource_spec），而不是从
``source_bot.get("template_config")`` 读取——后者来自 ac_bots 表，
该表没有 template_config 列。

由于 create_bot_instances 方法依赖较多（文件系统操作、并发等），
这里通过 mock _copy_tree_fast 和 get_bot_dir 等依赖来隔离测试。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.models import DeviceBindingStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> BotService:
    """构造一个绕过 __init__ 的 BotService，只装填本测试关心的依赖。"""
    svc = BotService.__new__(BotService)
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = MagicMock()
    svc._template_service = MagicMock()
    svc._oss_record_repo = MagicMock()
    svc._oss_record_repo.get_record.return_value = None
    svc._query_admin_worknos = MagicMock(return_value=None)

    # device service 默认成功分配
    device_svc = MagicMock()
    device_svc.apply_device.return_value = SimpleNamespace(
        id="bind-inst-1",
        device_id="dev-inst-1",
        device_provider="arca",
        status=DeviceBindingStatus.ACTIVE.value,
    )
    svc._device_service_provider = lambda: device_svc

    # skill_set_service
    skill_set_svc = MagicMock()
    skill_set_svc.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_svc
    svc._baas_template_resolver = None

    return svc


def _make_source_bot(
    *,
    bot_id: str = "source_bot_001",
    template_type: str = "service",
    active_engine: str = "moltis",
    entity_id: str = "staff_user001",
    owner_id: str = "user001",
) -> dict:
    """构造 get_by_id_and_owner 返回的 source_bot record。"""
    return {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "entity_id": entity_id,
        "entity_type": "staff",
        "bot_type": "service",
        "template_type": template_type,
        "active_engine": active_engine,
    }


def _run_create_instances(svc: BotService, **kwargs) -> MagicMock:
    """调用 create_bot_instances 并返回 apply_device mock。"""
    # Mock path_factory.get_bot_dir to return a mock path with exists=True
    mock_bot_dir = MagicMock()
    mock_bot_dir.exists.return_value = True

    with patch(
        "agentclaw.community.core.workspace.path_factory.get_bot_dir",
        return_value=mock_bot_dir,
    ), patch(
        "agentclaw.community.core.bot_management.services.bot_service._copy_tree_fast",
    ), patch(
        "agentclaw.community.core.bot_management.services.bot_service.BotService._is_new_bot_use_nas",
        return_value=False,
    ):
        svc.create_bot_instances(
            source_bot_id=kwargs.get("source_bot_id", "source_bot_001"),
            bot_id_with_version=kwargs.get("bot_id_with_version", "source_bot_v001"),
            pub_bot_id=kwargs.get("pub_bot_id", "source_bot_p001"),
            owner_id=kwargs.get("owner_id", "user001"),
            instance_count=1,
            operator=MagicMock(),
        )

    device_svc = svc._device_service_provider()
    return device_svc.apply_device


# ===========================================================================
# create_bot_instances template_config 透传
# ===========================================================================


class TestCreateBotInstancesTemplateConfig:
    """create_bot_instances 必须从 ac_templates.ext 读取 template_config
    并透传给 apply_device，而不是从 source_bot（ac_bots）读取。"""

    def test_template_config_passed_to_apply_device(self):
        """get_template_config 返回的值必须原样透传给 apply_device 的
        template_config 参数。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_source_bot()

        sandbox_overrides = {
            "image": "registry.example.com/service:latest",
            "command": "python /app/server.py",
            "envs": {"MODE": "production"},
            "resource_spec": {"cpu": 8, "memory": 16},
        }
        svc._template_service.get_template_config.return_value = sandbox_overrides

        apply_device = _run_create_instances(svc)

        apply_device.assert_called_once()
        _, kwargs = apply_device.call_args
        assert kwargs["template_config"] == sandbox_overrides, (
            "create_bot_instances must pass template_config from ac_templates.ext "
            "to apply_device; None means sandbox overrides are lost for instances"
        )

    def test_template_config_none_when_no_template(self):
        """get_template_config 返回 None 时，apply_device 的
        template_config 也应为 None——存量 bot 不受影响。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_source_bot()
        svc._template_service.get_template_config.return_value = None

        apply_device = _run_create_instances(svc)

        _, kwargs = apply_device.call_args
        assert kwargs["template_config"] is None

    def test_resolves_template_uid_before_instance_apply_device(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_source_bot(
            template_type="draft",
            active_engine="openclaw",
        )
        svc._template_service.get_template_config.return_value = None
        resolver = MagicMock()
        resolver.resolve_template_uid.return_value = "openclaw_service_draft_default"
        svc._baas_template_resolver = resolver

        apply_device = _run_create_instances(svc)

        resolver.resolve_template_uid.assert_called_once_with(
            bot_id="source_bot_p001",
            user_id="user001",
            env="dev",
            bot_type="service",
            engine_type="openclaw",
            template_type="draft",
            template_config=None,
        )
        _, kwargs = apply_device.call_args
        assert kwargs["template_config"] == {
            "template_uid": "openclaw_service_draft_default"
        }

    def test_template_config_survives_lookup_error(self):
        """get_template_config 抛异常时，instance_template_config 退化为 None，
        apply_device 仍然被调用（template_config=None），实例创建不被中断。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_source_bot()
        svc._template_service.get_template_config.side_effect = RuntimeError("db error")

        apply_device = _run_create_instances(svc)

        apply_device.assert_called_once()
        _, kwargs = apply_device.call_args
        assert kwargs["template_config"] is None

    def test_not_reading_template_config_from_source_bot(self):
        """防御回归：source_bot（来自 ac_bots）没有 template_config 列，
        create_bot_instances 不应尝试从 source_bot 读取 template_config。"""
        svc = _make_service()
        # source_bot 故意包含一个 template_config key 来模拟旧代码的误读
        source_bot = _make_source_bot()
        source_bot["template_config"] = "THIS_SHOULD_NOT_BE_USED"
        svc._repository.get_by_id_and_owner.return_value = source_bot

        correct_config = {"image": "registry.example.com/correct:latest"}
        svc._template_service.get_template_config.return_value = correct_config

        apply_device = _run_create_instances(svc)

        _, kwargs = apply_device.call_args
        # 必须用 get_template_config 的返回值，不是 source_bot 上的值
        assert kwargs["template_config"] == correct_config
        assert kwargs["template_config"] != "THIS_SHOULD_NOT_BE_USED"

    def test_get_template_config_called_with_source_bot_id(self):
        """get_template_config 必须用 source_bot_id 调用，
        不是 pub_bot_id。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_source_bot()
        svc._template_service.get_template_config.return_value = None

        _run_create_instances(svc, source_bot_id="source_bot_001")

        svc._template_service.get_template_config.assert_called_once_with(
            "source_bot_001"
        )

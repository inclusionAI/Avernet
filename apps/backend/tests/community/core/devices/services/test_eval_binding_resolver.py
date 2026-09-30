"""评测沙箱 default_tag 路由解析测试。

覆盖 DeviceInstanceService._resolve_eval_binding_id 和
DeviceServiceRouter.get_device_connection_by_bot 的 default_tag 参数。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_instance_service import (
    DeviceInstanceService,
    EvalBindingNotFoundError,
)


def _make_binding(
    *,
    binding_id: int = 1,
    entity_id: str = "staff-001",
    status: str = "ACTIVE",
    device_id: str = "DEVICE-001",
    device_provider: str = "baas",
    device_props: dict | None = None,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=binding_id,
        entity_id=entity_id,
        entity_type="staff",
        device_id=device_id,
        device_provider=device_provider,
        status=status,
        env="dev",
        device_props=device_props or {},
        apply_reason="test",
        applied_by="u1",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )


def _make_instance_service(
    *,
    bot_repo=None,
    binding_repo=None,
) -> DeviceInstanceService:
    publish_repo = MagicMock()
    providers = {}
    return DeviceInstanceService(
        repository=binding_repo or MagicMock(),
        providers=providers,
        publish_repo=publish_repo,
        bot_repo=bot_repo or MagicMock(),
    )


class TestResolveEvalBindingId:
    """_resolve_eval_binding_id 测试。"""

    def test_exact_default_tag_match(self):
        """精确匹配 default_tag。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "u1", "owner_id": "u1",
        }
        b1 = _make_binding(
            binding_id=10,
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-1"},
        )
        b2 = _make_binding(
            binding_id=20,
            device_props={"AGENTCLAW_DEFAULT_TAG": "eval-v2", "bot_id": "bot-1"},
        )
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (2, [b1, b2])
        svc = _make_instance_service(bot_repo=bot_repo, binding_repo=binding_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            result = svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="default")
        assert result == 10

    def test_fallback_when_no_exact_match(self):
        """无精确匹配时回退到第一个评测沙箱 binding。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "u1", "owner_id": "u1",
        }
        b1 = _make_binding(
            binding_id=20,
            device_props={"AGENTCLAW_DEFAULT_TAG": "eval-v2", "bot_id": "bot-1"},
        )
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (1, [b1])
        svc = _make_instance_service(bot_repo=bot_repo, binding_repo=binding_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            result = svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="nonexistent")
        assert result == 20

    def test_no_eval_binding_raises(self):
        """无评测沙箱 binding 时抛出 EvalBindingNotFoundError。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "u1", "owner_id": "u1",
        }
        b_prod = _make_binding(binding_id=1, device_props={})
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (1, [b_prod])
        svc = _make_instance_service(bot_repo=bot_repo, binding_repo=binding_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            with pytest.raises(EvalBindingNotFoundError):
                svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="default")

    def test_bot_not_found_raises(self):
        """bot 不存在时抛出 EvalBindingNotFoundError。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = None
        svc = _make_instance_service(bot_repo=bot_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            with pytest.raises(EvalBindingNotFoundError, match="Bot not found"):
                svc._resolve_eval_binding_id(bot_id="bot-missing", default_tag="default")

    def test_no_bot_repo_raises(self):
        """无 BotRepository 时抛出 EvalBindingNotFoundError。"""
        svc = _make_instance_service(bot_repo=None)
        svc._bot_repo = None
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            with pytest.raises(EvalBindingNotFoundError, match="BotRepository not available"):
                svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="default")

    def test_filters_released_bindings(self):
        """RELEASED 状态 binding 被过滤。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "u1", "owner_id": "u1",
        }
        b_active = _make_binding(
            binding_id=10, status="ACTIVE",
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-1"},
        )
        b_released = _make_binding(
            binding_id=20, status="RELEASED",
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-1"},
        )
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (2, [b_active, b_released])
        svc = _make_instance_service(bot_repo=bot_repo, binding_repo=binding_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            result = svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="default")
        assert result == 10

    def test_filters_by_bot_id_in_device_props(self):
        """device_props 中 bot_id 不匹配的 binding 被过滤。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "u1", "owner_id": "u1",
        }
        b_other = _make_binding(
            binding_id=10,
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-2"},
        )
        b_match = _make_binding(
            binding_id=20,
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-1"},
        )
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (2, [b_other, b_match])
        svc = _make_instance_service(bot_repo=bot_repo, binding_repo=binding_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            result = svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="default")
        assert result == 20

    def test_entity_id_diff_owner_id_queries_both(self):
        """entity_id != owner_id 时查询两个 ID 合并去重。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "entity-1", "owner_id": "owner-1",
        }
        b1 = _make_binding(
            binding_id=10, entity_id="owner-1",
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-1"},
        )
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (1, [b1])
        svc = _make_instance_service(bot_repo=bot_repo, binding_repo=binding_repo)
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            result = svc._resolve_eval_binding_id(bot_id="bot-1", default_tag="default")
        assert result == 10
        # 应该查询两个 entity_id
        assert binding_repo.list_bindings.call_count == 2


class TestRouterGetConnectionByBotWithDefaultTag:
    """DeviceServiceRouter.get_device_connection_by_bot default_tag 参数测试。"""

    def test_default_tag_routes_to_eval_binding(self):
        """指定 default_tag 时走 eval binding 解析路径。"""
        from agentclaw.community.core.devices.services.device_service_router import (
            DeviceServiceRouter,
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {
            "entity_id": "u1", "owner_id": "u1",
        }
        b_eval = _make_binding(
            binding_id=99, device_provider="baas",
            device_props={"AGENTCLAW_DEFAULT_TAG": "default", "bot_id": "bot-1"},
        )
        binding_repo = MagicMock()
        binding_repo.list_bindings.return_value = (1, [b_eval])
        # 设置 get_by_id 让 _get_provider_for_binding 能路由
        binding_repo.get_by_id.return_value = b_eval
        default_svc = MagicMock()
        default_svc.get_device_connection.return_value = MagicMock()
        router = DeviceServiceRouter(
            repository=binding_repo,
            bot_query=MagicMock(),
            providers={"baas": default_svc},
            default_provider_key="baas",
            publish_repo=MagicMock(),
            bot_repo=bot_repo,
        )
        operator = OperatorContext(staff_id="u1", staff="u1", nick_name="Test", operator_name="u1", tenant_id="default")
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            router.get_device_connection_by_bot(
                bot_id="bot-1", operator=operator, default_tag="default",
            )
        # 应该调用 eval binding 的 binding_id (99)
        default_svc.get_device_connection.assert_called_once()
        call_kwargs = default_svc.get_device_connection.call_args
        assert call_kwargs.kwargs["binding_id"] == 99

    def test_no_default_tag_routes_to_production(self):
        """不指定 default_tag 时走生产 binding 解析路径。"""
        from agentclaw.community.core.devices.services.device_service_router import (
            DeviceServiceRouter,
        )
        publish_repo = MagicMock()
        mock_record = MagicMock()
        mock_record.ext = {"binding": {"online": 50}}
        publish_repo.get_latest_success_by_source_bot_id.return_value = mock_record
        default_svc = MagicMock()
        default_svc.get_device_connection.return_value = MagicMock()
        binding_repo = MagicMock()
        binding_repo.get_by_id.return_value = _make_binding(binding_id=50, device_props={})
        router = DeviceServiceRouter(
            repository=binding_repo,
            bot_query=MagicMock(),
            providers={"baas": default_svc},
            default_provider_key="baas",
            publish_repo=publish_repo,
            bot_repo=MagicMock(),
        )
        operator = OperatorContext(staff_id="u1", staff="u1", nick_name="Test", operator_name="u1", tenant_id="default")
        with patch("agentclaw.community.core.devices.services.device_instance_service.env_utils.get_current_env", return_value="dev"):
            router.get_device_connection_by_bot(
                bot_id="bot-1", operator=operator,
            )
        default_svc.get_device_connection.assert_called_once()
        call_kwargs = default_svc.get_device_connection.call_args
        assert call_kwargs.kwargs["binding_id"] == 50
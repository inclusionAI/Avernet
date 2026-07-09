"""Unit tests for LocalDeviceAccessor.get_connection_info (plan-03 Part B).

Previously returned None always; now resolves binding → BaaS http_info →
returns dict shaped for ProdDeviceMCPSyncPlugin._get_base_url consumption.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.service_bot.services.baas_service import HttpConnectionInfo


def _make_binding(**overrides) -> DeviceBindingRecord:
    defaults = dict(
        id=42,
        entity_id="staff_u001",
        entity_type="staff",
        device_id="bot-uuid-001",
        device_provider="local",
        env="dev",
        device_props={"adapter_port": 20010, "tenant": "team_claw"},
        status="ACTIVE",
        apply_reason=None,
        applied_by="staff_u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )
    defaults.update(overrides)
    return DeviceBindingRecord(**defaults)


def _make_plugin(
    bot_repo=None, binding_repo=None, baas_service=None
):
    from agentclaw.community.core.devices.services.local_device_accessor import LocalDeviceAccessor

    return LocalDeviceAccessor(
        path_factory=MagicMock(),
        bot_repository=bot_repo or MagicMock(),
        binding_repo=binding_repo or MagicMock(),
        baas_service=baas_service or MagicMock(),
    )


def test_ctor_accepts_baas_service():
    plugin = _make_plugin()
    assert plugin is not None


def test_get_connection_info_happy_returns_full_dict():
    """有 bot+binding+adapter_port+成功 get_http_info → 返带 url/token 的 dict。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": 42}

    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _make_binding()

    baas_service = MagicMock()
    baas_service.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010",
        token="abc-token",
    )

    plugin = _make_plugin(
        bot_repo=bot_repo, binding_repo=binding_repo, baas_service=baas_service
    )

    info = plugin.get_connection_info("bot-001", "staff_u001")
    assert info is not None
    assert info["url"] == "http://10.0.0.1:20010"
    assert info["token"] == "abc-token"
    # ProdDeviceMCPSyncPlugin._get_base_url 不走 baas 分支因为 device_provider != "baas"
    # 它走 `return conn_info["url"]` 路径——所以 device_provider 设 "local" 即可
    assert info["device_provider"] == "local"
    # 仍带 headers (openclawToken) 方便容器侧 token 校验
    assert info["headers"]["openclawToken"] == "abc-token"

    baas_service.get_http_info.assert_called_once()
    call_kwargs = baas_service.get_http_info.call_args.kwargs
    assert call_kwargs["bind_id"] == 42
    assert call_kwargs["port"] == 20010
    # path 给个标识性的，方便审计 BaaS 端日志
    assert call_kwargs["path"] == "/api/mcp"


def test_get_connection_info_no_bot_returns_none():
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = None
    plugin = _make_plugin(bot_repo=bot_repo)
    assert plugin.get_connection_info("bot-no-binding", "staff_u001") is None


def test_get_connection_info_no_binding_returns_none():
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": None}
    plugin = _make_plugin(bot_repo=bot_repo)
    assert plugin.get_connection_info("bot-x", "staff_u001") is None


def test_get_connection_info_missing_adapter_port_returns_none():
    """没 adapter_port 视作非 BaaS-managed → 返 None（与 dispatcher 一致）。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": 42}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _make_binding(device_props={})
    plugin = _make_plugin(bot_repo=bot_repo, binding_repo=binding_repo)
    assert plugin.get_connection_info("bot-x", "staff_u001") is None


def test_get_connection_info_baas_failure_returns_none():
    """BaaS get_http_info 失败 → 返 None（caller 看到 None = skip sync, 不炸）。"""
    from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": 42}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _make_binding()
    baas_service = MagicMock()
    baas_service.get_http_info.side_effect = BaasServiceError("baas down")
    plugin = _make_plugin(
        bot_repo=bot_repo, binding_repo=binding_repo, baas_service=baas_service
    )
    assert plugin.get_connection_info("bot-x", "staff_u001") is None


def test_get_connection_info_bot_repo_exception_returns_none():
    """bot_repo lookup raising degrades to None (defensive, no propagation)."""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.side_effect = RuntimeError("db down")
    plugin = _make_plugin(bot_repo=bot_repo)
    assert plugin.get_connection_info("bot-x", "staff_u001") is None


def test_get_connection_info_binding_repo_exception_returns_none():
    """binding_repo lookup raising degrades to None."""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": 42}
    binding_repo = MagicMock()
    binding_repo.get_by_id.side_effect = RuntimeError("db down")
    plugin = _make_plugin(bot_repo=bot_repo, binding_repo=binding_repo)
    assert plugin.get_connection_info("bot-x", "staff_u001") is None


def test_get_connection_info_binding_missing_returns_none():
    """A binding_id that resolves to no row → None."""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"binding_id": 42}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = None
    plugin = _make_plugin(bot_repo=bot_repo, binding_repo=binding_repo)
    assert plugin.get_connection_info("bot-x", "staff_u001") is None

"""BaasDeviceAccessor -- 独立 DeviceAccessor 实现，只服务 BAAS device binding。"""
from unittest.mock import MagicMock, patch
import pytest


def _make_plugin(bot_service, baas_service=None):
    """Construct a BaasDeviceAccessor with lazy callable deps."""
    from agentclaw.community.core.devices.services.baas_device_accessor import BaasDeviceAccessor

    return BaasDeviceAccessor(
        bot_service_provider=lambda: bot_service,
        baas_service_provider=lambda: baas_service or MagicMock(),
        path_factory=MagicMock(),
        sandbox_client=MagicMock(),
    )


def test_baas_device_plugin_returns_none_for_non_baas_binding():
    fake_bot_service = MagicMock()
    fake_bot_service.get_bot.return_value = {
        "device_binding": {
            "id": 42,
            "device_provider": "arca",  # not baas
        }
    }

    plugin = _make_plugin(fake_bot_service)
    result = plugin.get_connection_info("bot-1", "user-1")
    assert result is None, "BaasDeviceAccessor must refuse non-baas bindings"


def test_baas_device_plugin_returns_none_when_no_binding():
    fake_bot_service = MagicMock()
    fake_bot_service.get_bot.return_value = {"device_binding": None}

    plugin = _make_plugin(fake_bot_service)
    assert plugin.get_connection_info("bot-1", "user-1") is None


def test_baas_device_plugin_builds_conn_info_via_baas_service():
    """Happy path: baas binding → BaasService.get_ws_info → build_baas_conn_info."""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_device_accessor import BaasDeviceAccessor

    fake_bot_service = MagicMock()
    fake_bot_service.get_bot.return_value = {
        "device_binding": {
            "id": 99,
            "device_provider": "baas",
        }
    }

    fake_ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="container_abc--machine_M1--user_U7@tpl_42",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
    )

    fake_baas_service = MagicMock()
    fake_baas_service.get_ws_info.return_value = fake_ws_info

    plugin = BaasDeviceAccessor(
        bot_service_provider=lambda: fake_bot_service,
        baas_service_provider=lambda: fake_baas_service,
        path_factory=MagicMock(),
        sandbox_client=MagicMock(),
    )

    conn_info = plugin.get_connection_info("bot-1", "user-1")

    assert conn_info is not None
    # device_provider 由 ctx.provider 单源承载(resolver 在 wrap 层 pop 该字段),
    # plugin 不再向 raw conn_info 写 device_provider。详见 baas_conn_info.py docstring。
    assert "device_provider" not in conn_info
    assert conn_info["paas_device_id"] == "container_abc--machine_M1--user_U7@tpl_42"
    assert conn_info["baas_base_url"] == "https://secbaas-prod.teamclaw.com"
    assert conn_info["engine_port"] == 20003

    # And it called BaasService.get_ws_info with binding id + user as device_affinity
    fake_baas_service.get_ws_info.assert_called_once_with(
        bind_id=99,
        device_affinity="user-1",
    )

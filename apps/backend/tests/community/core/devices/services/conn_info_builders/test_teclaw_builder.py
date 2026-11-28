from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError
from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TeclawConnInfoBuilder,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BotWsConnectionInfoResponse,
)


@pytest.fixture
def fake_binding():
    binding = MagicMock()
    binding.id = 42
    binding.bot_id = "bot-teclaw-1"
    binding.device_provider = "teclaw"
    binding.bot_type = "personal"
    return binding


@pytest.fixture
def fake_baas_service():
    svc = MagicMock()
    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://proxy.test/proxypass/TECLAW-target@42:20003/api/openclaw/ws",
        token="tok-teclaw",
        target="TECLAW-target@42:20003",
        expires_at="2026-12-31T00:00:00Z",
        paas_device_id="BOT-teclaw-device-id",
        baas_base_url="https://baas.test",
        engine_port=20003,
        tenant="team_claw",
        bot_uuid="BOT-teclaw-device-id",
    )
    svc.get_ws_info.return_value = ws_info
    return svc


def test_build_returns_teclaw_engine_type(fake_binding, fake_baas_service):
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    conn_info = builder.build(fake_binding, user_id="user-1")

    # teclaw provider 的关键标识(沿用 build_baas_conn_info(engine_type="teclaw"))
    assert conn_info.get("engine_type") == "teclaw"


def test_build_uses_teclaw_proxypass_http_base(fake_binding, fake_baas_service):
    """TeClaw direct flows use the existing BaaS proxy base, not /invoke-http."""
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["url"] == (
        "https://proxy.test/proxypass/TECLAW-target@42:20003"
    )
    assert "/invoke-http/" not in conn_info["url"]
    # The *_for_http contract stays available for TeclawDeviceFileSystem and
    # other bind_id consumers. Only the direct URL base is provider-specific.
    assert conn_info["bind_id"] == 42
    assert conn_info["type"] == "baas"
    assert conn_info["headers"] == {"x-proxypass-token": "tok-teclaw"}
    assert conn_info["use_proxy"] is True
    assert conn_info["sandbox_id"] is None

def test_build_uses_baas_get_ws_info(fake_binding, fake_baas_service):
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    builder.build(fake_binding, user_id="user-1")

    fake_baas_service.get_ws_info.assert_called_once()


def test_build_raises_conn_info_build_error(fake_binding, fake_baas_service):
    fake_baas_service.get_ws_info.side_effect = Exception("baas down")
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    with pytest.raises(ConnInfoBuildError):
        builder.build(fake_binding, user_id="user-1")

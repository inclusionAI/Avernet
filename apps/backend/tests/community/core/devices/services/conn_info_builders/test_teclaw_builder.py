from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError
from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TeclawConnInfoBuilder,
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
    ws_info = MagicMock()
    ws_info.bot_uuid = "uuid-1"
    ws_info.baas_base_url = "https://baas.test"
    ws_info.engine_port = 20003
    ws_info.tenant = "team_claw"
    ws_info.headers = {}
    # teclaw shares the BaaS invoke-http transport; its target is never an
    # ``ARCA_`` sandbox id, so the conn-info builder takes the invoke-http branch
    # (no sandbox_client needed). A bare MagicMock target would falsely satisfy
    # ``startswith("ARCA_")``, so pin a realistic non-ARCA value.
    ws_info.target = "BOT-teclaw-device-id"
    svc.get_ws_info.return_value = ws_info
    return svc


def test_build_returns_teclaw_engine_type(fake_binding, fake_baas_service):
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    conn_info = builder.build(fake_binding, user_id="user-1")

    # teclaw provider 的关键标识(沿用 build_baas_conn_info(engine_type="teclaw"))
    assert conn_info.get("engine_type") == "teclaw"


def test_build_uses_baas_get_ws_info(fake_binding, fake_baas_service):
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    builder.build(fake_binding, user_id="user-1")

    fake_baas_service.get_ws_info.assert_called_once()


def test_build_raises_conn_info_build_error(fake_binding, fake_baas_service):
    fake_baas_service.get_ws_info.side_effect = Exception("baas down")
    builder = TeclawConnInfoBuilder(baas_service=fake_baas_service)

    with pytest.raises(ConnInfoBuildError):
        builder.build(fake_binding, user_id="user-1")

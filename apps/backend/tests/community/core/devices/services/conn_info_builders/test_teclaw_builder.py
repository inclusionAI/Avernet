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
    # teclaw target always starts with TECLAW_; conn_info branch uses proxy URL
    # ``ARCA_`` sandbox id
    # (no sandbox_client needed)
    # pin a realistic TECLAW_ target to match production format.
    ws_info.target = "TECLAW_b_01M6FZ9Q767PPNW7YDR7HYASCQ@4:20003"
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

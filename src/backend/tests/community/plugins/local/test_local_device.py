"""Unit tests for LocalDeviceAccessor."""
from unittest.mock import MagicMock
import pytest

from agentclaw.community.core.devices.services.local_device_accessor import LocalDeviceAccessor


@pytest.fixture
def device():
    # Constructor deps; only get_engine_config_path / get_connection_info use
    # them — the get_connection_info empty-input cases under test here ignore
    # the constructor args.
    return LocalDeviceAccessor(
        path_factory=MagicMock(),
        bot_repository=MagicMock(),
        binding_repo=MagicMock(),
        baas_service=MagicMock(),
    )


class TestGetConnectionInfo:
    def test_returns_none(self, device):
        assert device.get_connection_info("bot-1", "user-1") is None

    def test_returns_none_for_empty_input(self, device):
        assert device.get_connection_info("", "") is None


def test_get_engine_config_path_uses_path_factory():
    path_factory = MagicMock()
    path_factory.get_bot_engine_dir.return_value = "/bots/entity/b1/openclaw"
    dev = LocalDeviceAccessor(
        path_factory=path_factory,
        bot_repository=MagicMock(),
        binding_repo=MagicMock(),
        baas_service=MagicMock(),
    )

    result = dev.get_engine_config_path(
        "b1", "owner", entity_id="entity", engine_type="openclaw"
    )

    assert result == "/bots/entity/b1/openclaw/openclaw.json"
    path_factory.get_bot_engine_dir.assert_called_once_with(
        "entity", "b1", "openclaw", "staff"
    )

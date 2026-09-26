"""Unit tests for the neutral device-provider lookup helpers."""
from unittest.mock import Mock

from agentclaw.community.core.devices.services import device_info


class TestGetDeviceInfo:
    def test_blank_inputs(self):
        repo = Mock()
        assert device_info.get_device_info("", "owner", repo) == (None, None)
        assert device_info.get_device_info("bot", "", repo) == (None, None)

    def test_success(self):
        repo = Mock()
        repo.get_device_provider_by_bot_id_and_owner.return_value = {
            "device_provider": "arca",
            "sandbox_id": "abc",
        }
        assert device_info.get_device_info("bot", "owner", repo) == ("arca", "abc")

    def test_missing_record(self):
        repo = Mock()
        repo.get_device_provider_by_bot_id_and_owner.return_value = None
        assert device_info.get_device_info("bot", "owner", repo) == (None, None)

    def test_exception_swallowed(self):
        repo = Mock()
        repo.get_device_provider_by_bot_id_and_owner.side_effect = RuntimeError("boom")
        assert device_info.get_device_info("bot", "owner", repo) == (None, None)


class TestGetDeviceInfoByBotId:
    def test_found(self):
        repo = type("R", (), {})()
        repo.get_device_provider_by_bot_id = lambda bot_id: {
            "device_provider": "arca",
            "sandbox_id": "x",
        }
        assert device_info.get_device_info_by_bot_id("b1", repo) == ("arca", "x")

    def test_blank_short_circuits(self):
        assert device_info.get_device_info_by_bot_id("", object()) == (None, None)

    def test_missing_record(self):
        repo = type("R", (), {"get_device_provider_by_bot_id": lambda self, bot_id: None})()
        assert device_info.get_device_info_by_bot_id("b1", repo) == (None, None)

    def test_exception_swallowed(self):
        def _boom(bot_id):
            raise RuntimeError("db")

        repo = type("R", (), {})()
        repo.get_device_provider_by_bot_id = _boom
        assert device_info.get_device_info_by_bot_id("b1", repo) == (None, None)

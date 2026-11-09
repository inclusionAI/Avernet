"""Provider receipts retain exact success semantics during Passport refresh."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.errors import DeviceServiceError
from tests.community.services.test_bot_passport import (
    TestBotServicePassportIntegration as PassportFixtures,
)


class TestPassportRuntimeAcknowledgement(PassportFixtures):
    @staticmethod
    def device_service(bot_service, provider, result):
        service = MagicMock()
        service.get_device.return_value = SimpleNamespace(
            id=20,
            device_id="draft-device",
            device_provider=provider,
            device_props={"sandbox_id": "ARCA-SANDBOX-test@0"},
        )
        service.update_device_headers.return_value = result
        bot_service._device_service_provider = lambda: service
        return service

    @pytest.mark.parametrize(
        ("provider", "receipt", "count"),
        [
            ("arca", True, 1),
            ("arca", False, 0),
            ("arca", None, 0),
            ("arca", 1, 0),
            ("arca", "true", 0),
            ("arca", {}, 0),
            ("arca", [], 0),
            ("baas", True, 0),
            ("teclaw", True, 0),
            ("baas", [], 0),
            ("teclaw", [], 0),
        ],
    )
    def test_only_explicit_arca_success_counts_as_one_target(
        self, bot_service, provider, receipt, count
    ):
        service = self.device_service(bot_service, provider, receipt)
        result = bot_service._hot_update_by_device_binding(
            "bot", "owner", "test-token", 20
        )
        assert result["device_count"] == count
        assert result["devices"] == []  # No physical-device inventory is invented.
        service.update_device_headers.assert_called_once()

    def test_baas_reported_devices_are_preserved(self, bot_service):
        devices = [{"device_uuid": "DEVICE-one"}, {"device_uuid": "DEVICE-two"}]
        self.device_service(bot_service, "baas", devices)
        result = bot_service._hot_update_by_device_binding(
            "bot", "owner", "test-token", 20
        )
        assert result["devices"] is devices
        assert result["device_count"] == 2

    def test_provider_exception_is_not_an_acknowledgement(self, bot_service):
        service = self.device_service(bot_service, "arca", True)
        service.update_device_headers.side_effect = DeviceServiceError(
            "update rejected"
        )
        with pytest.raises(DeviceServiceError, match="update rejected"):
            bot_service._hot_update_by_device_binding("bot", "owner", "test-token", 20)

    @pytest.mark.parametrize("bot_type", ["personal", "service"])
    def test_refresh_keeps_arca_ack_in_the_public_binding_result(
        self, bot_service, bot_type
    ):
        service = self.device_service(bot_service, "arca", True)
        bot_service._repository.get_by_id_and_owner.return_value = {
            "id": 1,
            "bot_id": "bot",
            "owner_id": "owner",
            "entity_id": "owner",
            "bot_type": bot_type,
            "binding_id": 20,
            "ext": {"passport": {"agent_code": "agent-code"}},
        }
        bot_service._bot_publish_repo.get_by_publish_bot_id.return_value = None
        bot_service._device_binding_repo.list_active_caller_instance_bindings.return_value = []
        result = bot_service.hot_update_passport_token_to_device(
            "bot", "owner", "test-token"
        )
        assert result["bindings"] == [
            {
                "binding_id": 20,
                "type": "draft" if bot_type == "service" else "personal",
                "device_count": 1,
                "devices": [],
            }
        ]
        assert (
            service.update_device_headers.call_args.kwargs["agent_pass_token"]
            == "test-token"
        )

"""Structural protocol conformance tests for BotManageService protocol.

Verifies that BotManageService Protocol correctly defines all expected
methods with proper signatures.
"""

from typing import Protocol

from secbaas.api.bot_manage import (
    BotListResponse,
    BotManageService,
    BotResponse,
    CreateBotResponse,
    DestroyBotResponse,
    RestartBotResponse,
    ScaleBotResponse,
    UpdateBotResponse,
)
from secbaas.api.device_manage import DeviceListResponse
from secbaas.core.service.bot_session import BotSession


class TestBotManageServiceProtocol:
    """Tests for the BotManageService Protocol definition."""

    def test_is_protocol(self):
        """THEN BotManageService is a Protocol class."""
        assert issubclass(BotManageService, Protocol)

    def test_all_methods_defined(self):
        """THEN Protocol defines all expected lifecycle methods."""
        expected = {
            "create_bot",
            "destroy_bot",
            "get_bot",
            "list_bots",
            "scale_bot",
            "update_bot",
            "restart_bot",
            "get_bot_with_devices",
            "list_bots_with_devices",
            "list_devices_by_bot_uuid",
            "list_devices_by_bot_id",
        }
        protocol_methods = {
            name for name in dir(BotManageService) if not name.startswith("_")
        }
        for method in expected:
            assert method in protocol_methods, f"Missing method: {method}"

    def test_create_bot_returns_create_bot_response(self):
        """THEN create_bot returns CreateBotResponse."""
        hints = __import__("typing").get_type_hints(BotManageService.create_bot)
        assert hints["return"] == CreateBotResponse

    def test_get_bot_returns_optional_bot_response(self):
        """THEN get_bot returns BotResponse | None."""
        hints = __import__("typing").get_type_hints(BotManageService.get_bot)
        assert hints["return"] == BotResponse | None

    def test_list_bots_returns_list_response(self):
        """THEN list_bots returns BotListResponse."""
        hints = __import__("typing").get_type_hints(BotManageService.list_bots)
        assert hints["return"] == BotListResponse

    def test_destroy_bot_returns_optional_destroy_response(self):
        """THEN destroy_bot returns DestroyBotResponse | None."""
        hints = __import__("typing").get_type_hints(BotManageService.destroy_bot)
        assert hints["return"] == DestroyBotResponse | None

    def test_scale_bot_returns_scale_response(self):
        """THEN scale_bot returns ScaleBotResponse."""
        hints = __import__("typing").get_type_hints(BotManageService.scale_bot)
        assert hints["return"] == ScaleBotResponse

    def test_update_bot_returns_update_response(self):
        """THEN update_bot returns UpdateBotResponse."""
        hints = __import__("typing").get_type_hints(BotManageService.update_bot)
        assert hints["return"] == UpdateBotResponse

    def test_restart_bot_returns_restart_response(self):
        """THEN restart_bot returns RestartBotResponse."""
        hints = __import__("typing").get_type_hints(BotManageService.restart_bot)
        assert hints["return"] == RestartBotResponse

    def test_restart_bot_auto_approve_param_default(self):
        """THEN restart_bot accepts auto_approve_publish parameter with default False."""
        import inspect

        sig = inspect.signature(BotManageService.restart_bot)
        param = sig.parameters.get("auto_approve_publish")
        assert param is not None, (
            "restart_bot is missing auto_approve_publish parameter"
        )
        assert param.default is False, (
            f"Expected auto_approve_publish default to be False, got {param.default}"
        )
        assert str(param.annotation) == "bool", (
            f"Expected auto_approve_publish type to be bool, got {param.annotation}"
        )

    def test_restart_bot_scope_param_default(self):
        """THEN restart_bot accepts scope parameter with default RestartScope.ALL."""
        import inspect

        from secbaas.api.publish_manage import RestartScope

        sig = inspect.signature(BotManageService.restart_bot)
        param = sig.parameters.get("scope")
        assert param is not None
        assert param.default == RestartScope.ALL

    def test_get_bot_with_devices_returns_optional_response(self):
        """THEN get_bot_with_devices returns BotResponse | None."""
        hints = __import__("typing").get_type_hints(
            BotManageService.get_bot_with_devices
        )
        assert hints["return"] == BotResponse | None

    def test_list_bots_with_devices_returns_list_response(self):
        """THEN list_bots_with_devices returns BotListResponse."""
        hints = __import__("typing").get_type_hints(
            BotManageService.list_bots_with_devices
        )
        assert hints["return"] == BotListResponse

    def test_list_devices_by_bot_uuid_returns_device_list(self):
        """THEN list_devices_by_bot_uuid returns list[DeviceListResponse]."""
        hints = __import__("typing").get_type_hints(
            BotManageService.list_devices_by_bot_uuid
        )
        assert hints["return"] == list[DeviceListResponse]

    def test_list_devices_by_bot_id_returns_device_response(self):
        """THEN list_devices_by_bot_id returns DeviceListResponse."""
        hints = __import__("typing").get_type_hints(
            BotManageService.list_devices_by_bot_id
        )
        assert hints["return"] == DeviceListResponse

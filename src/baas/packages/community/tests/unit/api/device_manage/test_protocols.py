"""Structural protocol conformance tests for DeviceService protocol.

Verifies that DeviceService Protocol correctly defines all expected
methods with proper signatures.
"""

from typing import Protocol, get_type_hints

from secbaas.api.device_manage import (
    DestroyDeviceResponse,
    DeviceResponse,
    DeviceService,
)


class TestDeviceServiceProtocol:
    """Tests for the DeviceService Protocol definition."""

    def test_is_protocol(self):
        """THEN DeviceService is a Protocol class."""
        assert issubclass(DeviceService, Protocol)

    def test_is_runtime_checkable(self):
        """THEN DeviceService is runtime_checkable (has __instancecheck__)."""
        assert hasattr(type(DeviceService), "__instancecheck__")

    def test_all_methods_defined(self):
        """THEN Protocol defines all expected lifecycle methods."""
        expected = {
            "create_device",
            "start_device",
            "restart_device",
            "update_device",
            "destroy_device_by_uuid",
            "stop_device_by_uuid",
            "get_device_info",
        }
        protocol_methods = {
            name
            for name, value in DeviceService.__dict__.items()
            if not name.startswith("_")
        }
        assert protocol_methods == expected

    def test_create_device_signature(self):
        """THEN create_device returns DeviceResponse."""
        hints = get_type_hints(DeviceService.create_device)
        assert hints["return"] == DeviceResponse

    def test_start_device_signature(self):
        """THEN start_device returns DeviceResponse."""
        hints = get_type_hints(DeviceService.start_device)
        assert hints["return"] == DeviceResponse

    def test_restart_device_signature(self):
        """THEN restart_device returns DeviceResponse."""
        hints = get_type_hints(DeviceService.restart_device)
        assert hints["return"] == DeviceResponse

    def test_destroy_device_by_uuid_signature(self):
        """THEN destroy_device_by_uuid returns DestroyDeviceResponse."""
        hints = get_type_hints(DeviceService.destroy_device_by_uuid)
        assert hints["return"] == DestroyDeviceResponse

    def test_stop_device_by_uuid_signature(self):
        """THEN stop_device_by_uuid returns DestroyDeviceResponse."""
        hints = get_type_hints(DeviceService.stop_device_by_uuid)
        assert hints["return"] == DestroyDeviceResponse

    def test_get_device_info_signature(self):
        """THEN get_device_info returns DeviceResponse | None."""
        hints = get_type_hints(DeviceService.get_device_info)
        assert hints["return"] == DeviceResponse | None

"""Unit tests for DeviceStatus enum."""

from secbaas.community.api.device_manage import DeviceStatus


class TestDeviceStatus:
    """Test DeviceStatus enum values and properties."""

    def test_members(self):
        assert DeviceStatus.PENDING == "PENDING"
        assert DeviceStatus.ACTIVE == "ACTIVE"
        assert DeviceStatus.UPDATING == "UPDATING"
        assert DeviceStatus.RELEASED == "RELEASED"
        assert DeviceStatus.STOPPED == "STOPPED"
        assert DeviceStatus.FAILED == "FAILED"
        assert DeviceStatus.OFFLINE == "OFFLINE"

    def test_is_str_enum(self):
        assert isinstance(DeviceStatus.ACTIVE, str)

    def test_iteration(self):
        members = list(DeviceStatus)
        assert members == [
            DeviceStatus.PENDING,
            DeviceStatus.ACTIVE,
            DeviceStatus.UPDATING,
            DeviceStatus.RELEASED,
            DeviceStatus.STOPPED,
            DeviceStatus.FAILED,
            DeviceStatus.OFFLINE,
        ]

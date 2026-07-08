"""Tests for device domain models."""

import pytest

from secbaas.api.device_manage import (
    DeployConfig,
    DeviceConfig,
    DeviceStatus,
    MountPermission,
    MountPoint,
)


class TestDeployConfig:
    """Test DeployConfig lifecycle hook configuration model."""

    def test_deploy_config_defaults(self):
        """Test DeployConfig with all defaults."""
        config = DeployConfig()
        assert config.after_create_cmd_hook is None
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_cmd_hook is None
        assert config.before_destroy_hook_wait_seconds == 300

    def test_deploy_config_with_hooks(self):
        """Test DeployConfig with custom hook values."""
        config = DeployConfig(
            after_create_cmd_hook="echo 'starting' && ./start.sh",
            after_create_hook_wait_seconds=600,
            before_destroy_cmd_hook="./cleanup.sh",
            before_destroy_hook_wait_seconds=120,
        )
        assert config.after_create_cmd_hook == "echo 'starting' && ./start.sh"
        assert config.after_create_hook_wait_seconds == 600
        assert config.before_destroy_cmd_hook == "./cleanup.sh"
        assert config.before_destroy_hook_wait_seconds == 120

    def test_deploy_config_partial_hooks(self):
        """Test DeployConfig with only after_create hook set."""
        config = DeployConfig(after_create_cmd_hook="./init.sh")
        assert config.after_create_cmd_hook == "./init.sh"
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_cmd_hook is None
        assert config.before_destroy_hook_wait_seconds == 300

    def test_deploy_config_zero_wait(self):
        """Test DeployConfig allows zero wait seconds."""
        config = DeployConfig(
            after_create_hook_wait_seconds=0,
            before_destroy_hook_wait_seconds=0,
        )
        assert config.after_create_hook_wait_seconds == 0
        assert config.before_destroy_hook_wait_seconds == 0

    def test_deploy_config_negative_wait_rejected(self):
        """Test DeployConfig rejects negative wait seconds."""
        with pytest.raises(ValueError):
            DeployConfig(after_create_hook_wait_seconds=-1)


class TestDeviceConfig:
    """Test DeviceConfig model."""

    def test_device_config_defaults(self):
        """Test DeviceConfig with default values."""
        config = DeviceConfig()
        assert config.template_uuid is None
        assert config.deploy_config is None

    def test_device_config_with_deploy_config(self):
        """Test DeviceConfig with DeployConfig."""
        deploy = DeployConfig(after_create_cmd_hook="./setup.sh")
        config = DeviceConfig(template_uuid="TEMPLATE-abc123", deploy_config=deploy)
        assert config.template_uuid == "TEMPLATE-abc123"
        assert config.deploy_config is not None
        assert config.deploy_config.after_create_cmd_hook == "./setup.sh"


class TestDeviceStatus:
    """Test DeviceStatus enum values."""

    def test_status_values(self):
        """Test DeviceStatus enum has expected values."""
        assert DeviceStatus.PENDING == "PENDING"
        assert DeviceStatus.ACTIVE == "ACTIVE"
        assert DeviceStatus.UPDATING == "UPDATING"
        assert DeviceStatus.RELEASED == "RELEASED"
        assert DeviceStatus.FAILED == "FAILED"


class TestMountPoint:
    """Test MountPoint from Arca SDK with DeployConfig validator."""

    def test_mount_point_defaults(self):
        """Test MountPoint with required fields and default permission."""
        mp = MountPoint(id="mp-1", remote_dir="my-bucket", local_dir="/mnt/data")
        assert mp.id == "mp-1"
        assert mp.remote_dir == "my-bucket"
        assert mp.local_dir == "/mnt/data"
        assert mp.permission == MountPermission.READ_WRITE

    def test_mount_point_read_only(self):
        """Test MountPoint with READ_ONLY permission."""
        mp = MountPoint(
            id="mp-2",
            remote_dir="oss://my-bucket/path",
            local_dir="/mnt/oss",
            permission=MountPermission.READ_ONLY,
        )
        assert mp.id == "mp-2"
        assert mp.remote_dir == "oss://my-bucket/path"
        assert mp.local_dir == "/mnt/oss"
        assert mp.permission == MountPermission.READ_ONLY

    def test_mount_point_permission_enum_values(self):
        """Test MountPermission enum values."""
        assert MountPermission.READ_WRITE == "rw"
        assert MountPermission.READ_ONLY == "ro"


class TestDeployConfigWithMountPoints:
    """Test DeployConfig with mount_points field and validator."""

    def test_deploy_config_with_mount_point_objects(self):
        """Test DeployConfig with Arca MountPoint objects."""
        config = DeployConfig(
            after_create_cmd_hook="./setup.sh",
            mount_points=[
                MountPoint(id="", remote_dir="bucket1", local_dir="/mnt/data1"),
                MountPoint(
                    id="data2",
                    remote_dir="bucket2",
                    local_dir="/mnt/data2",
                    permission=MountPermission.READ_ONLY,
                ),
            ],
        )
        assert config.after_create_cmd_hook == "./setup.sh"
        assert config.mount_points is not None
        assert len(config.mount_points) == 2
        assert config.mount_points[0].remote_dir == "bucket1"
        assert config.mount_points[0].local_dir == "/mnt/data1"
        assert config.mount_points[1].permission == MountPermission.READ_ONLY

    def test_deploy_config_with_mount_point_dicts(self):
        """Test DeployConfig with dict mount_points (JSON deserialization)."""
        config = DeployConfig(
            mount_points=[
                {"id": "mp-1", "remote_dir": "bucket1", "local_dir": "/mnt/data1"},
                {
                    "id": "mp-2",
                    "remote_dir": "bucket2",
                    "local_dir": "/mnt/data2",
                    "permission": "READ_ONLY",
                },
            ],
        )
        assert config.mount_points is not None
        assert len(config.mount_points) == 2
        assert config.mount_points[0].remote_dir == "bucket1"
        assert config.mount_points[1].permission == MountPermission.READ_ONLY

    def test_deploy_config_without_mount_points(self):
        """Test DeployConfig defaults mount_points to None."""
        config = DeployConfig()
        assert config.mount_points is None

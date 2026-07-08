"""Unit tests for device info models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from secbaas.api.device_manage import (
    ArcaDeviceInfo,
    DeviceInfo,
    DockerDeviceInfo,
    LocalDeviceInfo,
    SigmaDeviceInfo,
)


class TestPlatformDeviceInfo:
    """Test base DeviceInfo from device_info module."""

    def test_required_fields(self):
        from datetime import datetime

        info = DeviceInfo(
            device_uuid="d-001",
            status="ACTIVE",
            gmt_create=datetime(2024, 1, 1, 0, 0, 0),
        )
        assert info.device_uuid == "d-001"
        assert info.status == "ACTIVE"


class TestArcaDeviceInfo:
    """Test ArcaDeviceInfo model."""

    def test_required_fields(self):
        now = datetime(2024, 1, 1, 0, 0, 0)
        info = ArcaDeviceInfo(
            platform="arca",
            status="ACTIVE",
            sandbox_id="sb-1",
            template_id="tpl-1",
            ttl_seconds=3600,
            created_at=now,
        )
        assert info.sandbox_id == "sb-1"
        assert info.template_id == "tpl-1"
        assert info.ttl_seconds == 3600
        assert info.created_at == now

    def test_optional_fields(self):
        now = datetime(2024, 1, 1, 0, 0, 0)
        info = ArcaDeviceInfo(
            platform="arca",
            status="ACTIVE",
            sandbox_id="sb-1",
            template_id="tpl-1",
            ttl_seconds=3600,
            created_at=now,
            ip_address="10.0.0.1",
            name="my-sandbox",
        )
        assert info.ip_address == "10.0.0.1"
        assert info.name == "my-sandbox"


class TestSigmaDeviceInfo:
    """Test SigmaDeviceInfo model."""

    def test_required_fields(self):
        info = SigmaDeviceInfo(platform="sigma", status="RUNNING")
        assert info.platform == "sigma"
        assert info.status == "RUNNING"


class TestLocalDeviceInfo:
    """Test LocalDeviceInfo model."""

    def test_required_fields(self):
        info = LocalDeviceInfo(
            platform="local",
            status="ACTIVE",
            container_id="c-001",
            machine_id="m-001",
            user_id="u-001",
            port=8080,
        )
        assert info.container_id == "c-001"
        assert info.machine_id == "m-001"
        assert info.user_id == "u-001"
        assert info.port == 8080


# ==================== DockerDeviceInfo ====================


class TestDockerDeviceInfo:
    """Tests for DockerDeviceInfo model."""

    def test_platform_fixed_docker(self):
        """WHEN DockerDeviceInfo constructed, THEN platform defaults to 'docker'."""
        info = DockerDeviceInfo(
            container_id="abc", host_port=80, image="alpine:latest", status="running"
        )
        assert info.platform == "docker"

    def test_host_port_range_too_low(self):
        """WHEN host_port=0, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerDeviceInfo(
                container_id="abc", host_port=0, image="alpine:latest", status="running"
            )

    def test_host_port_range_too_high(self):
        """WHEN host_port=65536, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerDeviceInfo(
                container_id="abc",
                host_port=65536,
                image="alpine:latest",
                status="running",
            )

    def test_missing_container_id_raises(self):
        """WHEN container_id is missing, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerDeviceInfo(host_port=80, image="alpine", status="running")

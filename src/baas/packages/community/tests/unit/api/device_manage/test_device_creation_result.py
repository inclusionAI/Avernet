"""Unit tests for device creation result models."""

import pytest
from pydantic import ValidationError

from secbaas.api.device_manage import (
    ArcaCreationResult,
    DeviceCreationResult,
    DockerCreationResult,
    LocalCreationResult,
    PoolabCreationResult,
    SigmaCreationResult,
)


class TestDeviceCreationResult:
    """Test base DeviceCreationResult."""

    def test_required_fields(self):
        r = DeviceCreationResult(platform="arca", status="PENDING")
        assert r.platform == "arca"
        assert r.status == "PENDING"


class TestArcaCreationResult:
    """Test ArcaCreationResult model."""

    def test_required_fields(self):
        r = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-1",
            sandbox_id="sb-1",
        )
        assert r.platform == "arca"
        assert r.template_id == "tpl-1"
        assert r.sandbox_id == "sb-1"

    def test_defaults(self):
        r = ArcaCreationResult(
            platform="arca",
            status="PENDING",
            template_id="tpl-1",
            sandbox_id="sb-1",
        )
        assert r.resources is None
        assert r.envs is None
        assert r.metadata is None


class TestSigmaCreationResult:
    """Test SigmaCreationResult model."""

    def test_required_fields(self):
        r = SigmaCreationResult(platform="sigma", status="ACTIVE")
        assert r.platform == "sigma"
        assert r.status == "ACTIVE"

    def test_with_fields(self):
        r = SigmaCreationResult(
            platform="sigma",
            status="ACTIVE",
            region="us-east-1",
            instance_id="i-001",
        )
        assert r.region == "us-east-1"
        assert r.instance_id == "i-001"


class TestLocalCreationResult:
    """Test LocalCreationResult model."""

    def test_required_fields(self):
        r = LocalCreationResult(
            platform="local",
            status="ACTIVE",
            container_id="c-001",
        )
        assert r.platform == "local"
        assert r.status == "ACTIVE"
        assert r.container_id == "c-001"


class TestPoolabCreationResult:
    """Test PoolabCreationResult model."""

    def test_required_fields(self):
        """PoolabCreationResult requires poolab_id and poolab_user_id."""
        r = PoolabCreationResult(
            platform="Poolab",
            status="READY",
            poolab_id="123",
            poolab_user_id="user1",
        )
        assert r.platform == "Poolab"
        assert r.status == "READY"
        assert r.poolab_id == "123"
        assert r.poolab_user_id == "user1"

    def test_defaults(self):
        """Optional fields default to None."""
        r = PoolabCreationResult(
            platform="Poolab",
            status="PENDING",
            poolab_id="456",
            poolab_user_id="user2",
        )
        assert r.poolab_hostname is None
        assert r.poolab_image_id is None
        assert r.poolab_order_id is None
        assert r.poolab_status is None
        assert r.poolab_openclaw_url is None
        assert r.poolab_openclaw_token is None
        assert r.poolab_display_status is None
        assert r.poolab_type is None
        assert r.poolab_network_type is None

    def test_with_all_fields(self):
        """All fields from Poolab API createMachine response."""
        r = PoolabCreationResult(
            platform="Poolab",
            status="READY",
            poolab_id="789",
            poolab_user_id="user3",
            poolab_user_nick="User Three",
            poolab_hostname="antclaw-abc123.inc.alipay.net",
            poolab_image_id="img-xyz",
            poolab_order_id="order-001",
            poolab_status="READY",
            poolab_openclaw_url="http://antclaw-abc123.inc.alipay.net:9999",
            poolab_openclaw_token="tok-123456",
            poolab_display_status="OPENED",
            poolab_type="OpenClaw",
            poolab_network_type="PUBLIC",
            poolab_operations_url="http://antclaw-abc123.inc.alipay.net:9999/?token=123456",
            poolab_remote_url="http://antclaw-abc123.inc.alipay.net/vnc.html",
            poolab_model_config_type="public",
            poolab_env="TEST",
        )
        assert r.poolab_id == "789"
        assert r.poolab_user_nick == "User Three"
        assert r.poolab_hostname == "antclaw-abc123.inc.alipay.net"
        assert r.poolab_openclaw_url == "http://antclaw-abc123.inc.alipay.net:9999"
        assert r.poolab_openclaw_token == "tok-123456"
        assert r.poolab_env == "TEST"


# ==================== DockerCreationResult ====================


class TestDockerCreationResult:
    """Tests for DockerCreationResult model."""

    def test_missing_container_id_raises(self):
        """WHEN container_id is missing, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreationResult(host_port=80, status="running")

    def test_host_port_range_too_low(self):
        """WHEN host_port=0, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreationResult(container_id="abc", host_port=0, status="running")

    def test_host_port_range_too_high(self):
        """WHEN host_port=65536, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreationResult(container_id="abc", host_port=65536, status="running")

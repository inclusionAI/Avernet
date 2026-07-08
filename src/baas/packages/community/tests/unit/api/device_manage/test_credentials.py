"""Unit tests for device_manage credentials, ctoken, proxy, and local_device_id."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from secbaas.api.device_manage import (
    ArcaCredentials,
    InvalidLocalDeviceIdError,
    LocalCredentials,
    LocalDeviceId,
    PaasCredentials,
    PoolabCredentials,
    ProxyExecRequest,
    ProxyHealthRequest,
    SigmaCredentials,
)


class TestPaasCredentials:
    """Test PaasCredentials base model."""

    def test_base_fields(self):
        creds = PaasCredentials(
            template_id=123, template_uuid="uuid-1", tenant_name="default"
        )
        assert creds.template_id == 123
        assert creds.template_uuid == "uuid-1"
        assert creds.tenant_name == "default"


_TEMPLATE_UUID = "tpl-uuid-1"


class TestArcaCredentials:
    """Test ArcaCredentials model."""

    def test_minimal(self):
        creds = ArcaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            base_url="https://arca.example.com",
            api_key="key-123",
            arca_template_id="tpl-1",
        )
        assert creds.timeout == 30.0
        assert creds.default_ttl_minutes == 1440
        assert creds.app_name == "secbaas"

    def test_is_configured_all_present(self):
        creds = ArcaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            base_url="https://arca.example.com",
            api_key="key-123",
            arca_template_id="tpl-1",
        )
        assert creds.is_configured() is True

    def test_is_configured_missing_base_url(self):
        creds = ArcaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            base_url=None,
            api_key="key-123",
            arca_template_id="tpl-1",
        )
        assert creds.is_configured() is False

    def test_is_configured_missing_api_key(self):
        creds = ArcaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            base_url="https://arca.example.com",
            api_key=None,
            arca_template_id="tpl-1",
        )
        assert creds.is_configured() is False

    def test_is_configured_missing_template_id(self):
        creds = ArcaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            base_url="https://arca.example.com",
            api_key="key-123",
            arca_template_id=None,
        )
        assert creds.is_configured() is False

    def test_is_configured_all_empty(self):
        creds = ArcaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            base_url="",
            api_key="",
            arca_template_id=None,
        )
        assert creds.is_configured() is False


class TestSigmaCredentials:
    """Test SigmaCredentials model."""

    def test_required(self):
        creds = SigmaCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            endpoint="https://sigma.example.com",
            access_key="ak-1",
            secret_key="sk-1",
        )
        assert creds.endpoint == "https://sigma.example.com"
        assert creds.region == "default"

    def test_missing_endpoint(self):
        with pytest.raises(ValidationError):
            SigmaCredentials(
                template_id=1,
                template_uuid=_TEMPLATE_UUID,
                access_key="ak-1",
                secret_key="sk-1",
            )


class TestLocalCredentials:
    """Test LocalCredentials model."""

    def test_minimal(self):
        creds = LocalCredentials(template_id=1, template_uuid=_TEMPLATE_UUID)
        assert creds.template_id == 1


class TestProxyExecRequest:
    """Test ProxyExecRequest model."""

    def test_fields(self):
        req = ProxyExecRequest(sandbox_id="sbx-1", command="ls")
        assert req.sandbox_id == "sbx-1"
        assert req.command == "ls"


class TestProxyHealthRequest:
    """Test ProxyHealthRequest model."""

    def test_fields(self):
        req = ProxyHealthRequest(sandbox_id="sbx-1")
        assert req.sandbox_id == "sbx-1"


class TestLocalDeviceId:
    """Test LocalDeviceId model."""

    def test_parse_valid(self):
        lid = LocalDeviceId.parse("container-1--machine-1--user-1")
        assert lid.container_id == "container-1"
        assert lid.machine_id == "machine-1"
        assert lid.user_id == "user-1"

    def test_format(self):
        lid = LocalDeviceId(container_id="c1", machine_id="m1", user_id="u1")
        assert lid.format() == "c1--m1--u1"

    def test_roundtrip(self):
        raw = "container-x--machine-y--user-z"
        lid = LocalDeviceId.parse(raw)
        assert lid.format() == raw

    def test_parse_invalid_too_few_parts(self):
        with pytest.raises(InvalidLocalDeviceIdError):
            LocalDeviceId.parse("only-two--parts")

    def test_parse_invalid_too_many_parts(self):
        with pytest.raises(InvalidLocalDeviceIdError):
            LocalDeviceId.parse("a--b--c--d")

    def test_parse_invalid_empty_parts(self):
        with pytest.raises(InvalidLocalDeviceIdError):
            LocalDeviceId.parse("")
        with pytest.raises(InvalidLocalDeviceIdError):
            LocalDeviceId.parse("a--")

    def test_invalid_error_extends_valueerror(self):
        assert issubclass(InvalidLocalDeviceIdError, ValueError)


class TestPoolabCredentials:
    """Test PoolabCredentials model."""

    def test_minimal(self):
        """Test construction with only base PaasCredentials fields."""
        creds = PoolabCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
        )
        assert creds.template_id == 1
        assert creds.template_uuid == _TEMPLATE_UUID
        assert creds.poolab_endpoint is None
        assert creds.poolab_tenant_id is None
        assert creds.poolab_tenant_token is None
        assert creds.poolab_image_id is None

    def test_with_all_fields(self):
        """Test construction with all Poolab-specific fields."""
        creds = PoolabCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            tenant_name="default",
            poolab_endpoint="https://poolab-pre.alipay.com",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="token-abc123",
            poolab_image_id="img-poolab-1",
        )
        assert creds.poolab_endpoint == "https://poolab-pre.alipay.com"
        assert creds.poolab_tenant_id == "tenant-001"
        assert creds.poolab_tenant_token == "token-abc123"
        assert creds.poolab_image_id == "img-poolab-1"

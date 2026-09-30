"""Unit tests for device.py Pydantic models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from secbaas.community.api.device_manage import (
    DeployConfig,
    DestroyDeviceResponse,
    DeviceConfig,
    DeviceCreate,
    DeviceInfo,
    DeviceListResponse,
    DeviceNotFoundError,
    DeviceResponse,
)


class TestDeviceConfig:
    """Test DeviceConfig model."""

    def test_empty(self):
        config = DeviceConfig()
        assert config.template_uuid is None
        assert config.deploy_config is None

    def test_with_template_uuid(self):
        config = DeviceConfig(template_uuid="tpl-abc")
        assert config.template_uuid == "tpl-abc"

    def test_with_deploy_config(self):
        dc = DeployConfig(after_create_cmd_hook="/hook.sh")
        config = DeviceConfig(deploy_config=dc)
        assert config.deploy_config is not None
        assert config.deploy_config.after_create_cmd_hook == "/hook.sh"

    def test_extra_fields_preserved(self):
        config = DeviceConfig.model_validate({"custom_key": "val"})
        assert config.model_dump()["custom_key"] == "val"

    def test_model_dump_exclude_none(self):
        config = DeviceConfig()
        data = config.model_dump(exclude_none=True)
        assert data == {"metadata": {}}

    def test_round_trip_with_extra(self):
        raw = {"template_uuid": "t-1", "custom": "x"}
        c1 = DeviceConfig.model_validate(raw)
        c2 = DeviceConfig.model_validate(c1.model_dump())
        assert c2.template_uuid == "t-1"
        assert c2.model_dump()["custom"] == "x"


class TestDeviceCreate:
    """Test DeviceCreate model."""

    def test_required_fields(self):
        req = DeviceCreate(operator="user-1")
        assert req.operator == "user-1"
        assert req.domain == "default"
        assert isinstance(req.extra_config, DeviceConfig)

    def test_custom_domain(self):
        req = DeviceCreate(domain="my-domain", operator="admin")
        assert req.domain == "my-domain"

    def test_operator_empty_rejected(self):
        with pytest.raises(ValidationError):
            DeviceCreate(operator="")

    def test_operator_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceCreate(operator="x" * 129)

    def test_domain_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceCreate(domain="x" * 129, operator="u")


class TestDeviceResponse:
    """Test DeviceResponse model."""

    @pytest.fixture
    def sample_data(self):
        return {
            "id": 1,
            "device_uuid": "dev-uuid-001",
            "tenant": "test-tenant",
            "env": "prod",
            "domain": "default",
            "status": "ACTIVE",
            "provider_type": None,
            "provider_device_id": None,
            "provider_device_props": None,
            "creator": "admin",
            "modifier": "admin",
            "gmt_create": datetime(2024, 1, 1, 0, 0, 0),
            "gmt_modified": datetime(2024, 1, 1, 0, 0, 0),
        }

    def test_required_fields(self, sample_data):
        resp = DeviceResponse(**sample_data)
        assert resp.id == 1
        assert resp.device_uuid == "dev-uuid-001"
        assert resp.tenant == "test-tenant"
        assert resp.status == "ACTIVE"

    def test_optional_fields_default_none(self, sample_data):
        resp = DeviceResponse(**sample_data)
        assert resp.provider_type is None
        assert resp.provider_device_id is None
        assert resp.extra_config is None

    def test_from_attributes_config(self):
        assert DeviceResponse.model_config.get("from_attributes") is True

    def test_extra_config_round_trip(self, sample_data):
        dc = DeviceConfig(template_uuid="t-1")
        sample_data["extra_config"] = dc
        resp = DeviceResponse(**sample_data)
        assert resp.extra_config is not None
        assert resp.extra_config.template_uuid == "t-1"


class TestDeviceInfo:
    """Test DeviceInfo model."""

    @pytest.fixture
    def sample_data(self):
        return {
            "device_uuid": "dev-001",
            "status": "ACTIVE",
            "gmt_create": datetime(2024, 1, 1, 0, 0, 0),
        }

    def test_required_fields(self, sample_data):
        info = DeviceInfo(**sample_data)
        assert info.device_uuid == "dev-001"
        assert info.status == "ACTIVE"

    def test_optional_fields(self, sample_data):
        info = DeviceInfo(**sample_data)
        assert info.provider_type is None
        assert info.provider_device_id is None

    def test_from_attributes_config(self):
        assert DeviceInfo.model_config.get("from_attributes") is True


class TestDeviceListResponse:
    """Test DeviceListResponse pagination model."""

    def test_empty(self):
        resp = DeviceListResponse(items=[], total=0, page=1, page_size=10)
        assert resp.items == []
        assert resp.total == 0

    def test_with_items(self):
        items = [
            DeviceResponse(
                id=1,
                device_uuid="d1",
                tenant="t",
                env="p",
                domain="d",
                status="A",
                provider_type=None,
                provider_device_id=None,
                provider_device_props=None,
                creator="u",
                modifier="u",
                gmt_create=datetime(2024, 1, 1),
                gmt_modified=datetime(2024, 1, 1),
            )
        ]
        resp = DeviceListResponse(items=items, total=1, page=1, page_size=20)
        assert len(resp.items) == 1
        assert resp.page == 1
        assert resp.page_size == 20


class TestDeviceNotFoundError:
    """Test DeviceNotFoundError domain error."""

    def test_default_message(self):
        err = DeviceNotFoundError()
        assert err.error_code == "DEVICE_NOT_FOUND"
        assert err.http_status == 404

    def test_with_string_id(self):
        err = DeviceNotFoundError("dev-abc")
        assert "dev-abc" in str(err)

    def test_with_int_id(self):
        err = DeviceNotFoundError(42)
        assert "42" in str(err)

    def test_is_exception(self):
        assert issubclass(DeviceNotFoundError, Exception)


class TestDestroyDeviceResponse:
    """Test DestroyDeviceResponse model."""

    def test_success(self):
        resp = DestroyDeviceResponse(success=True)
        assert resp.success is True
        assert resp.error_message is None
        assert resp.hook_result is None

    def test_failure_with_message(self):
        resp = DestroyDeviceResponse(success=False, error_message="timeout")
        assert resp.success is False
        assert resp.error_message == "timeout"

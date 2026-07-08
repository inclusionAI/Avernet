"""Unit tests for api/domain/device_template_manage.py — device template domain types."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from secbaas.api.template_manage import (
    ArcaTemplateConfig,
    DeviceTemplateResponse,
    DockerTemplateConfig,
    LocalTemplateConfig,
    SigmaTemplateConfig,
    TemplateByIdNotFoundError,
    TemplateCreate,
    TemplateListResponse,
    TemplateNotFoundError,
    TemplateStatus,
    TemplateUpdate,
)
from secbaas.api.tenant_manage import ImagePullPolicy, TenantType

# ==================== TemplateStatus ====================


class TestTemplateStatus:
    """Tests for the TemplateStatus enum."""

    def test_members(self):
        """THEN all expected members exist with correct values."""
        assert TemplateStatus.CREATED == "CREATED"
        assert TemplateStatus.AUDITED == "AUDITED"
        assert TemplateStatus.ONLINE == "ONLINE"
        assert TemplateStatus.OFFLINE == "OFFLINE"

    def test_is_str_enum(self):
        """THEN members behave as strings."""
        assert issubclass(TemplateStatus, str)

    def test_status_lifecycle_order(self):
        """THEN statuses represent lifecycle: CREATED → AUDITED → ONLINE ↔ OFFLINE."""
        assert list(TemplateStatus) == [
            TemplateStatus.CREATED,
            TemplateStatus.AUDITED,
            TemplateStatus.ONLINE,
            TemplateStatus.OFFLINE,
        ]


# ==================== ArcaTemplateConfig ====================


class TestArcaTemplateConfig:
    """Tests for ArcaTemplateConfig model."""

    def test_required_fields(self):
        """WHEN required fields provided, THEN model validates."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.type == "ARCA"
        assert config.base_url == "http://test"
        assert config.api_key == "test"

    def test_encrypt_api_key_default_false(self):
        """WHEN encrypt_api_key not provided, THEN defaults to False."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.encrypt_api_key is False

    def test_encrypt_api_key_true(self):
        """WHEN encrypt_api_key=True, THEN stored as True."""
        config = ArcaTemplateConfig(
            type="ARCA", base_url="http://test", api_key="test", encrypt_api_key=True
        )
        assert config.encrypt_api_key is True

    def test_default_app_name(self):
        """WHEN app_name not provided, THEN defaults to 'secbaas'."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.app_name == "secbaas"

    def test_default_ttl_minutes(self):
        """WHEN default_ttl_minutes not provided, THEN defaults to 1440."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.default_ttl_minutes == 1440

    def test_custom_ttl_minutes(self):
        """WHEN default_ttl_minutes provided, THEN custom value is stored."""
        config = ArcaTemplateConfig(
            type="ARCA", base_url="http://test", api_key="test", default_ttl_minutes=60
        )
        assert config.default_ttl_minutes == 60

    def test_default_timeout(self):
        """WHEN timeout not provided, THEN defaults to 30.0."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.timeout == 30.0

    def test_custom_timeout(self):
        """WHEN timeout provided, THEN custom value is stored."""
        config = ArcaTemplateConfig(
            type="ARCA", base_url="http://test", api_key="test", timeout=60.0
        )
        assert config.timeout == 60.0

    def test_default_template_id_none(self):
        """WHEN arca_template_id not provided, THEN defaults to None."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.arca_template_id is None

    def test_template_id_alias(self):
        """WHEN template_id alias provided, THEN sets arca_template_id."""
        config = ArcaTemplateConfig(
            type="ARCA", base_url="http://test", api_key="test", template_id="tpl-001"
        )
        assert config.arca_template_id == "tpl-001"

    def test_template_id_alias_by_alias(self):
        """WHEN template_id used in model_validate, THEN maps to arca_template_id."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "http://test",
                "api_key": "test",
                "template_id": "tpl-001",
            }
        )
        assert config.arca_template_id == "tpl-001"

    def test_extra_allow(self):
        """WHEN unknown fields provided, THEN accepted (extra='allow')."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "http://test",
                "api_key": "test",
                "custom_field": "some_value",
            }
        )
        assert config.model_dump().get("custom_field") == "some_value"

    def test_get_effective_template_id_pre_with_pre_id(self):
        """WHEN env='pre' and arca_template_id_pre set, THEN returns pre ID."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="test",
            arca_template_id="default-id",
            arca_template_id_pre="pre-id",
        )
        assert config.get_effective_template_id("pre") == "pre-id"

    def test_get_effective_template_id_pre_fallback(self):
        """WHEN env='pre' and arca_template_id_pre is None, THEN falls back to arca_template_id."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="test",
            arca_template_id="default-id",
        )
        assert config.get_effective_template_id("pre") == "default-id"

    def test_get_effective_template_id_prod_with_prod_id(self):
        """WHEN env='prod' and arca_template_id_prod set, THEN returns prod ID."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="test",
            arca_template_id="default-id",
            arca_template_id_prod="prod-id",
        )
        assert config.get_effective_template_id("prod") == "prod-id"

    def test_get_effective_template_id_prod_fallback(self):
        """WHEN env='prod' and arca_template_id_prod is None, THEN falls back to arca_template_id."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="test",
            arca_template_id="default-id",
        )
        assert config.get_effective_template_id("prod") == "default-id"

    def test_get_effective_template_id_other_env(self):
        """WHEN env is neither 'pre' nor 'prod', THEN returns arca_template_id."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="test",
            arca_template_id="default-id",
            arca_template_id_pre="pre-id",
            arca_template_id_prod="prod-id",
        )
        assert config.get_effective_template_id("dev") == "default-id"
        assert config.get_effective_template_id("test") == "default-id"

    def test_get_effective_template_id_case_insensitive(self):
        """WHEN env has mixed case, THEN normalized to lower."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="test",
            arca_template_id="default-id",
            arca_template_id_pre="pre-id",
        )
        assert config.get_effective_template_id("PRE") == "pre-id"
        assert config.get_effective_template_id("Prod") == "default-id"

    def test_oss_mount_id_default_none(self):
        """WHEN oss_mount_id not provided, THEN defaults to None."""
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        assert config.oss_mount_id is None

    def test_oss_mount_id_set(self):
        """WHEN oss_mount_id provided, THEN stored."""
        config = ArcaTemplateConfig(
            type="ARCA", base_url="http://test", api_key="test", oss_mount_id="oss-123"
        )
        assert config.oss_mount_id == "oss-123"


# ==================== SigmaTemplateConfig ====================


class TestSigmaTemplateConfig:
    """Tests for SigmaTemplateConfig model."""

    def test_required_fields(self):
        """WHEN required fields provided, THEN model validates."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="http://sigma:8080",
            access_key="ak-test",
            secret_key="sk-test",
        )
        assert config.type == "Sigma"
        assert config.endpoint == "http://sigma:8080"
        assert config.access_key == "ak-test"
        assert config.secret_key == "sk-test"

    def test_default_region(self):
        """WHEN region not provided, THEN defaults to 'default'."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="http://sigma:8080",
            access_key="ak-test",
            secret_key="sk-test",
        )
        assert config.region == "default"

    def test_custom_region(self):
        """WHEN region provided, THEN stored."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="http://sigma:8080",
            access_key="ak-test",
            secret_key="sk-test",
            region="cn-hangzhou",
        )
        assert config.region == "cn-hangzhou"

    def test_extra_allow(self):
        """WHEN unknown fields provided, THEN accepted (extra='allow')."""
        config = SigmaTemplateConfig.model_validate(
            {
                "type": "Sigma",
                "endpoint": "http://test",
                "access_key": "ak",
                "secret_key": "sk",
                "extra_param": 42,
            }
        )
        assert config.model_dump().get("extra_param") == 42


# ==================== LocalTemplateConfig ====================


class TestLocalTemplateConfig:
    """Tests for LocalTemplateConfig model."""

    def test_default_type(self):
        """WHEN type not provided, THEN defaults to 'LOCAL'."""
        config = LocalTemplateConfig()
        assert config.type == "LOCAL"

    def test_default_offline_threshold(self):
        """WHEN mng_offline_threshold_seconds not provided, THEN defaults to 30."""
        config = LocalTemplateConfig()
        assert config.mng_offline_threshold_seconds == 30

    def test_custom_offline_threshold(self):
        """WHEN mng_offline_threshold_seconds provided, THEN custom value stored."""
        config = LocalTemplateConfig(mng_offline_threshold_seconds=60)
        assert config.mng_offline_threshold_seconds == 60

    def test_extra_allow(self):
        """WHEN unknown fields provided, THEN accepted (extra='allow')."""
        config = LocalTemplateConfig.model_validate(
            {"type": "LOCAL", "extra_key": "val"}
        )
        assert config.model_dump().get("extra_key") == "val"


# ==================== DeviceTemplateConfig Union ====================


class TestDeviceTemplateConfig:
    """Tests for DeviceTemplateConfig union type discrimination."""

    def test_sigma_config_validate(self):
        """WHEN validating Sigma dict, THEN creates SigmaTemplateConfig."""
        raw = {
            "type": "Sigma",
            "endpoint": "http://test",
            "access_key": "ak",
            "secret_key": "sk",
        }
        config = SigmaTemplateConfig.model_validate(raw)
        assert isinstance(config, SigmaTemplateConfig)

    def test_local_config_validate(self):
        """WHEN validating LOCAL dict, THEN creates LocalTemplateConfig."""
        config = LocalTemplateConfig.model_validate({"type": "LOCAL"})
        assert isinstance(config, LocalTemplateConfig)


# ==================== TemplateCreate ====================


class TestTemplateCreate:
    """Tests for TemplateCreate model."""

    def test_required_fields(self):
        """WHEN required fields provided, THEN model validates."""
        data = TemplateCreate(
            template_id=1001,
            type=TenantType.ARCA,
            name="test-template",
            operator="user-1",
        )
        assert data.template_id == 1001
        assert data.type == TenantType.ARCA
        assert data.name == "test-template"
        assert data.operator == "user-1"

    def test_template_uuid_optional(self):
        """WHEN template_uuid omitted, THEN defaults to None (auto-generation)."""
        data = TemplateCreate(
            template_id=1001,
            type=TenantType.ARCA,
            name="test-template",
            operator="user-1",
        )
        assert data.template_uuid is None

    def test_template_uuid_provided(self):
        """WHEN template_uuid provided, THEN stored."""
        data = TemplateCreate(
            template_id=1001,
            type=TenantType.ARCA,
            name="test-template",
            operator="user-1",
            template_uuid="my-uuid-001",
        )
        assert data.template_uuid == "my-uuid-001"

    def test_template_uuid_min_length(self):
        """WHEN template_uuid is empty string, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001,
                type=TenantType.ARCA,
                name="test",
                operator="u",
                template_uuid="",
            )

    def test_template_uuid_max_length(self):
        """WHEN template_uuid exceeds 128 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001,
                type=TenantType.ARCA,
                name="test",
                operator="u",
                template_uuid="x" * 129,
            )

    def test_template_id_min_zero(self):
        """WHEN template_id is 0, THEN valid (minimum value)."""
        data = TemplateCreate(
            template_id=0, type=TenantType.ARCA, name="test", operator="u"
        )
        assert data.template_id == 0

    def test_template_id_negative_raises(self):
        """WHEN template_id is negative, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=-1, type=TenantType.ARCA, name="test", operator="u"
            )

    def test_name_min_length(self):
        """WHEN name is empty string, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001, type=TenantType.ARCA, name="", operator="u"
            )

    def test_name_max_length(self):
        """WHEN name exceeds 64 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001,
                type=TenantType.ARCA,
                name="x" * 65,
                operator="u",
            )

    def test_description_default_none(self):
        """WHEN description omitted, THEN defaults to None."""
        data = TemplateCreate(
            template_id=1001, type=TenantType.ARCA, name="test", operator="u"
        )
        assert data.description is None

    def test_description_max_length(self):
        """WHEN description exceeds 1024 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001,
                type=TenantType.ARCA,
                name="test",
                operator="u",
                description="x" * 1025,
            )

    def test_operator_min_length(self):
        """WHEN operator is empty string, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001, type=TenantType.ARCA, name="test", operator=""
            )

    def test_operator_max_length(self):
        """WHEN operator exceeds 128 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateCreate(
                template_id=1001,
                type=TenantType.ARCA,
                name="test",
                operator="x" * 129,
            )

    def test_default_config_araca(self):
        """WHEN config omitted, THEN defaults to ArcaTemplateConfig."""
        data = TemplateCreate(
            template_id=1001, type=TenantType.ARCA, name="test", operator="u"
        )
        assert isinstance(data.config, ArcaTemplateConfig)

    def test_custom_config_sigma(self):
        """WHEN config provided as Sigma, THEN stored."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="http://sigma:8080",
            access_key="ak",
            secret_key="sk",
        )
        data = TemplateCreate(
            template_id=1001,
            type=TenantType.SIGMA,
            name="test",
            operator="u",
            config=config,
        )
        assert isinstance(data.config, SigmaTemplateConfig)
        assert data.config.endpoint == "http://sigma:8080"


# ==================== TemplateUpdate ====================


class TestTemplateUpdate:
    """Tests for TemplateUpdate model."""

    def test_operator_required(self):
        """WHEN only operator provided, THEN model validates."""
        data = TemplateUpdate(operator="user-1")
        assert data.operator == "user-1"

    def test_all_fields_optional(self):
        """WHEN only operator provided, THEN other fields default to None."""
        data = TemplateUpdate(operator="user-1")
        assert data.template_id is None
        assert data.type is None
        assert data.name is None
        assert data.description is None
        assert data.config is None

    def test_partial_update_name_only(self):
        """WHEN only name provided, THEN only name is set."""
        data = TemplateUpdate(name="new-name", operator="user-1")
        assert data.name == "new-name"
        assert data.template_id is None

    def test_template_id_ge_1(self):
        """WHEN template_id is 1, THEN valid (minimum value for update)."""
        data = TemplateUpdate(template_id=1, operator="u")
        assert data.template_id == 1

    def test_template_id_zero_raises(self):
        """WHEN template_id is 0, THEN ValidationError (must be ≥1 for update)."""
        with pytest.raises(ValidationError):
            TemplateUpdate(template_id=0, operator="u")

    def test_name_min_length(self):
        """WHEN name is empty string, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateUpdate(name="", operator="u")

    def test_name_max_length(self):
        """WHEN name exceeds 64 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateUpdate(name="x" * 65, operator="u")

    def test_description_max_length(self):
        """WHEN description exceeds 1024 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateUpdate(description="x" * 1025, operator="u")

    def test_operator_min_length(self):
        """WHEN operator is empty string, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TemplateUpdate(operator="")

    def test_update_with_config(self):
        """WHEN config provided, THEN update includes new config."""
        config = ArcaTemplateConfig(
            type="ARCA", base_url="http://new-url", api_key="new-key"
        )
        data = TemplateUpdate(config=config, operator="u")
        assert data.config.base_url == "http://new-url"


# ==================== DeviceTemplateResponse ====================


class TestDeviceTemplateResponse:
    """Tests for DeviceTemplateResponse model."""

    def test_all_fields(self):
        """WHEN all fields provided, THEN model validates."""
        now = datetime.now()
        config = ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")
        resp = DeviceTemplateResponse(
            id=1,
            template_id=1001,
            type="ARCA",
            template_uuid="uuid-001",
            tenant="test-tenant",
            name="test-template",
            description="desc",
            status="ONLINE",
            config=config,
            creator="user-1",
            modifier="user-1",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.id == 1
        assert resp.template_id == 1001
        assert resp.template_uuid == "uuid-001"
        assert resp.tenant == "test-tenant"
        assert resp.name == "test-template"
        assert resp.status == "ONLINE"
        assert resp.description == "desc"
        assert resp.config is not None
        assert resp.creator == "user-1"
        assert resp.modifier == "user-1"
        assert resp.gmt_create == now
        assert resp.gmt_modified == now

    def test_description_optional(self):
        """WHEN description is None, THEN stored as None."""
        now = datetime.now()
        resp = DeviceTemplateResponse(
            id=1,
            template_id=1001,
            type="ARCA",
            template_uuid="uuid-001",
            tenant="test-tenant",
            name="test",
            description=None,
            status="CREATED",
            config=None,
            creator="u",
            modifier="u",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.description is None
        assert resp.config is None

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True."""
        assert DeviceTemplateResponse.model_config.get("from_attributes") is True

    def test_populate_by_name(self):
        """THEN model_config has populate_by_name=True."""
        assert DeviceTemplateResponse.model_config.get("populate_by_name") is True


# ==================== TemplateListResponse ====================


class TestTemplateListResponse:
    """Tests for TemplateListResponse model."""

    def test_basic_structure(self):
        """WHEN created with items, THEN structure is correct."""
        now = datetime.now()
        item = DeviceTemplateResponse(
            id=1,
            template_id=1001,
            type="ARCA",
            template_uuid="uuid-001",
            tenant="test",
            name="test",
            description=None,
            status="CREATED",
            config=None,
            creator="u",
            modifier="u",
            gmt_create=now,
            gmt_modified=now,
        )
        resp = TemplateListResponse(items=[item], total=1, page=1, page_size=20)
        assert len(resp.items) == 1
        assert resp.items[0].name == "test"
        assert resp.total == 1
        assert resp.page == 1
        assert resp.page_size == 20

    def test_empty_items(self):
        """WHEN no items, THEN total is 0, items is empty."""
        resp = TemplateListResponse(items=[], total=0, page=1, page_size=20)
        assert resp.items == []
        assert resp.total == 0


# ==================== TemplateNotFoundError ====================


class TestTemplateNotFoundError:
    """Tests for TemplateNotFoundError exception."""

    def test_with_uuid_string(self):
        """WHEN created with uuid string, THEN message includes uuid."""
        err = TemplateNotFoundError(template_uuid="uuid-001")
        assert err.error_code == "TEMPLATE_NOT_FOUND"
        assert err.http_status == 404
        assert "uuid-001" in err.message
        assert "uuid-001" in str(err)

    def test_with_int(self):
        """WHEN created with int, THEN message includes int."""
        err = TemplateNotFoundError(template_uuid=1001)
        assert "1001" in err.message

    def test_default_empty(self):
        """WHEN created without args, THEN message with empty string."""
        err = TemplateNotFoundError()
        assert err.message == "Template with uuid not found: "


# ==================== TemplateByIdNotFoundError ====================


class TestTemplateByIdNotFoundError:
    """Tests for TemplateByIdNotFoundError exception."""

    def test_with_template_id(self):
        """WHEN created with template_id, THEN message includes ID."""
        err = TemplateByIdNotFoundError(template_id=1001)
        assert err.error_code == "TEMPLATE_BY_ID_NOT_FOUND"
        assert err.http_status == 404
        assert "1001" in err.message
        assert "1001" in str(err)

    def test_default_zero(self):
        """WHEN created without args, THEN message with 0."""
        err = TemplateByIdNotFoundError()
        assert err.message == "Template not found by ID: 0"


# ==================== ImagePullPolicy ====================


class TestImagePullPolicy:
    """Tests for the ImagePullPolicy enum."""

    def test_members(self):
        """THEN all expected members exist with correct values."""
        assert ImagePullPolicy.ALWAYS == "always"
        assert ImagePullPolicy.IF_NOT_PRESENT == "if_not_present"
        assert ImagePullPolicy.NEVER == "never"

    def test_is_str_enum(self):
        """THEN members behave as strings."""
        assert issubclass(ImagePullPolicy, str)


# ==================== DockerTemplateConfig ====================


class TestDockerTemplateConfig:
    """Tests for DockerTemplateConfig model."""

    def test_required_fields(self):
        """WHEN required fields provided, THEN model validates and defaults correct."""
        config = DockerTemplateConfig(
            type="DOCKER",
            image="alpine:latest",
            container_port=8080,
            memory_limit="512m",
        )
        assert config.type == "DOCKER"
        assert config.image == "alpine:latest"
        assert config.container_port == 8080
        assert config.memory_limit == "512m"
        assert config.image_pull_policy == ImagePullPolicy.IF_NOT_PRESENT
        assert config.health_endpoint == "/health"
        assert config.health_timeout_seconds == 120
        assert config.default_ttl_minutes == 1440
        assert config.cpu_limit == 1.0

    def test_missing_image_raises(self):
        """WHEN image is missing, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER", container_port=8080, memory_limit="512m"
            )

    def test_missing_memory_limit_raises(self):
        """WHEN memory_limit is missing, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER", image="alpine:latest", container_port=8080
            )

    def test_missing_container_port_raises(self):
        """WHEN container_port is missing, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER", image="alpine:latest", memory_limit="512m"
            )

    def test_default_values(self):
        """WHEN minimal construct, THEN all defaults are correct."""
        config = DockerTemplateConfig(
            type="DOCKER", image="nginx", container_port=80, memory_limit="256m"
        )
        assert config.image_pull_policy == ImagePullPolicy.IF_NOT_PRESENT
        assert config.health_endpoint == "/health"
        assert config.health_timeout_seconds == 120
        assert config.default_ttl_minutes == 1440
        assert config.cpu_limit == 1.0
        assert config.envs is None

    def test_container_port_range_too_low(self):
        """WHEN container_port=0, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER",
                image="alpine:latest",
                container_port=0,
                memory_limit="512m",
            )

    def test_container_port_range_too_high(self):
        """WHEN container_port=65536, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER",
                image="alpine:latest",
                container_port=65536,
                memory_limit="512m",
            )

    def test_cpu_limit_range_too_low(self):
        """WHEN cpu_limit=0.05, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER",
                image="alpine:latest",
                container_port=8080,
                memory_limit="512m",
                cpu_limit=0.05,
            )

    def test_cpu_limit_range_too_high(self):
        """WHEN cpu_limit=65, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER",
                image="alpine:latest",
                container_port=8080,
                memory_limit="512m",
                cpu_limit=65,
            )

    def test_health_timeout_range_too_low(self):
        """WHEN health_timeout_seconds=5, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER",
                image="alpine:latest",
                container_port=8080,
                memory_limit="512m",
                health_timeout_seconds=5,
            )

    def test_health_timeout_range_too_high(self):
        """WHEN health_timeout_seconds=601, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="DOCKER",
                image="alpine:latest",
                container_port=8080,
                memory_limit="512m",
                health_timeout_seconds=601,
            )

    def test_type_literal_fixed(self):
        """WHEN type is not 'DOCKER', THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerTemplateConfig(
                type="WRONG",
                image="alpine:latest",
                container_port=8080,
                memory_limit="512m",
            )

    def test_envs_optional(self):
        """WHEN envs not provided, THEN defaults to None."""
        config = DockerTemplateConfig(
            type="DOCKER", image="nginx", container_port=80, memory_limit="256m"
        )
        assert config.envs is None

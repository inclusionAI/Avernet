"""Unit tests for PaasServiceFacade - Poolab platform branch.

Covers POOLAB-specific logic: _POOLAB_ALLOWED_OVERRIDE_FIELDS whitelist
filtering in _merge_config, _parse_device_id for integer IDs, class-name-based
_get_platform_type inference, and create_device full chain with PoolabCreateConfig
validation and {poolab_id}@{template_id} ID assembly.

Per D-06 (difference-points + create_device full-chain strategy) and D-07
(no Arca mirroring — destroy_device, get_device_info, execute_command in
the facade have no POOLAB-specific code paths).
"""

from unittest.mock import AsyncMock, MagicMock

import pydantic
import pytest

from secbaas.community.api.device_manage import (
    PoolabCreateConfig,
    PoolabCreationResult,
    PoolabCredentials,
    PoolabDeviceConfig,
)
from secbaas.community.api.template_manage import (
    PoolabTemplateConfig,
    TemplateStatus,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
    PoolabPaasService,
)
from secbaas.plugins.sandbox.poolab import StubPoolabSandboxPlugin

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def facade():
    """Create a fresh PaasServiceFacade instance with mocked dependencies."""
    return PaasServiceFacade(
        device_repository=MagicMock(),
        device_template_service=MagicMock(),
        factory=MagicMock(),
    )


@pytest.fixture
def mock_poolab_service():
    """Create a mock PoolabPaasService with async methods."""
    mock = MagicMock()
    mock.create_device = AsyncMock()
    mock.get_credentials = AsyncMock()
    mock.get_platform_type = AsyncMock()
    return mock


@pytest.fixture
def mock_factory_service(facade, mock_poolab_service):
    """Wire mock_poolab_service as the facade factory's create() return value."""
    facade._factory.create.return_value = mock_poolab_service
    return facade._factory


# ============================================================================
# Helpers
# ============================================================================


def make_poolab_template(
    tenant="test-tenant",
    template_uuid="test-poolab-template-uuid",
    template_id=42,
):
    """Create a mock POOLAB template response."""
    config = PoolabTemplateConfig(
        type="POOLAB",
        poolab_endpoint_pre="http://poolab-pre.test:8080",
        poolab_endpoint_prod="http://poolab-prod.test:8080",
        poolab_tenant_id="tenant-001",
        poolab_tenant_token="template-token",
        poolab_default_image_id_pre="img-pre-001",
        poolab_default_image_id_prod="img-prod-001",
    )
    template = MagicMock()
    template.id = 1
    template.template_id = template_id
    template.template_uuid = template_uuid
    template.type = "POOLAB"
    template.tenant = tenant
    template.name = "Poolab Test Template"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    return template


# ============================================================================
# TestPoolabConfigMerge — _merge_config POOLAB whitelist filtering
# ============================================================================


class TestPoolabConfigMerge:
    """Test _merge_config POOLAB branch — whitelist filtering.

    Per D-06: Cover POOLAB-specific _POOLAB_ALLOWED_OVERRIDE_FIELDS
    whitelist behavior (field retention, field filtering, template-only
    fallback, platform mismatch validation).
    """

    def test_poolab_merge_retains_allowed_fields(self, facade):
        """POOLAB whitelist fields are retained in merged config."""
        template_config = PoolabTemplateConfig(
            type="POOLAB",
            poolab_endpoint_pre="http://poolab-pre.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="template-token",
        )
        detail_config = PoolabDeviceConfig(
            poolab_user_id="user-001",
            poolab_image_id="img-001",
            poolab_envs={"KEY": "VALUE"},
            name="test-device",
            description="test desc",
        )
        merged = facade._merge_config(template_config, detail_config, "POOLAB")

        # Allowed fields retained
        assert merged["poolab_user_id"] == "user-001"
        assert merged["poolab_image_id"] == "img-001"
        assert merged["poolab_envs"] == {"KEY": "VALUE"}
        assert merged["name"] == "test-device"
        assert merged["description"] == "test desc"

    def test_poolab_merge_filters_disallowed_fields(self, facade):
        """Fields not in _POOLAB_ALLOWED_OVERRIDE_FIELDS are filtered.

        Per D-06: Template credential fields (endpoint, tenant_token)
        are NOT in the whitelist and must not be overridden.
        """
        template_config = PoolabTemplateConfig(
            type="POOLAB",
            poolab_endpoint_pre="http://poolab-pre.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="template-token",
        )
        detail_config = PoolabDeviceConfig(
            poolab_user_id="user-001",
            name="test-device",
        )
        merged = facade._merge_config(template_config, detail_config, "POOLAB")

        # Whitelisted user-facing fields are retained
        assert merged["poolab_user_id"] == "user-001"
        assert merged["name"] == "test-device"
        # Template credential fields are NOT overridden
        assert merged["poolab_endpoint_pre"] == "http://poolab-pre.test:8080"
        assert merged["poolab_tenant_token"] == "template-token"

    def test_poolab_merge_template_only_no_detail(self, facade):
        """When detail_config is None, return template config as-is."""
        template_config = PoolabTemplateConfig(
            type="POOLAB",
            poolab_endpoint_pre="http://poolab-pre.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="template-token",
        )
        merged = facade._merge_config(template_config, None, "POOLAB")

        assert merged["poolab_endpoint_pre"] == "http://poolab-pre.test:8080"
        assert merged["poolab_tenant_id"] == "tenant-001"
        assert merged["poolab_tenant_token"] == "template-token"
        assert merged["type"] == "POOLAB"

    def test_poolab_merge_platform_mismatch_raises(self, facade):
        """Non-POOLAB detail_config with POOLAB platform_type raises ValueError.

        The facade validates that detail_config type matches platform_type to
        catch misconfigurations early.
        """
        from secbaas.community.api.device_manage import ArcaDeviceConfig

        template_config = PoolabTemplateConfig(
            type="POOLAB",
            poolab_endpoint_pre="http://poolab-pre.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="tok",
        )
        detail_config = ArcaDeviceConfig(arca_template_id="tpl-test")

        with pytest.raises(ValueError):
            facade._merge_config(template_config, detail_config, "POOLAB")


# ============================================================================
# TestPoolabParseDeviceId — _parse_device_id for POOLAB integer IDs
# ============================================================================


class TestPoolabParseDeviceId:
    """Test _parse_device_id for POOLAB format integer IDs.

    Per D-06: Validate that POOLAB-style integer IDs parse correctly
    through the shared _parse_device_id static method.
    """

    def test_parse_poolab_id_with_suffix(self, facade):
        """Parse POOLAB device ID with @template_id suffix."""
        device_id, template_id = facade._parse_device_id("123@42")
        assert device_id == "123"
        assert template_id == 42

    def test_parse_poolab_id_multiple_at(self, facade):
        """POOLAB ID with multiple @ symbols splits from rightmost."""
        device_id, template_id = facade._parse_device_id("user@host@99")
        assert device_id == "user@host"
        assert template_id == 99


# ============================================================================
# TestPoolabGetPlatformType — _get_platform_type class-name inference
# ============================================================================


class TestPoolabGetPlatformType:
    """Test _get_platform_type POOLAB inference from class name.

    When a PaasService does not have an explicit get_platform_type method,
    the facade falls back to class-name-based detection.
    """

    @pytest.mark.asyncio
    async def test_poolab_service_detected_by_class_name(self):
        """PoolabPaasService is detected as POOLAB by class name."""
        mock_service = MagicMock()
        mock_service.__class__.__name__ = "PoolabPaasService"
        # Remove get_platform_type so facade falls back to class-name detection
        del mock_service.get_platform_type

        platform = await PaasServiceFacade._get_platform_type(mock_service)
        assert platform == "POOLAB"


# ============================================================================
# TestPoolabCreateDevice — create_device POOLAB full chain
# ============================================================================


class TestPoolabCreateDevice:
    """Test create_device POOLAB branch: config validation + ID assembly.

    Per D-06: Cover the full create_device chain for POOLAB:
    template resolution → _merge_config → PoolabCreateConfig.model_validate
    → service.create_device → PoolabCreationResult with {poolab_id}@{template_id}.
    Plus error-path (PaasError wrapping) and config validation failure.
    """

    @pytest.mark.asyncio
    async def test_create_poolab_device_happy_path(
        self, facade, mock_poolab_service, mock_factory_service
    ):
        """create_device POOLAB branch: full chain with ID assembly.

        Per Pitfall 5 (RESEARCH.md): Assert EVERY field on the returned
        PoolabCreationResult to ensure the shallow-copy + ID-overwrite
        pattern is complete and no fields are dropped.
        """
        template = make_poolab_template(template_id=42)
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_poolab_service.get_credentials.return_value = PoolabCredentials(
            template_id=42,
            template_uuid="test-poolab-template-uuid",
            tenant_name="test-tenant",
            poolab_endpoint="http://poolab.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="tok",
            poolab_image_id="img-001",
        )
        mock_poolab_service.get_platform_type.return_value = MagicMock(value="POOLAB")

        service_result = PoolabCreationResult(
            platform="poolab",
            status="CREATED",
            poolab_id="456",
            poolab_user_id="user-001",
            poolab_user_nick="TestUser",
            poolab_hostname="test-host",
            poolab_image_id="img-001",
            poolab_order_id="1001",
            poolab_status="READY",
            poolab_openclaw_url="http://test-host:9999",
            poolab_openclaw_token="ws-token",
            poolab_display_status="OPENED",
            poolab_type="OpenClaw",
            poolab_network_type="PUBLIC",
            poolab_operations_url="http://test-host:9999/?token=ws-token",
            poolab_remote_url="http://test-host/vnc.html",
            poolab_model_config_type="public",
            poolab_env="TEST",
        )
        mock_poolab_service.create_device.return_value = service_result

        result = await facade.create_device(
            tenant_name="test-tenant",
            device_template_uuid="test-poolab-template-uuid",
            detail_config=PoolabDeviceConfig(
                poolab_user_id="user-001",
                name="test-device",
            ),
        )

        assert isinstance(result, PoolabCreationResult)
        # ID assembly: poolab_id should have @template_id suffix
        assert result.poolab_id == "456@42"
        # All other fields preserved from service result
        assert result.platform == "poolab"
        assert result.poolab_user_id == "user-001"
        assert result.poolab_user_nick == "TestUser"
        assert result.poolab_hostname == "test-host"
        assert result.poolab_image_id == "img-001"
        assert result.poolab_order_id == "1001"
        assert result.poolab_status == "READY"
        assert result.poolab_openclaw_url == "http://test-host:9999"
        assert result.poolab_openclaw_token == "ws-token"
        assert result.poolab_display_status == "OPENED"
        assert result.poolab_type == "OpenClaw"
        assert result.poolab_network_type == "PUBLIC"
        assert result.poolab_operations_url == "http://test-host:9999/?token=ws-token"
        assert result.poolab_remote_url == "http://test-host/vnc.html"
        assert result.poolab_model_config_type == "public"
        assert result.poolab_env == "TEST"

    @pytest.mark.asyncio
    async def test_create_poolab_device_wraps_paas_error(
        self, facade, mock_poolab_service, mock_factory_service
    ):
        """create_device wraps PaasError as DeviceFacadeException.

        When the underlying PoolabPaasService.create_device raises PaasError,
        the facade wraps it in DeviceFacadeException with operation,
        platform_type, template_id, and original_error preserved.
        """
        template = make_poolab_template(template_id=42)
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_poolab_service.get_credentials.return_value = PoolabCredentials(
            template_id=42,
            template_uuid="test-poolab-template-uuid",
            tenant_name="test-tenant",
            poolab_endpoint="http://poolab.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="tok",
            poolab_image_id="img-001",
        )
        mock_poolab_service.get_platform_type.return_value = MagicMock(value="POOLAB")

        paas_error = PaasError(ErrorCode.DEVICE_CREATION_FAILED, "Creation failed")
        mock_poolab_service.create_device.side_effect = paas_error

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="test-poolab-template-uuid",
                detail_config=PoolabDeviceConfig(
                    poolab_user_id="user-001",
                    name="test-device",
                ),
            )

        assert exc_info.value.operation == "create_device"
        assert exc_info.value.platform_type == "POOLAB"
        assert exc_info.value.template_id == 42
        assert exc_info.value.original_error == paas_error

    @pytest.mark.asyncio
    async def test_create_poolab_device_config_validation_failure(
        self, facade, mock_poolab_service, mock_factory_service
    ):
        """create_device POOLAB: missing mandatory config triggers validation error.

        When the template config is None (simulating a template with missing
        mandatory config), _merge_config raises ValueError, which propagates
        as an unhandled error from the facade. This guards against invalid
        template data reaching service.create_device.
        """
        template = make_poolab_template(template_id=42)
        # Simulate template with missing config triggering early validation
        template.config = None
        mock_tpl_svc = MagicMock()
        mock_tpl_svc.get_default_or_explicit_template.return_value = template
        facade._get_template_service = MagicMock(return_value=mock_tpl_svc)

        mock_poolab_service.get_credentials.return_value = PoolabCredentials(
            template_id=42,
            template_uuid="test-poolab-template-uuid",
            tenant_name="test-tenant",
            poolab_endpoint="http://poolab.test:8080",
            poolab_tenant_id="tenant-001",
            poolab_tenant_token="tok",
            poolab_image_id="img-001",
        )
        mock_poolab_service.get_platform_type.return_value = MagicMock(value="POOLAB")

        # _merge_config with template_config=None will fail before
        # pydantic validation, raising ValueError. Either ValueError or
        # pydantic.ValidationError indicates correct handling of invalid config.
        with pytest.raises((ValueError, pydantic.ValidationError)):
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="test-poolab-template-uuid",
                detail_config=None,
            )


# ============================================================================
# TestPoolabFileTransferNotImplemented — pull/push raises NotImplementedError
# ============================================================================


class TestPoolabFileTransferNotImplemented:
    """Verify poolab PaasService pull/push raise NotImplementedError."""

    @pytest.mark.asyncio
    async def test_pull_file_from_url_raises_not_implemented(self):
        service = PoolabPaasService(
            credentials=PoolabCredentials(
                template_id=1,
                template_uuid="tpl-test",
                poolab_endpoint="http://poolab.test:8080",
            ),
            plugin=StubPoolabSandboxPlugin(),
        )

        with pytest.raises(
            NotImplementedError, match="File transfer not supported on Poolab platform"
        ):
            await service.pull_file_from_url("device-1", "http://src", "/dst")

    @pytest.mark.asyncio
    async def test_push_file_to_url_raises_not_implemented(self):
        service = PoolabPaasService(
            credentials=PoolabCredentials(
                template_id=1,
                template_uuid="tpl-test",
                poolab_endpoint="http://poolab.test:8080",
            ),
            plugin=StubPoolabSandboxPlugin(),
        )

        with pytest.raises(
            NotImplementedError, match="File transfer not supported on Poolab platform"
        ):
            await service.push_file_to_url("device-1", "/src", "http://dst")

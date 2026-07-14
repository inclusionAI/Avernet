"""Unit tests for PaasServiceFacade - TECLAW platform branch.

Covers TECLAW-specific logic: _TECLAW_ALLOWED_OVERRIDE_FIELDS whitelist
filtering in _merge_config, _parse_device_id for teclaw_bot_id suffix parsing,
and create_device full chain with TeClawCreateConfig validation and
{teclaw_bot_id}@{template_id} ID assembly.

Per phase 54 plan 03: structurally mirrors test_poolab_facade.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pydantic
import pytest

from secbaas.community.api.device_manage import (
    TeClawCreateConfig,
    TeClawCreationResult,
    TeClawCredentials,
    TeClawDeviceConfig,
)
from secbaas.community.api.template_manage import (
    TeClawTemplateConfig,
    TemplateStatus,
)
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
    TeClawPaasService,
)

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
def mock_teclaw_service():
    """Create a mock TeClawPaasService with async methods."""
    mock = MagicMock()
    mock.create_device = AsyncMock()
    mock.get_credentials = AsyncMock()
    mock.get_platform_type = AsyncMock()
    return mock


@pytest.fixture
def mock_factory_service(facade, mock_teclaw_service):
    """Wire mock_teclaw_service as the facade factory's create() return value."""
    facade._factory.create.return_value = mock_teclaw_service
    return facade._factory


# ============================================================================
# Helpers
# ============================================================================


def make_teclaw_template(
    tenant="test-tenant",
    template_uuid="test-teclaw-template-uuid",
    template_id=42,
):
    """Create a mock TECLAW template response."""
    config = TeClawTemplateConfig(
        type="TECLAW",
        teclaw_endpoint="http://teclaw.test:8080",
    )
    template = MagicMock()
    template.id = 1
    template.template_id = template_id
    template.template_uuid = template_uuid
    template.type = "TECLAW"
    template.tenant = tenant
    template.name = "TeClaw Test Template"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    return template


# ============================================================================
# TestFacadeMergeConfigForTeClaw — _merge_config TECLAW whitelist filtering
# ============================================================================


class TestFacadeMergeConfigForTeClaw:
    """Test _merge_config TECLAW branch — whitelist filtering.

    Per D-06: Cover TECLAW-specific _TECLAW_ALLOWED_OVERRIDE_FIELDS
    whitelist behavior (field retention, field filtering, template-only
    fallback, platform mismatch validation).
    """

    def test_teclaw_merge_retains_allowed_fields(self, facade):
        """TECLAW whitelist fields are retained in merged config.

        _TECLAW_ALLOWED_OVERRIDE_FIELDS = {teclaw_bot_config, name, description}.
        """
        template_config = TeClawTemplateConfig(
            type="TECLAW",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        detail_config = TeClawDeviceConfig(
            teclaw_bot_config={"cpu": 2, "mem": "4Gi"},
            name="test-device",
            description="test desc",
        )
        merged = facade._merge_config(template_config, detail_config, "TECLAW")

        # Allowed fields retained
        assert merged["teclaw_bot_config"] == {"cpu": 2, "mem": "4Gi"}
        assert merged["name"] == "test-device"
        assert merged["description"] == "test desc"

    def test_teclaw_merge_filters_disallowed_fields(self, facade):
        """Template credential fields are NOT in the whitelist and must not be overridden.

        TeClawDeviceConfig only has teclaw_bot_config, name, description fields.
        The template's teclaw_endpoint is a credential field that should never
        be overridden by detail_config.
        """
        template_config = TeClawTemplateConfig(
            type="TECLAW",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        detail_config = TeClawDeviceConfig(
            teclaw_bot_config={"cpu": 2},
            name="test-device",
        )
        merged = facade._merge_config(template_config, detail_config, "TECLAW")

        # Whitelisted user-facing fields are retained
        assert merged["teclaw_bot_config"] == {"cpu": 2}
        assert merged["name"] == "test-device"
        # Template credential fields are NOT overridden — still the template value
        assert merged["teclaw_endpoint"] == "http://teclaw.test:8080"
        assert merged["type"] == "TECLAW"

    def test_teclaw_merge_template_only_no_detail(self, facade):
        """When detail_config is None, return template config as-is."""
        template_config = TeClawTemplateConfig(
            type="TECLAW",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        merged = facade._merge_config(template_config, None, "TECLAW")

        assert merged["teclaw_endpoint"] == "http://teclaw.test:8080"
        assert merged["type"] == "TECLAW"

    def test_teclaw_merge_platform_mismatch_raises(self, facade):
        """Non-TECLAW detail_config with TECLAW platform_type raises ValueError.

        The facade validates that detail_config type matches platform_type to
        catch misconfigurations early.
        """
        from secbaas.community.api.device_manage import ArcaDeviceConfig

        template_config = TeClawTemplateConfig(
            type="TECLAW",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        detail_config = ArcaDeviceConfig(arca_template_id="tpl-test")

        with pytest.raises(ValueError, match="must be TeClawDeviceConfig"):
            facade._merge_config(template_config, detail_config, "TECLAW")


# ============================================================================
# TestFacadeParseDeviceIdForTeClaw — _parse_device_id for teclaw_bot_id suffix
# ============================================================================


class TestFacadeParseDeviceIdForTeClaw:
    """Test _parse_device_id for TECLAW format IDs.

    Per D-06: Validate that TECLAW-style teclaw_bot_id IDs parse correctly
    through the shared _parse_device_id static method using rsplit("@", 1).
    """

    def test_parse_teclaw_id_with_suffix(self, facade):
        """Parse TECLAW device ID with @template_id suffix."""
        device_id, template_id = facade._parse_device_id("bot-abc123@42")
        assert device_id == "bot-abc123"
        assert template_id == 42

    def test_parse_teclaw_id_multiple_at(self, facade):
        """TECLAW ID with multiple @ symbols splits from rightmost."""
        device_id, template_id = facade._parse_device_id("bot@domain@99")
        assert device_id == "bot@domain"
        assert template_id == 99


# ============================================================================
# TestFacadeCreateDeviceForTeClaw — create_device TECLAW full chain
# ============================================================================


class TestFacadeCreateDeviceForTeClaw:
    """Test create_device TECLAW branch: config validation + ID assembly.

    Per D-06: Cover the full create_device chain for TECLAW:
    template resolution -> _merge_config -> TeClawCreateConfig.model_validate
    -> service.create_device -> TeClawCreationResult with {teclaw_bot_id}@{template_id}.
    Plus error-path (PaasError wrapping) and config validation failure.
    """

    @pytest.mark.asyncio
    async def test_create_teclaw_device_happy_path(
        self, facade, mock_teclaw_service, mock_factory_service
    ):
        """create_device TECLAW branch: full chain with ID assembly.

        Per D-06: The facade suffixes teclaw_bot_id with @template_id
        after successful service.create_device. All other fields are preserved.
        """
        template = make_teclaw_template(template_id=42)
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_teclaw_service.get_credentials.return_value = TeClawCredentials(
            template_id=42,
            template_uuid="test-teclaw-template-uuid",
            tenant_name="test-tenant",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        mock_teclaw_service.get_platform_type.return_value = MagicMock(value="TECLAW")

        service_result = TeClawCreationResult(
            platform="teclaw",
            status="CREATED",
            teclaw_bot_id="bot-abc123",
            teclaw_bot_config={"cpu": 2},
        )
        mock_teclaw_service.create_device.return_value = service_result

        result = await facade.create_device(
            tenant_name="test-tenant",
            device_template_uuid="test-teclaw-template-uuid",
            detail_config=TeClawDeviceConfig(
                name="test-device",
                teclaw_bot_config={"cpu": 2},
            ),
        )

        assert isinstance(result, TeClawCreationResult)
        # ID assembly: teclaw_bot_id should have @template_id suffix
        assert result.teclaw_bot_id == "bot-abc123@42"
        # All other fields preserved from service result
        assert result.platform == "teclaw"
        assert result.status == "CREATED"
        assert result.teclaw_bot_config == {"cpu": 2}

    @pytest.mark.asyncio
    async def test_create_teclaw_device_wraps_paas_error(
        self, facade, mock_teclaw_service, mock_factory_service
    ):
        """create_device wraps PaasError as DeviceFacadeException.

        When the underlying TeClawPaasService.create_device raises PaasError,
        the facade wraps it in DeviceFacadeException with operation,
        platform_type, template_id, and original_error preserved.
        """
        template = make_teclaw_template(template_id=42)
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_teclaw_service.get_credentials.return_value = TeClawCredentials(
            template_id=42,
            template_uuid="test-teclaw-template-uuid",
            tenant_name="test-tenant",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        mock_teclaw_service.get_platform_type.return_value = MagicMock(value="TECLAW")

        paas_error = PaasError(ErrorCode.DEVICE_CREATION_FAILED, "Creation failed")
        mock_teclaw_service.create_device.side_effect = paas_error

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="test-teclaw-template-uuid",
                detail_config=TeClawDeviceConfig(
                    name="test-device",
                ),
            )

        assert exc_info.value.operation == "create_device"
        assert exc_info.value.platform_type == "TECLAW"
        assert exc_info.value.template_id == 42
        assert exc_info.value.original_error == paas_error

    @pytest.mark.asyncio
    async def test_create_teclaw_device_config_validation_failure(
        self, facade, mock_teclaw_service, mock_factory_service
    ):
        """create_device TECLAW: unknown platform type triggers ValueError.

        When the template type is None (simulating a misconfigured template),
        platform_str becomes empty string, and the facade raises ValueError
        for the unknown platform type before reaching service.create_device.
        """
        template = make_teclaw_template(template_id=42)
        # Simulate template with no type configured
        template.type = None
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_teclaw_service.get_credentials.return_value = TeClawCredentials(
            template_id=42,
            template_uuid="test-teclaw-template-uuid",
            tenant_name="test-tenant",
            teclaw_endpoint="http://teclaw.test:8080",
        )
        mock_teclaw_service.get_platform_type.return_value = MagicMock(value="TECLAW")

        # platform_str = template.type.upper() with template.type=None -> ""
        # then "".upper() -> "" which doesn't match any platform branch,
        # so falls to else: raise ValueError("Unknown platform type: ")
        with pytest.raises((ValueError, pydantic.ValidationError)):
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="test-teclaw-template-uuid",
                detail_config=None,
            )

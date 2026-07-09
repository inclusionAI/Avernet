"""Unit tests for PaasServiceFacade - DOCKER platform branch.

Covers DOCKER-specific logic: _DOCKER_ALLOWED_OVERRIDE_FIELDS whitelist
filtering in _merge_config, and create_device full chain with
DockerCreateConfig validation and {container_id}@{template_id} ID assembly.

Per phase 09 plan 02: structurally mirrors test_teclaw_facade.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.device_manage import (
    DockerCreateConfig,
    DockerCreationResult,
    DockerCredentials,
    DockerDeviceConfig,
)
from secbaas.api.template_manage import (
    DockerTemplateConfig,
    TemplateStatus,
)
from secbaas.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
    StandalonePaasService,
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
def mock_docker_service():
    """Create a mock StandalonePaasService with async methods."""
    mock = MagicMock()
    mock.create_device = AsyncMock()
    mock.get_credentials = AsyncMock()
    mock.get_platform_type = AsyncMock()
    return mock


@pytest.fixture
def mock_factory_service(facade, mock_docker_service):
    """Wire mock_docker_service as the facade factory's create() return value."""
    facade._factory.create.return_value = mock_docker_service
    return facade._factory


# ============================================================================
# Helpers
# ============================================================================


def make_docker_template(
    tenant="test-tenant",
    template_uuid="tpl-docker-001",
    template_id=50,
):
    """Create a mock DOCKER template response."""
    config = DockerTemplateConfig(
        type="DOCKER",
        image="alpine:latest",
        container_port=8080,
        memory_limit="512m",
    )
    template = MagicMock()
    template.id = 1
    template.template_id = template_id
    template.template_uuid = template_uuid
    template.type = "DOCKER"
    template.tenant = tenant
    template.name = "Docker Test Template"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    return template


# ============================================================================
# TestFacadeMergeConfigForDocker — _merge_config DOCKER whitelist filtering
# ============================================================================


class TestFacadeMergeConfigForDocker:
    """Test _merge_config DOCKER branch — whitelist filtering.

    Covers DOCKER_TEST-03: DOCKER-specific _DOCKER_ALLOWED_OVERRIDE_FIELDS
    whitelist behavior (allowed field retention, disallowed field filtering,
    template-only fallback, platform mismatch validation).
    """

    def test_docker_merge_retains_allowed_fields(self, facade):
        """DOCKER whitelist fields are retained in merged config.

        _DOCKER_ALLOWED_OVERRIDE_FIELDS = {container_port, cpu_limit,
        description, envs, image, memory_limit, name}.

        Detail overrides take precedence over template defaults.
        """
        template_config = DockerTemplateConfig(
            type="DOCKER",
            image="alpine:latest",
            container_port=8080,
            memory_limit="512m",
        )
        detail_config = DockerDeviceConfig(
            image="nginx:latest",
            container_port=3000,
            envs={"NODE_ENV": "prod"},
            cpu_limit=2.0,
            memory_limit="1g",
            name="my-device",
            description="test device",
        )
        merged = facade._merge_config(template_config, detail_config, "DOCKER")

        # All 7 allowed fields retained with detail values
        assert merged["image"] == "nginx:latest"
        assert merged["container_port"] == 3000
        assert merged["envs"] == {"NODE_ENV": "prod"}
        assert merged["cpu_limit"] == 2.0
        assert merged["memory_limit"] == "1g"
        assert merged["name"] == "my-device"
        assert merged["description"] == "test device"

        # Template-only fields NOT overridden by detail still present
        assert merged["type"] == "DOCKER"

    def test_docker_merge_template_only_no_detail(self, facade):
        """When detail_config is None, return template config as-is."""
        template_config = DockerTemplateConfig(
            type="DOCKER",
            image="alpine:latest",
            container_port=8080,
            memory_limit="512m",
        )
        merged = facade._merge_config(template_config, None, "DOCKER")

        assert merged["image"] == "alpine:latest"
        assert merged["container_port"] == 8080
        assert merged["type"] == "DOCKER"

    def test_docker_merge_platform_mismatch_raises(self, facade):
        """Non-DOCKER detail_config with DOCKER platform_type raises ValueError.

        The facade validates that detail_config type matches platform_type to
        catch misconfigurations early.
        """
        from secbaas.api.device_manage import LocalDeviceConfig

        template_config = DockerTemplateConfig(
            type="DOCKER",
            image="alpine:latest",
            container_port=8080,
            memory_limit="512m",
        )
        detail_config = LocalDeviceConfig(
            user_id="u1",
            machine_id="m1",
            tc_bot_id="b1",
            agent_code="ac",
        )

        with pytest.raises(ValueError, match="must be DockerDeviceConfig"):
            facade._merge_config(template_config, detail_config, "DOCKER")


# ============================================================================
# TestFacadeCreateDeviceForDocker — create_device DOCKER full chain
# ============================================================================


class TestFacadeCreateDeviceForDocker:
    """Test create_device DOCKER branch: config validation + ID assembly.

    Covers DOCKER_TEST-04: the full create_device chain for DOCKER:
    template resolution -> _merge_config -> DockerCreateConfig.model_validate
    -> service.create_device -> DockerCreationResult with
    {container_id}@{template_id}. Plus error-path (PaasError wrapping).
    """

    @pytest.mark.asyncio
    async def test_create_docker_device_happy_path(
        self, facade, mock_docker_service, mock_factory_service
    ):
        """create_device DOCKER branch: full chain with ID assembly.

        The facade suffixes container_id with @template_id after successful
        service.create_device. All other fields are preserved.
        """
        template = make_docker_template(template_id=42)
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_docker_service.get_credentials.return_value = DockerCredentials(
            template_id=42,
            template_uuid="tpl-docker-001",
            tenant_name="test-tenant",
        )
        mock_docker_service.get_platform_type.return_value = MagicMock(value="DOCKER")

        service_result = DockerCreationResult(
            platform="docker",
            container_id="abc123def456",
            host_port=32768,
            status="running",
        )
        mock_docker_service.create_device.return_value = service_result

        result = await facade.create_device(
            tenant_name="test-tenant",
            device_template_uuid="tpl-docker-001",
            detail_config=DockerDeviceConfig(
                image="alpine:latest",
                container_port=8080,
                memory_limit="512m",
            ),
        )

        assert isinstance(result, DockerCreationResult)
        # ID assembly: container_id should have @template_id suffix
        assert result.container_id == "abc123def456@42"
        # All other fields preserved from service result
        assert result.host_port == 32768
        assert result.platform == "docker"
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_create_docker_device_wraps_paas_error(
        self, facade, mock_docker_service, mock_factory_service
    ):
        """create_device wraps PaasError as DeviceFacadeException.

        When the underlying StandalonePaasService.create_device raises PaasError,
        the facade wraps it in DeviceFacadeException with operation,
        platform_type, and original_error preserved.
        """
        template = make_docker_template(template_id=42)
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        mock_docker_service.get_credentials.return_value = DockerCredentials(
            template_id=42,
            template_uuid="tpl-docker-001",
            tenant_name="test-tenant",
        )
        mock_docker_service.get_platform_type.return_value = MagicMock(value="DOCKER")

        paas_error = PaasError(ErrorCode.CONFIG_INVALID, "Image not found")
        mock_docker_service.create_device.side_effect = paas_error

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.create_device(
                tenant_name="test-tenant",
                detail_config=DockerDeviceConfig(
                    image="alpine:latest",
                    container_port=8080,
                    memory_limit="512m",
                ),
            )

        assert exc_info.value.operation == "create_device"
        assert exc_info.value.platform_type == "DOCKER"
        assert exc_info.value.original_error.code == ErrorCode.CONFIG_INVALID

"""Tests for Docker platform factory integration.

Tests for _create_docker_credentials_from_template() static method
and the DOCKER platform branch in PaasServiceFactory.create().

Post-Plan-11-05 refactoring: Factory uses DI plugin pattern —
self._paas_sandbox_plugins.docker_sandbox_plugin instead of
docker.from_env(). Tests mock the plugin directly, no @patch needed.
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.device_manage import DockerCredentials
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DockerTemplateConfig,
)
from secbaas.community.api.tenant_manage import ImagePullPolicy
from secbaas.community.core.service.paas import (
    PaasSandboxPlugins,
    StandalonePaasService,
)
from secbaas.community.core.service.paas._factory import PaasServiceFactory


def make_docker_template(
    tenant="test-tenant",
    template_uuid="tpl-docker-001",
    template_id=50,
    image="alpine:latest",
    container_port=8080,
    memory_limit="512m",
    image_pull_policy=ImagePullPolicy.IF_NOT_PRESENT,
    health_endpoint="/health",
    health_timeout_seconds=120,
):
    """Create a mock Docker template with DockerTemplateConfig."""
    config = DockerTemplateConfig(
        type="DOCKER",
        image=image,
        container_port=container_port,
        memory_limit=memory_limit,
        image_pull_policy=image_pull_policy,
        health_endpoint=health_endpoint,
        health_timeout_seconds=health_timeout_seconds,
    )
    template = MagicMock()
    template.template_id = template_id
    template.template_uuid = template_uuid
    template.tenant = tenant
    template.config = config
    return template


class TestDockerCredentialsFromTemplate:
    """Tests for _create_docker_credentials_from_template static method.

    This method is NOT modified in this phase — it still works exactly
    as before. Tests are preserved 100% unchanged.
    """

    def test_creates_docker_credentials_with_correct_fields(self):
        """Returns DockerCredentials with template_id, template_uuid, tenant_name
        matching the template object."""
        template = make_docker_template(
            tenant="my-tenant",
            template_uuid="uuid-test-001",
            template_id=42,
        )

        creds = PaasServiceFactory._create_docker_credentials_from_template(template)

        assert isinstance(creds, DockerCredentials)
        assert creds.template_id == 42
        assert creds.template_uuid == "uuid-test-001"
        assert creds.tenant_name == "my-tenant"

    def test_raises_value_error_when_config_is_none(self):
        """Raises ValueError with 'config is None' in message when config is None."""
        template = MagicMock()
        template.template_uuid = "tpl-001"
        template.config = None

        with pytest.raises(ValueError) as exc_info:
            PaasServiceFactory._create_docker_credentials_from_template(template)

        assert "config is None" in str(exc_info.value)

    def test_raises_value_error_when_config_is_not_docker_template_config(self):
        """Raises ValueError with 'expected DockerTemplateConfig' when config
        is a different platform config type."""
        wrong_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.example.com",
            api_key="test-key",
            template_id="tpl-arca-001",
        )
        template = MagicMock()
        template.template_uuid = "tpl-docker-wrong"
        template.template_id = 50
        template.tenant = "test-tenant"
        template.config = wrong_config

        with pytest.raises(ValueError) as exc_info:
            PaasServiceFactory._create_docker_credentials_from_template(template)

        assert "expected DockerTemplateConfig" in str(exc_info.value)


@pytest.fixture
def factory():
    """Create a PaasServiceFactory instance with all dependencies mocked.

    Post-Plan-11-05: Includes docker_sandbox_plugin mock in PaasSandboxPlugins.
    No docker.from_env() patching needed — the factory uses DI plugin directly.
    """
    from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
    from secbaas.community.plugins.sandbox.desktop import StubDesktopSandboxPlugin
    from secbaas.community.plugins.sandbox.teclaw import StubTeClawBotPlugin

    mock_docker_plugin = MagicMock()

    return PaasServiceFactory(
        template_service=MagicMock(),
        connection_manager=MagicMock(),
        worker_router=MagicMock(),
        instance_router=MagicMock(),
        device_template_repository=MagicMock(),
        device_repository=MagicMock(),
        publish_record_repository=MagicMock(),
        local_user_machine_repository=MagicMock(),
        paas_sandbox_plugins=PaasSandboxPlugins(
            arca_sandbox_plugin_factory=StubArcaSandboxPlugin,
            desktop_sandbox_plugin=StubDesktopSandboxPlugin(),
            teclaw_bot_plugin_factory=lambda endpoint, key_supplier, timeout: (
                StubTeClawBotPlugin()
            ),
            docker_sandbox_plugin=mock_docker_plugin,
        ),
        secret_plugin=MagicMock(),
    )


class TestDockerCreateBranch:
    """Tests for DOCKER platform branch in PaasServiceFactory.create().

    Post-Plan-11-05: Factory uses paas_sandbox_plugins.docker_sandbox_plugin
    instead of docker.from_env(). Tests inject mock plugin via factory fixture.
    No @patch decorators needed.
    """

    def test_create_returns_standalone_paas_service_for_docker_type(self, factory):
        """create() returns StandalonePaasService when template type is Docker."""
        template = make_docker_template(
            tenant="test-tenant",
            template_uuid="tpl-docker-001",
        )
        template.type = "Docker"

        service = factory.create(tenant_name="test-tenant", template=template)

        assert isinstance(service, StandalonePaasService)

    @pytest.mark.asyncio
    async def test_docker_credentials_are_docker_credentials_type(self, factory):
        """The credentials on the returned StandalonePaasService
        are DockerCredentials instances."""
        template = make_docker_template(
            tenant="test-tenant",
            template_uuid="tpl-docker-001",
        )
        template.type = "Docker"

        service = factory.create(tenant_name="test-tenant", template=template)

        creds = await service.get_credentials()
        assert isinstance(creds, DockerCredentials)
        assert creds.template_uuid == "tpl-docker-001"

    def test_create_passes_plugin_to_service(self, factory):
        """The factory injects the mock docker_sandbox_plugin into StandalonePaasService.
        This verifies the DI plugin pattern — the service receives the plugin
        that was configured in PaasSandboxPlugins, not a docker.from_env() client."""
        template = make_docker_template(
            tenant="test-tenant",
            template_uuid="tpl-docker-001",
        )
        template.type = "Docker"

        service = factory.create(tenant_name="test-tenant", template=template)

        # The service._plugin should be the same mock_docker_plugin from the
        # factory fixture's PaasSandboxPlugins
        assert service._plugin is factory._paas_sandbox_plugins.docker_sandbox_plugin

    def test_create_logs_plugin_type(self, factory):
        """Factory.create() succeeds with the mocked Docker plugin.
        Verifies the full DOCKER branch executes without error."""
        template = make_docker_template(
            tenant="test-tenant",
            template_uuid="tpl-docker-001",
            image="ubuntu:22.04",
            container_port=3000,
        )
        template.type = "Docker"

        service = factory.create(tenant_name="test-tenant", template=template)

        assert isinstance(service, StandalonePaasService)
        # Verify the template config fields were used for health params
        assert service._health_endpoint == "/health"
        assert service._health_timeout_seconds == 120

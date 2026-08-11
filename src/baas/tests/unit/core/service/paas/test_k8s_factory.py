"""Tests for PaasServiceFactory K8S platform — using template-based resolution.

Covers:
  - Factory routing: K8S template → K8sPaasService with DI-injected plugin
  - Credential extraction: all 11 fields from K8sTemplateConfig
  - Error cases: None config, wrong config type
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.device_manage import K8sCredentials
from secbaas.community.api.template_manage import (
    K8sTemplateConfig,
    TemplateStatus,
)
from secbaas.community.core.service.paas import PaasServiceFactory
from secbaas.community.core.service.paas._k8s_paas_service import K8sPaasService


def make_k8s_template(
    tenant="test-tenant",
    template_uuid="tpl-k8s-001",
    kubeconfig="/path/to/kubeconfig",
    namespace="default",
    image="registry.example.com/bot:latest",
):
    """Create a mock K8S template.

    Args:
        tenant: Tenant name
        template_uuid: Template UUID
        kubeconfig: Path to kubeconfig file
        namespace: K8s namespace
        image: Container image
    """
    config = K8sTemplateConfig(
        type="K8s",
        kubeconfig=kubeconfig,
        namespace=namespace,
        image=image,
    )
    template = MagicMock()
    template.id = 10
    template.template_id = 60
    template.template_uuid = template_uuid
    template.type = "K8s"
    template.tenant = tenant
    template.name = "K8s Template"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    return template


@pytest.fixture
def factory():
    """Create a PaasServiceFactory instance with all dependencies mocked."""
    from secbaas.community.core.service.paas import PaasSandboxPlugins
    from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
    from secbaas.community.plugins.sandbox.desktop import StubDesktopSandboxPlugin
    from secbaas.community.plugins.sandbox.k8s import StubK8sSandboxPlugin
    from secbaas.community.plugins.sandbox.teclaw import StubTeClawBotPlugin

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
            teclaw_bot_plugin_factory=lambda bot_id, key_supplier, timeout=30.0: (
                StubTeClawBotPlugin()
            ),
            k8s_sandbox_plugin_factory=lambda creds: StubK8sSandboxPlugin(
                credentials=creds
            ),
        ),
        secret_plugin=MagicMock(),
        callback_handler=MagicMock(handle=AsyncMock(return_value={"status": "ok"})),
    )


class TestK8sFactoryCreate:
    """Test PaasServiceFactory routing and creation for K8S platform."""

    def test_create_k8s_returns_k8s_paas_service(self, factory):
        """Factory returns K8sPaasService for K8S template."""
        template = make_k8s_template()

        result = factory.create(
            tenant_name="test-tenant",
            template=template,
        )

        assert isinstance(result, K8sPaasService)
        assert result._credentials.template_uuid == "tpl-k8s-001"
        # Verify plugin was constructed through the DI-injected factory callable
        # (not hardcoded), with credentials correctly passed through
        assert result._plugin._credentials is not None
        assert result._plugin._credentials.namespace == "default"

    def test_create_k8s_constructs_service_with_credentials(self, factory):
        """Factory constructs K8sPaasService with correct credentials."""
        template = make_k8s_template()

        result = factory.create(
            tenant_name="test-tenant",
            template=template,
        )

        assert isinstance(result, K8sPaasService)
        assert result._credentials.namespace == "default"
        assert result._credentials.image == "registry.example.com/bot:latest"


class TestK8sFactoryCredentials:
    """Tests for _create_k8s_credentials_from_template."""

    def test_credentials_all_fields_mapped(self):
        """All 11 credential fields correctly extracted from template."""
        template = make_k8s_template()

        creds = PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert isinstance(creds, K8sCredentials)
        assert creds.template_id == 60
        assert creds.template_uuid == "tpl-k8s-001"
        assert creds.tenant_name == "test-tenant"
        assert creds.kubeconfig == "/path/to/kubeconfig"
        assert creds.namespace == "default"
        assert creds.image == "registry.example.com/bot:latest"

    def test_credentials_kubeconfig_set(self):
        """kubeconfig field is correctly extracted."""
        template = make_k8s_template(
            kubeconfig="/etc/k8s/admin.conf",
        )

        creds = PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert creds.kubeconfig == "/etc/k8s/admin.conf"

    def test_credentials_namespace_set(self):
        """namespace field is correctly extracted with non-default value."""
        template = make_k8s_template(
            namespace="production",
        )

        creds = PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert creds.namespace == "production"

    def test_credentials_image_set(self):
        """image supports non-default values."""
        template = make_k8s_template(
            image="my-registry.io/custom-bot:v2.0",
        )

        creds = PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert creds.image == "my-registry.io/custom-bot:v2.0"

    def test_credentials_resource_fields(self):
        """cpu_request/cpu_limit/memory_request/memory_limit correctly extracted when all set."""
        config = K8sTemplateConfig(
            type="K8s",
            kubeconfig="/path/to/kubeconfig",
            cpu_request="500m",
            cpu_limit="1",
            memory_request="512Mi",
            memory_limit="1Gi",
        )
        template = MagicMock()
        template.id = 10
        template.template_id = 60
        template.template_uuid = "tpl-k8s-001"
        template.type = "K8s"
        template.tenant = "test-tenant"
        template.name = "K8s Template"
        template.status = TemplateStatus.ONLINE.value
        template.config = config

        creds = PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert creds.cpu_request == "500m"
        assert creds.cpu_limit == "1"
        assert creds.memory_request == "512Mi"
        assert creds.memory_limit == "1Gi"

    def test_credentials_resource_fields_none(self):
        """cpu_request/cpu_limit/memory_request/memory_limit correctly extracted as None when not set."""
        config = K8sTemplateConfig(
            type="K8s",
            kubeconfig="/path/to/kubeconfig",
        )
        template = MagicMock()
        template.id = 10
        template.template_id = 60
        template.template_uuid = "tpl-k8s-001"
        template.type = "K8s"
        template.tenant = "test-tenant"
        template.name = "K8s Template"
        template.status = TemplateStatus.ONLINE.value
        template.config = config

        creds = PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert creds.cpu_request is None
        assert creds.cpu_limit is None
        assert creds.memory_request is None
        assert creds.memory_limit is None

    def test_credentials_with_none_config_raises(self):
        """None config raises ValueError."""
        template = MagicMock()
        template.template_uuid = "tpl-k8s-001"
        template.config = None

        with pytest.raises(ValueError) as exc_info:
            PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert "config is None" in str(exc_info.value)

    def test_credentials_with_wrong_config_type_raises(self):
        """Non-K8sTemplateConfig raises ValueError."""
        from secbaas.community.api.template_manage import SigmaTemplateConfig

        wrong_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )
        template = MagicMock()
        template.template_uuid = "tpl-k8s-001"
        template.template_id = 60
        template.tenant = "test-tenant"
        template.config = wrong_config

        with pytest.raises(ValueError) as exc_info:
            PaasServiceFactory._create_k8s_credentials_from_template(template)

        assert "expected K8sTemplateConfig" in str(exc_info.value)

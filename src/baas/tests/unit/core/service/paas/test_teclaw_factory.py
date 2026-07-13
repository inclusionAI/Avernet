"""Tests for PaasServiceFactory TeClaw platform — using template-based resolution.

Covers:
  - Factory routing: TECLAW template → TeClawPaasService
  - Credential extraction: teclaw_endpoint, template_id, template_uuid, tenant_name
  - Error cases: None config, wrong config type
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.device_manage import TeClawCredentials
from secbaas.community.api.template_manage import (
    TeClawTemplateConfig,
    TemplateStatus,
)
from secbaas.community.core.service.paas import PaasServiceFactory, TeClawPaasService


def make_teclaw_template(
    tenant="test-tenant",
    template_uuid="tpl-teclaw-001",
    teclaw_endpoint="http://teclaw.test:8080",
):
    """Create a mock TeClaw template.

    Args:
        tenant: Tenant name
        template_uuid: Template UUID
        teclaw_endpoint: TeClaw API endpoint URL
    """
    config = TeClawTemplateConfig(
        type="TECLAW",
        teclaw_endpoint=teclaw_endpoint,
    )
    template = MagicMock()
    template.id = 10
    template.template_id = 60
    template.template_uuid = template_uuid
    template.type = "TECLAW"
    template.tenant = tenant
    template.name = "TeClaw Template"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    return template


@pytest.fixture
def factory():
    """Create a PaasServiceFactory instance with all dependencies mocked."""
    from secbaas.community.core.service.paas._factory import PaasSandboxPlugins
    from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
    from secbaas.community.plugins.sandbox.desktop import StubDesktopSandboxPlugin
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
            teclaw_bot_plugin_factory=lambda endpoint, timeout: StubTeClawBotPlugin(),
        ),
        secret_plugin=MagicMock(),
    )


class TestTeClawFactoryCreate:
    """Test PaasServiceFactory routing and creation for TECLAW platform."""

    def test_create_teclaw_with_template(self, factory):
        """Factory creates TeClawPaasService for TECLAW template."""
        template = make_teclaw_template()

        service = factory.create(
            tenant_name="test-tenant",
            template=template,
        )
        assert isinstance(service, TeClawPaasService)

    def test_create_teclaw_extracts_credentials_from_template(self, factory):
        """Factory correctly extracts TeClawCredentials from template config."""
        template = make_teclaw_template()

        service = factory.create(tenant_name="test-tenant", template=template)

        assert isinstance(service, TeClawPaasService)
        credentials = service._credentials
        assert isinstance(credentials, TeClawCredentials)
        assert credentials.template_id == 60
        assert credentials.template_uuid == "tpl-teclaw-001"
        assert credentials.tenant_name == "test-tenant"
        assert credentials.teclaw_endpoint == "http://teclaw.test:8080"

    def test_create_teclaw_raises_on_none_config(self, factory):
        """None config raises ValueError."""
        template = MagicMock()
        template.template_uuid = "tpl-teclaw-001"
        template.tenant = "test-tenant"
        template.config = None
        template.type = "TECLAW"

        with pytest.raises(ValueError) as exc_info:
            factory.create(tenant_name="test-tenant", template=template)

        assert "config is None" in str(exc_info.value)

    def test_create_teclaw_raises_on_wrong_config_type(self, factory):
        """Non-TeClawTemplateConfig raises ValueError."""
        from secbaas.community.api.template_manage import SigmaTemplateConfig

        wrong_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )
        template = MagicMock()
        template.template_uuid = "tpl-teclaw-001"
        template.template_id = 60
        template.tenant = "test-tenant"
        template.type = "TECLAW"
        template.config = wrong_config

        with pytest.raises(ValueError) as exc_info:
            factory.create(tenant_name="test-tenant", template=template)

        assert "expected TeClawTemplateConfig" in str(exc_info.value)


class TestTeClawFactoryCredentials:
    """Tests for _create_teclaw_credentials_from_template edge cases."""

    def test_credentials_all_fields_mapped(self, factory):
        """All 4 credential fields correctly extracted from template."""
        template = make_teclaw_template()

        creds = factory._create_teclaw_credentials_from_template(template)

        assert isinstance(creds, TeClawCredentials)
        assert creds.template_id == 60
        assert creds.template_uuid == "tpl-teclaw-001"
        assert creds.tenant_name == "test-tenant"
        assert creds.teclaw_endpoint == "http://teclaw.test:8080"

    def test_teclaw_endpoint_set(self, factory):
        """TeClaw endpoint is correctly extracted from config."""
        template = make_teclaw_template(
            teclaw_endpoint="https://real.teclaw.com/api",
        )

        creds = factory._create_teclaw_credentials_from_template(template)

        assert creds.teclaw_endpoint == "https://real.teclaw.com/api"

    def test_tenant_name_preserved(self, factory):
        """Tenant name is correctly preserved from template."""
        template = make_teclaw_template(tenant="custom-tenant")

        creds = factory._create_teclaw_credentials_from_template(template)

        assert creds.tenant_name == "custom-tenant"

    def test_template_uuid_preserved(self, factory):
        """Template UUID is correctly preserved from template."""
        template = make_teclaw_template(template_uuid="tpl-custom-uuid")

        creds = factory._create_teclaw_credentials_from_template(template)

        assert creds.template_uuid == "tpl-custom-uuid"

    def test_credentials_with_none_config_raises(self, factory):
        """None config raises ValueError."""
        template = MagicMock()
        template.template_uuid = "tpl-teclaw-001"
        template.config = None

        with pytest.raises(ValueError) as exc_info:
            factory._create_teclaw_credentials_from_template(template)

        assert "config is None" in str(exc_info.value)

    def test_credentials_with_wrong_config_type_raises(self, factory):
        """Non-TeClawTemplateConfig raises ValueError."""
        from secbaas.community.api.template_manage import SigmaTemplateConfig

        wrong_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )
        template = MagicMock()
        template.template_uuid = "tpl-teclaw-001"
        template.template_id = 60
        template.tenant = "test-tenant"
        template.config = wrong_config

        with pytest.raises(ValueError) as exc_info:
            factory._create_teclaw_credentials_from_template(template)

        assert "expected TeClawTemplateConfig" in str(exc_info.value)

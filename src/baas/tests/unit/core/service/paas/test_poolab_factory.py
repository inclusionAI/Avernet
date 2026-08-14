"""Tests for PaasServiceFactory Poolab platform — using template-based resolution.

Covers:
  - Factory routing: POOLAB template → PoolabPaasService
  - Credential extraction: SM4 decryption, env-aware endpoint/image_id, error cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.device_manage import PoolabCredentials
from secbaas.community.api.template_manage import (
    PoolabTemplateConfig,
    TemplateStatus,
)
from secbaas.community.core.service.paas import PaasServiceFactory, PoolabPaasService


def make_poolab_template(
    tenant="test-tenant",
    template_uuid="tpl-poolab-001",
    poolab_endpoint_pre="https://poolab-pre.example.com",
    poolab_endpoint_prod="https://poolab-prod.example.com",
    poolab_tenant_id="tenant-abc",
    poolab_tenant_token="token-abc",
    encrypt_tenant_token=False,
    poolab_default_image_id_pre="img-pre-001",
    poolab_default_image_id_prod="img-prod-001",
):
    """Create a mock Poolab template.

    Args:
        tenant: Tenant name
        template_uuid: Template UUID
        poolab_endpoint_pre: Pre-environment Poolab endpoint
        poolab_endpoint_prod: Prod-environment Poolab endpoint
        poolab_tenant_id: Poolab tenant ID
        poolab_tenant_token: Poolab tenant token
        encrypt_tenant_token: Whether token should be SM4 encrypted
        poolab_default_image_id_pre: Pre-environment default image ID
        poolab_default_image_id_prod: Prod-environment default image ID
    """
    config = PoolabTemplateConfig(
        type="POOLAB",
        poolab_endpoint_pre=poolab_endpoint_pre,
        poolab_endpoint_prod=poolab_endpoint_prod,
        poolab_tenant_id=poolab_tenant_id,
        poolab_tenant_token=poolab_tenant_token,
        encrypt_tenant_token=encrypt_tenant_token,
        poolab_default_image_id_pre=poolab_default_image_id_pre,
        poolab_default_image_id_prod=poolab_default_image_id_prod,
    )
    template = MagicMock()
    template.id = 10
    template.template_id = 60
    template.template_uuid = template_uuid
    template.type = "POOLAB"
    template.tenant = tenant
    template.name = "Poolab Template"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    return template


@pytest.fixture
def factory():
    """Create a PaasServiceFactory instance with all dependencies mocked."""
    from secbaas.community.core.service.paas import PaasSandboxPlugins
    from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
    from secbaas.community.plugins.sandbox.desktop import StubDesktopSandboxPlugin
    from secbaas.community.plugins.sandbox.poolab import StubPoolabSandboxPlugin
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
            teclaw_bot_plugin_factory=lambda endpoint, key_supplier, timeout: (
                StubTeClawBotPlugin()
            ),
            poolab_sandbox_plugin_factory=StubPoolabSandboxPlugin,
        ),
        secret_plugin=MagicMock(),
        callback_handler=MagicMock(handle=AsyncMock(return_value={"status": "ok"})),
    )


class TestPoolabFactoryCreate:
    """Test PaasServiceFactory routing and creation for POOLAB platform."""

    def test_create_poolab_with_template(self, factory):
        """Factory creates PoolabPaasService for POOLAB template."""
        template = make_poolab_template()

        service = factory.create(
            tenant_name="test-tenant",
            template=template,
        )
        assert isinstance(service, PoolabPaasService)

    @patch("secbaas.community.core.service.paas._factory.get_current_env")
    @pytest.mark.asyncio
    async def test_create_poolab_extracts_credentials_from_template(
        self, mock_get_env, factory
    ):
        """Factory correctly extracts PoolabCredentials from template config."""
        mock_get_env.return_value = "dev"
        template = make_poolab_template()

        service = factory.create(tenant_name="test-tenant", template=template)

        assert isinstance(service, PoolabPaasService)
        credentials = await service.get_credentials()
        assert isinstance(credentials, PoolabCredentials)
        assert credentials.template_id == 60


class TestPoolabCredentialsFromTemplate:
    """Tests for _create_poolab_credentials_from_template edge cases."""

    @patch("secbaas.community.core.service.paas._factory.get_current_env")
    @patch("secbaas.community.core.utils.secret_utils.common_sm4_decrypt")
    def test_poolab_credentials_with_encrypted_tenant_token(
        self, mock_decrypt, mock_get_env, factory
    ):
        """When encrypt_tenant_token is True, token is decrypted via SM4."""
        mock_decrypt.return_value = "decrypted-token"
        mock_get_env.return_value = "dev"

        template = make_poolab_template(
            poolab_tenant_token="encrypted-token-value",
            encrypt_tenant_token=True,
        )

        creds = factory._create_poolab_credentials_from_template(template)

        mock_decrypt.assert_called_once()
        assert mock_decrypt.call_args[0][0] == "encrypted-token-value"
        assert creds.poolab_tenant_token == "decrypted-token"

    @patch("secbaas.community.core.service.paas._factory.get_current_env")
    def test_poolab_credentials_with_plaintext_token(self, mock_get_env, factory):
        """When encrypt_tenant_token is False, token is used as-is."""
        mock_get_env.return_value = "dev"

        template = make_poolab_template(
            poolab_tenant_token="plaintext-token",
            encrypt_tenant_token=False,
        )

        creds = factory._create_poolab_credentials_from_template(template)

        assert creds.poolab_tenant_token == "plaintext-token"

    @patch("secbaas.community.core.service.paas._factory.get_current_env")
    def test_poolab_credentials_env_aware_pre(self, mock_get_env, factory):
        """Pre environment selects pre-specific endpoint and image ID."""
        mock_get_env.return_value = "pre"

        template = make_poolab_template(
            poolab_endpoint_pre="https://poolab-pre.example.com",
            poolab_endpoint_prod="https://poolab-prod.example.com",
            poolab_default_image_id_pre="img-pre-001",
            poolab_default_image_id_prod="img-prod-001",
        )

        creds = factory._create_poolab_credentials_from_template(template)

        assert creds.poolab_endpoint == "https://poolab-pre.example.com"
        assert creds.poolab_image_id == "img-pre-001"

    @patch("secbaas.community.core.service.paas._factory.get_current_env")
    def test_poolab_credentials_env_aware_prod(self, mock_get_env, factory):
        """Prod environment selects prod-specific endpoint and image ID."""
        mock_get_env.return_value = "prod"

        template = make_poolab_template(
            poolab_endpoint_pre="https://poolab-pre.example.com",
            poolab_endpoint_prod="https://poolab-prod.example.com",
            poolab_default_image_id_pre="img-pre-001",
            poolab_default_image_id_prod="img-prod-001",
        )

        creds = factory._create_poolab_credentials_from_template(template)

        assert creds.poolab_endpoint == "https://poolab-prod.example.com"
        assert creds.poolab_image_id == "img-prod-001"

    def test_poolab_credentials_with_none_config_raises(self, factory):
        """None config raises ValueError."""
        template = MagicMock()
        template.template_uuid = "tpl-poolab-001"
        template.config = None

        with pytest.raises(ValueError) as exc_info:
            factory._create_poolab_credentials_from_template(template)

        assert "config is None" in str(exc_info.value)

    def test_poolab_credentials_with_wrong_config_type_raises(self, factory):
        """Non-PoolabTemplateConfig raises ValueError."""
        from secbaas.community.api.template_manage import SigmaTemplateConfig

        wrong_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )
        template = MagicMock()
        template.template_uuid = "tpl-poolab-001"
        template.template_id = 60
        template.tenant = "test-tenant"
        template.config = wrong_config

        with pytest.raises(ValueError) as exc_info:
            factory._create_poolab_credentials_from_template(template)

        assert "expected PoolabTemplateConfig" in str(exc_info.value)

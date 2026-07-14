"""Tests for SigmaPaasService stub implementation."""

import pytest

from secbaas.community.api.device_manage import SigmaCreateConfig, SigmaCredentials
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas import (
    ErrorCode,
    PaasError,
    PaasService,
    SigmaPaasService,
)


@pytest.fixture
def sigma_credentials():
    """Create test Sigma credentials."""
    return SigmaCredentials(
        endpoint="http://sigma.test:8080",
        access_key="test-access-key",
        secret_key="test-secret-key",
        template_id=1,
        template_uuid="tpl-test-001",
    )


class TestSigmaPaasService:
    """Test SigmaPaasService stub implementation."""

    def test_inherits_from_paas_service(self):
        """SigmaPaasService implements PaasService ABC."""
        assert issubclass(SigmaPaasService, PaasService)

    @pytest.mark.asyncio
    async def test_create_device_raises_paas_error(self, sigma_credentials):
        """create_device raises PaasError indicating not implemented."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(PaasError) as exc_info:
            await service.create_device(config=SigmaCreateConfig())

        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED
        assert "not yet implemented" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_destroy_device_raises_paas_error(self, sigma_credentials):
        """destroy_device raises PaasError indicating not implemented."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(PaasError) as exc_info:
            await service.destroy_device("device-123")

        assert exc_info.value.code == ErrorCode.DEVICE_DESTROY_FAILED
        assert "not yet implemented" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_execute_command_raises_paas_error(self, sigma_credentials):
        """execute_command raises PaasError indicating not implemented."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(PaasError) as exc_info:
            await service.execute_command(
                "device-123", "echo hello", env={"KEY": "value"}
            )

        assert exc_info.value.code == ErrorCode.COMMAND_FAILED
        assert "not yet implemented" in exc_info.value.message.lower()

    def test_can_instantiate(self, sigma_credentials):
        """SigmaPaasService can be instantiated (unlike abstract ABC)."""
        service = SigmaPaasService(credentials=sigma_credentials)
        assert isinstance(service, SigmaPaasService)

    @pytest.mark.asyncio
    async def test_invoke_http_in_device_raises_not_implemented(
        self, sigma_credentials
    ):
        """invoke_http_in_device raises NotImplementedError per D-01."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(NotImplementedError) as exc_info:
            await service.invoke_http_in_device(
                paas_device_id="sigma-device",
                method="GET",
                port=8080,
                path="/api/health",
                query_string=None,
                headers={},
                body=b"",
            )

        assert "does not support HTTP invocation" in str(exc_info.value)

    def test_credentials_None_raises_NotImplementedError(self):
        """Constructor with None credentials raises NotImplementedError."""
        with pytest.raises(NotImplementedError) as exc_info:
            SigmaPaasService(credentials=None)

        assert "not yet implemented" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_get_credentials_returns_stored_credentials(self, sigma_credentials):
        """get_credentials returns the SigmaCredentials passed to constructor."""
        service = SigmaPaasService(credentials=sigma_credentials)

        result = await service.get_credentials()

        assert result == sigma_credentials
        assert result.endpoint == "http://sigma.test:8080"
        assert result.access_key == "test-access-key"

    @pytest.mark.asyncio
    async def test_get_platform_type_returns_SIGMA(self, sigma_credentials):
        """get_platform_type returns TenantType.SIGMA."""
        service = SigmaPaasService(credentials=sigma_credentials)

        result = await service.get_platform_type()

        assert result == TenantType.SIGMA

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_raises_NotImplementedError(
        self, sigma_credentials
    ):
        """resolve_ws_conn_info raises NotImplementedError for Sigma platform."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(NotImplementedError) as exc_info:
            await service.resolve_ws_conn_info(
                paas_device_id="sigma-device",
                port=8080,
                path="/ws",
            )

        assert "not yet implemented" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_get_device_info_raises_PaasError_with_DEVICE_NOT_FOUND(
        self, sigma_credentials
    ):
        """get_device_info raises PaasError with DEVICE_NOT_FOUND."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(PaasError) as exc_info:
            await service.get_device_info("sigma-device")

        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND
        assert "not yet implemented" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_update_outbound_operation_rule_raises_PaasError_with_DEVICE_UNAVAILABLE(
        self, sigma_credentials
    ):
        """update_outbound_operation_rule raises PaasError with DEVICE_UNAVAILABLE."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(PaasError) as exc_info:
            await service.update_outbound_operation_rule(
                paas_device_id="sigma-device",
                outbound_operation_rule={},
            )

        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE
        assert "not yet implemented" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_restart_device_raises_NotImplementedError(self, sigma_credentials):
        """restart_device raises NotImplementedError for Sigma platform."""
        service = SigmaPaasService(credentials=sigma_credentials)

        with pytest.raises(NotImplementedError) as exc_info:
            await service.restart_device("sigma-device")

        assert "restart_device not yet implemented" in str(exc_info.value).lower()

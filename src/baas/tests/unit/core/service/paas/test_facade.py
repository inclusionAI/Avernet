"""Unit tests for PaasServiceFacade with mocked dependencies."""

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.device_manage import (
    ArcaCreateConfig,
    ArcaCreationResult,
    ArcaDeviceConfig,
    CommandResult,
    LocalCreateConfig,
    LocalCreationResult,
    LocalDeviceConfig,
    SigmaDeviceConfig,
)
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    LocalTemplateConfig,
    SigmaTemplateConfig,
    TemplateStatus,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
)


@pytest.fixture
def facade():
    """Create a fresh PaasServiceFacade instance with mocked dependencies."""
    mock_template_svc = MagicMock()
    mock_device_repo = MagicMock()
    mock_factory = MagicMock()
    return PaasServiceFacade(
        device_repository=mock_device_repo,
        device_template_service=mock_template_svc,
        factory=mock_factory,
    )


@pytest.fixture
def arca_device_config():
    """Create a test ArcaDeviceConfig."""
    return ArcaDeviceConfig(
        arca_template_id="template-123",
        ttl_in_minutes=60,
        name="test-device",
        description="Test device",
    )


@pytest.fixture
def mock_service():
    """Create a mock PaasService with async methods."""
    from secbaas.community.api.bot_runtime import WsConnectionInfo

    mock = MagicMock()
    # Set up async methods that can be awaited
    mock.create_device = AsyncMock()
    mock.destroy_device = AsyncMock()
    mock.execute_command = AsyncMock()
    # get_credentials() is now await in facade, needs AsyncMock
    mock.get_credentials = AsyncMock()
    mock.get_platform_type = AsyncMock()
    # Set up resolve_ws_conn_info to return a proper WsConnectionInfo
    mock.resolve_ws_conn_info = AsyncMock(
        return_value=WsConnectionInfo(
            ws_url="wss://proxy.example.com/proxypass/ARCA_sandbox-abc123@42:20003/api/openclaw/ws",
            token="jwt-token-xyz",
            target="ARCA_sandbox-abc123@42:20003",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    invoke_http_mock = AsyncMock()
    mock.invoke_http_in_device = invoke_http_mock
    mock.get_device_info = AsyncMock()
    mock.update_outbound_operation_rule = AsyncMock()
    mock.update_device_ttl = AsyncMock()
    mock.restart_device = AsyncMock()
    return mock


@pytest.fixture
def mock_template_response():
    """Create mock template response for DefaultDeviceTemplateService."""
    return make_mock_template()


def make_mock_template(
    tenant: str = "test-tenant",
    template_uuid: str = "test-template-uuid",
    template_id: int = 42,
    platform_type: str = "ARCA",
) -> MagicMock:
    """Create a mock template response (callable, not a fixture)."""
    if platform_type.upper() == "ARCA":
        config: ArcaTemplateConfig | SigmaTemplateConfig = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.example.com",
            api_key="template-api-key",
            template_id="tpl-123",
            oss_mount_id="mount-456",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
        )
    else:
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )

    template = MagicMock()
    template.id = 1
    template.template_id = template_id
    template.template_uuid = template_uuid
    template.type = platform_type
    template.tenant = tenant
    template.name = "Test Template"
    template.description = "Test template description"
    template.status = TemplateStatus.ONLINE.value
    template.config = config
    template.creator = "test-user"
    template.modifier = "test-user"
    template.gmt_create = datetime.now(UTC)
    template.gmt_modified = datetime.now(UTC)
    return template


def make_mock_local_template(
    tenant: str = "test-tenant",
    template_uuid: str = "test-local-template-uuid",
    template_id: int = 42,
) -> MagicMock:
    """Create a mock Local template response."""
    # Local templates have a real LocalTemplateConfig for model_dump compatibility
    config = LocalTemplateConfig(type="LOCAL")

    template = MagicMock()
    template.id = 2
    template.template_id = template_id
    template.template_uuid = template_uuid
    template.type = "LOCAL"
    template.tenant = tenant
    template.name = "Local Test Template"
    template.description = "Local template for testing"
    template.status = TemplateStatus.ONLINE.value
    template.config = config  # Real config, not a mock
    template.creator = "test-user"
    template.modifier = "test-user"
    template.gmt_create = datetime.now(UTC)
    template.gmt_modified = datetime.now(UTC)
    return template


@pytest.fixture
def detail_config():
    """Create a test detail_config for overriding template."""
    return ArcaDeviceConfig(
        # Only override fields - no template_id needed in detail_config
        ttl_in_minutes=120,  # Override template default (10080)
        name="override-device-name",
        description="Override description",
    )


@pytest.mark.unit
class TestParseDeviceId:
    """Test device ID suffix parsing (Decision D-05)."""

    def test_parse_with_suffix(self, facade):
        """Parse device ID with @template_id suffix."""
        device_id, template_id = facade._parse_device_id("sandbox-abc123@42")

        assert device_id == "sandbox-abc123"
        assert template_id == 42

    def test_parse_with_different_tenant(self, facade):
        """Parse device ID with different tenant values."""
        device_id, template_id = facade._parse_device_id("device-xyz@123")

        assert device_id == "device-xyz"
        assert template_id == 123

    def test_parse_without_suffix_backward_compat(self, facade):
        """Backward compatibility: no suffix means template_id=0."""
        device_id, template_id = facade._parse_device_id("legacy-device")

        assert device_id == "legacy-device"
        assert template_id == 0

    def test_parse_empty_suffix_invalid(self, facade):
        """Invalid suffix (non-numeric) returns full string as device_id."""
        device_id, template_id = facade._parse_device_id("device@invalid")

        assert device_id == "device@invalid"
        assert template_id == 0

    def test_parse_multiple_at_symbols(self, facade):
        """Multiple @ symbols: split from rightmost @."""
        device_id, template_id = facade._parse_device_id("user@host@42")

        assert device_id == "user@host"
        assert template_id == 42

    def test_parse_zero_tenant(self, facade):
        """Explicit template_id=0 in suffix."""
        device_id, template_id = facade._parse_device_id("device@0")

        assert device_id == "device"
        assert template_id == 0

    def test_parse_negative_tenant_invalid(self, facade):
        """Negative template_id treated as valid int (implementation detail)."""
        device_id, template_id = facade._parse_device_id("device@-1")

        # Negative is technically parsable as int, but treated as valid
        # per current implementation (just int conversion)
        assert device_id == "device"
        assert template_id == -1


@pytest.mark.unit
class TestCreateDevice:
    """Test create_device with new signature (per D-03, D-04, D-05)."""

    @pytest.mark.asyncio
    async def test_create_device_success_with_explicit_template(
        self, facade, mock_service, mock_template_response
    ):
        """create_device with explicit device_template_uuid returns sandbox_id with suffix."""
        # Set return value on the facade's injected template service mock
        facade._device_template_service.get_default_or_explicit_template.return_value = mock_template_response

        mock_service.get_credentials.return_value.template_id = 42
        facade._factory.create.return_value = mock_service

        mock_result = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="tpl-123",
            sandbox_id="sandbox-abc123",
        )
        mock_service.create_device.return_value = mock_result

        result = await facade.create_device(
            tenant_name="test-tenant",
            device_template_uuid="explicit-template-uuid",
            detail_config=None,
        )

        assert isinstance(result, ArcaCreationResult)
        assert result.sandbox_id == "sandbox-abc123@42"

        # Verify template resolution with explicit UUID
        facade._device_template_service.get_default_or_explicit_template.assert_called_once_with(
            tenant="test-tenant", template_uuid="explicit-template-uuid"
        )

        # Verify Factory called with resolved template
        facade._factory.create.assert_called_once_with(
            tenant_name="test-tenant",
            template_uuid="test-template-uuid",
            template=mock_template_response,
        )

    @pytest.mark.asyncio
    async def test_create_device_uses_default_template_when_uuid_not_provided(
        self, facade, mock_service, mock_template_response
    ):
        """create_device falls back to default template when device_template_uuid is None (D-02)."""
        facade._device_template_service.get_default_or_explicit_template.return_value = mock_template_response

        mock_service.get_credentials.return_value.template_id = 42
        facade._factory.create.return_value = mock_service

        mock_result = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="tpl-123",
            sandbox_id="sandbox-default",
        )
        mock_service.create_device.return_value = mock_result

        result = await facade.create_device(tenant_name="test-tenant")

        assert result.sandbox_id == "sandbox-default@42"

        # Service should resolve template with None template_uuid
        facade._device_template_service.get_default_or_explicit_template.assert_called_once_with(
            tenant="test-tenant", template_uuid=None
        )

    @pytest.mark.asyncio
    async def test_create_device_wraps_paas_error_new_signature(
        self, facade, mock_service, mock_template_response
    ):
        """create_device wraps PaasError as DeviceFacadeException (D-06)."""
        facade._device_template_service.get_default_or_explicit_template.return_value = mock_template_response

        mock_service.get_credentials.return_value.template_id = 42
        facade._factory.create.return_value = mock_service

        paas_error = PaasError(ErrorCode.DEVICE_CREATION_FAILED, "Creation failed")
        mock_service.create_device.side_effect = paas_error

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="test-uuid",
            )

        exception = exc_info.value
        assert exception.operation == "create_device"
        assert exception.platform_type == "ARCA"
        assert exception.template_id == 42
        assert exception.paas_device_id is None
        assert exception.original_error == paas_error

    @pytest.mark.asyncio
    async def test_create_device_validates_tenant_name_required(self, facade):
        """create_device raises ValueError when tenant_name is empty or None (D-04)."""
        with pytest.raises(ValueError) as exc_info:
            await facade.create_device(tenant_name="")
        assert "tenant_name" in str(exc_info.value).lower()

        with pytest.raises(ValueError) as exc_info:
            await facade.create_device(tenant_name=None)
        assert "tenant_name" in str(exc_info.value).lower()


@pytest.mark.unit
class TestConfigMerge:
    """Test config merge logic: detail_config > template.config (Decision D-01)."""

    def test_merge_config_returns_template_config_when_no_detail(self, facade):
        """When detail_config is None, return template config as-is."""
        template_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://template.example.com",
            api_key="template-key",
            template_id="tpl-123",
            oss_mount_id="mount-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
        )

        merged = facade._merge_config(template_config, None, "ARCA")

        assert merged["base_url"] == "https://template.example.com"
        assert merged["api_key"] == "template-key"
        assert merged["template_id"] == "tpl-123"
        assert merged["oss_mount_id"] == "mount-123"

    def test_merge_config_detail_overrides_template(self, facade):
        """detail_config fields override template.config fields (D-01)."""
        template_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://template.example.com",
            api_key="template-key",
            template_id="tpl-123",
            oss_mount_id=None,
            arca_template_id_pre=None,
            arca_template_id_prod=None,
        )

        detail_config = ArcaDeviceConfig(
            arca_template_id="detail-tpl-456",  # Override
            ttl_in_minutes=120,  # New field not in template
            name="detail-name",  # New field not in template
        )

        merged = facade._merge_config(template_config, detail_config, "ARCA")

        # Template values used when not overridden
        assert merged["base_url"] == "https://template.example.com"
        assert merged["api_key"] == "template-key"

        # Detail overrides
        assert merged["template_id"] == "detail-tpl-456"
        assert merged["ttl_in_minutes"] == 120
        assert merged["name"] == "detail-name"

    def test_merge_config_raises_on_platform_mismatch(self, facade):
        """ValueError raised when detail_config type doesn't match platform."""
        template_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.example.com",
            api_key="key",
            template_id="tpl-test",
            oss_mount_id=None,
            arca_template_id_pre=None,
            arca_template_id_prod=None,
        )

        detail_config = ArcaDeviceConfig(arca_template_id="test")

        # Mismatch: detail is ARCA type but platform_type says SIGMA
        with pytest.raises(ValueError) as exc_info:
            facade._merge_config(template_config, detail_config, "SIGMA")

        assert (
            "platform" in str(exc_info.value).lower()
            or "type" in str(exc_info.value).lower()
        )

    def test_merge_config_empty_template_config(self, facade):
        """When template config is None, use detail_config only."""
        detail_config = ArcaDeviceConfig(
            arca_template_id="detail-only",
            ttl_in_minutes=60,
            name="detail-device",
        )

        merged = facade._merge_config(None, detail_config, "ARCA")

        assert merged["template_id"] == "detail-only"
        assert merged["ttl_in_minutes"] == 60
        assert merged["name"] == "detail-device"

    def test_merge_config_filters_disallowed_credentials_fields(self, facade):
        """Credentials-only fields from template config are not overridable from detail_config (security).

        Note: Previously this test tried to set credentials fields (base_url, api_key, app_name)
        in detail_config, but these fields no longer exist in ArcaDeviceConfig.
        The security is now enforced at the type level - these fields simply cannot be passed.
        """
        template_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://template.example.com",
            api_key="template-api-key",
            template_id="tpl-123",
            oss_mount_id=None,
            arca_template_id_pre=None,
            arca_template_id_prod=None,
        )

        # Only allowed fields can be passed in detail_config
        detail_config = ArcaDeviceConfig(
            arca_template_id="override-tpl",  # Allowed
            ttl_in_minutes=60,  # Allowed
            name="override-name",  # Allowed
        )

        merged = facade._merge_config(template_config, detail_config, "ARCA")

        # Template credentials preserved (never overridable from detail_config)
        assert merged["base_url"] == "https://template.example.com"
        assert merged["api_key"] == "template-api-key"

        # Allowed fields from detail_config applied
        assert merged["template_id"] == "override-tpl"
        assert merged["ttl_in_minutes"] == 60
        assert merged["name"] == "override-name"
        assert merged["name"] == "override-name"


@pytest.mark.unit
class TestDestroyDevice:
    """Test destroy_device with tenant suffix parsing (Decision D-05)."""

    @pytest.mark.asyncio
    async def test_destroy_device_parses_suffix_and_resolves_tenant(
        self, facade, mock_service
    ):
        """destroy_device extracts template_id from suffix and uses Factory (D-08)."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42
        mock_service.destroy_device.return_value = True

        # Device ID with suffix
        paas_device_id = "sandbox-abc123@42"

        result = await facade.destroy_device(paas_device_id)

        assert result is True

        # Verify template looked up by template_id from suffix
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=42
        )

        # Verify Factory called with tenant, template_uuid, and resolved template
        facade._factory.create.assert_called_once()
        call_kwargs = facade._factory.create.call_args[1]
        assert call_kwargs["tenant_name"] == "test-tenant"
        assert call_kwargs["template_uuid"] == "test-template-uuid"

        # Verify underlying service called with device_id (no suffix)
        mock_service.destroy_device.assert_called_once_with("sandbox-abc123")

    @pytest.mark.asyncio
    async def test_destroy_device_without_suffix_backward_compat(
        self, facade, mock_service
    ):
        """destroy_device without suffix uses default template lookup (D-05)."""
        # With template_id=0, it uses tenant's default template
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service
        mock_service.destroy_device.return_value = True

        # Legacy device ID without suffix - template_id defaults to 0
        paas_device_id = "legacy-device-id"

        result = await facade.destroy_device(paas_device_id)

        assert result is True
        # template_id=0 triggers default template lookup
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=0
        )

    @pytest.mark.asyncio
    async def test_destroy_device_wraps_paas_error(self, facade, mock_service):
        """destroy_device wraps PaasError as DeviceFacadeException (D-06)."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.get_credentials.return_value.template_id = 42

            paas_error = PaasError(ErrorCode.DEVICE_DESTROY_FAILED, "Destroy failed")
            mock_service.destroy_device.side_effect = paas_error

            paas_device_id = "sandbox-abc@42"

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.destroy_device(paas_device_id)

            exception = exc_info.value
            assert exception.operation == "destroy_device"
            assert exception.template_id == 42
            assert exception.paas_device_id == "sandbox-abc@42"
            assert exception.original_error == paas_error

    @pytest.mark.asyncio
    async def test_destroy_device_not_found_idempotent(self, facade, mock_service):
        """destroy_device returns True when device already gone (idempotent)."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            # Service returns True for already-destroyed (idempotent)
            mock_service.destroy_device.return_value = True

            result = await facade.destroy_device("sandbox-gone@42")

            assert result is True


@pytest.mark.unit
class TestExecuteCommand:
    """Test execute_command with tenant suffix parsing (Decision D-05)."""

    @pytest.mark.asyncio
    async def test_execute_command_parses_suffix_and_resolves_tenant(
        self, facade, mock_service
    ):
        """execute_command extracts template_id from suffix and executes (D-08)."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        mock_result = CommandResult(
            exit_code=0,
            stdout="hello",
            stderr="",
            execution_time_ms=150,
            command="echo hello",
            env=None,
        )
        mock_service.execute_command.return_value = mock_result

        # Device ID with suffix
        paas_device_id = "sandbox-abc123@42"

        result = await facade.execute_command(paas_device_id, "echo hello")

        assert isinstance(result, CommandResult)
        assert result.exit_code == 0
        assert result.stdout == "hello"

        # Verify template looked up by template_id from suffix
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=42
        )

        # Verify underlying service called with device_id (no suffix)
        mock_service.execute_command.assert_called_once_with(
            "sandbox-abc123", "echo hello", None, 30
        )

    @pytest.mark.asyncio
    async def test_execute_command_with_env(self, facade, mock_service):
        """execute_command passes environment variables to service."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service

            mock_result = CommandResult(
                exit_code=0,
                stdout="env output",
                stderr="",
                execution_time_ms=200,
                command="env",
                env={"KEY": "value"},
            )
            mock_service.execute_command.return_value = mock_result

            env = {"KEY": "value", "FOO": "bar"}
            result = await facade.execute_command("sandbox-abc@42", "env", env=env)

            # Verify env was passed through
            mock_service.execute_command.assert_called_once_with(
                "sandbox-abc", "env", env, 30
            )
            assert result.env == {"KEY": "value"}

    @pytest.mark.asyncio
    async def test_execute_command_wraps_paas_error(self, facade, mock_service):
        """execute_command wraps PaasError as DeviceFacadeException (D-06)."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.get_credentials.return_value.template_id = 42

            paas_error = PaasError(ErrorCode.COMMAND_FAILED, "Command failed")
            mock_service.execute_command.side_effect = paas_error

            paas_device_id = "sandbox-abc@42"

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.execute_command(paas_device_id, "bad command")

            exception = exc_info.value
            assert exception.operation == "execute_command"
            assert exception.template_id == 42
            assert exception.paas_device_id == "sandbox-abc@42"
            assert exception.original_error == paas_error

    @pytest.mark.asyncio
    async def test_execute_command_timeout_wrapping(self, facade, mock_service):
        """execute_command correctly wraps COMMAND_TIMEOUT errors."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service

            paas_error = PaasError(
                ErrorCode.COMMAND_TIMEOUT, "Command timed out after 30s"
            )
            mock_service.execute_command.side_effect = paas_error

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.execute_command("sandbox-abc@42", "sleep 100")

            exception = exc_info.value
            assert exception.operation == "execute_command"
            assert exception.original_error.code == ErrorCode.COMMAND_TIMEOUT


@pytest.mark.unit
class TestFacadeEdgeCases:
    """Edge cases and integration tests for Facade."""

    def test_is_provided_method(self, facade):
        """_is_provided correctly identifies provided values (D-01/D-02)."""
        assert facade._is_provided("value") is True
        assert facade._is_provided(123) is True
        assert facade._is_provided(0) is True  # Zero is provided
        assert facade._is_provided([]) is True  # Empty list is provided (not None)
        assert facade._is_provided(None) is False
        assert facade._is_provided("") is False  # Empty string is "not provided"

    @pytest.mark.asyncio
    async def test_get_platform_type(self, facade):
        """_get_platform_type detects platform from service class name or method."""
        # Create a mock service with "arca" in the class name
        # Turn off spec for MagicMock to avoid hasattr always returning True
        mock_arca = MagicMock()
        mock_arca.__class__.__name__ = "ArcaPaasService"
        # Delete get_platform_type to force class name fallback
        del mock_arca.get_platform_type
        assert await facade._get_platform_type(mock_arca) == "ARCA"

        # Create a mock service with "sigma" in the class name
        mock_sigma = MagicMock()
        mock_sigma.__class__.__name__ = "SigmaPaasService"
        del mock_sigma.get_platform_type
        assert await facade._get_platform_type(mock_sigma) == "SIGMA"

        # Create a mock service with "local" in the class name
        mock_local = MagicMock()
        mock_local.__class__.__name__ = "LocalPaasService"
        del mock_local.get_platform_type
        assert await facade._get_platform_type(mock_local) == "LOCAL"

        # Test with get_platform_type method (per D-FF04)
        from secbaas.community.api.tenant_manage import TenantType

        mock_with_method = MagicMock()
        mock_with_method.get_platform_type = AsyncMock(return_value=TenantType.LOCAL)
        assert await facade._get_platform_type(mock_with_method) == "LOCAL"

        # Unknown service type
        mock_unknown = MagicMock()
        mock_unknown.__class__.__name__ = "UnknownService"
        del mock_unknown.get_platform_type
        assert await facade._get_platform_type(mock_unknown) == "UNKNOWN"

        # None returns UNKNOWN
        assert await facade._get_platform_type(None) == "UNKNOWN"

    def test_architecture_inheritance(self):
        """Verify PaasServiceFacade implements expected architecture."""
        from secbaas.community.core.service.paas import PaasServiceFacade

        # Verify all expected methods exist
        assert hasattr(PaasServiceFacade, "create_device")
        assert hasattr(PaasServiceFacade, "destroy_device")
        assert hasattr(PaasServiceFacade, "execute_command")
        assert hasattr(PaasServiceFacade, "_parse_device_id")
        # Note: _resolve_template was replaced with DeviceTemplateService calls
        assert hasattr(PaasServiceFacade, "_merge_config")
        assert hasattr(PaasServiceFacade, "_is_provided")

        # Verify method signatures (by inspection)
        import inspect

        # create_device
        create_sig = inspect.signature(PaasServiceFacade.create_device)
        assert "tenant_name" in create_sig.parameters
        assert "device_template_uuid" in create_sig.parameters
        assert "detail_config" in create_sig.parameters

        # destroy_device
        destroy_sig = inspect.signature(PaasServiceFacade.destroy_device)
        assert "paas_device_id" in destroy_sig.parameters

        # execute_command
        exec_sig = inspect.signature(PaasServiceFacade.execute_command)
        assert "paas_device_id" in exec_sig.parameters
        assert "cmd" in exec_sig.parameters
        assert "env" in exec_sig.parameters

    def test_parse_device_id_edge_cases(self, facade):
        """Additional edge cases for _parse_device_id."""
        # Empty string
        device_id, template_id = facade._parse_device_id("")
        assert device_id == ""
        assert template_id == 0

        # Just @ symbol - everything before @ is empty
        device_id, template_id = facade._parse_device_id("@")
        assert device_id == "@"  # Empty suffix treated as invalid, returns full string
        assert template_id == 0  # Empty string after @ can't be parsed as int

        # Large tenant ID
        device_id, template_id = facade._parse_device_id("device@999999999")
        assert device_id == "device"
        assert template_id == 999999999


@pytest.mark.unit
class TestResolveWsConnInfo:
    """Test cases for PaasServiceFacade.resolve_ws_conn_info."""

    @pytest.fixture
    def facade_with_mocks(self, mock_service):
        """Create facade with all dependencies mocked."""
        mock_template_svc = MagicMock()
        mock_device_repo = MagicMock()
        mock_factory = MagicMock()
        facade = PaasServiceFacade(
            device_repository=mock_device_repo,
            device_template_service=mock_template_svc,
            factory=mock_factory,
        )

        # Set service class name so _get_platform_type returns "ARCA"
        mock_service.__class__.__name__ = "ArcaPaasService"
        mock_template_svc.get_by_template_id.return_value = make_mock_template()
        mock_factory.create.return_value = mock_service

        # Store mocks on facade for test assertions (type: ignore because these are dynamic attrs)
        facade._mock_get_template = mock_template_svc.get_by_template_id  # type: ignore[attr-defined]
        facade._mock_factory = mock_factory  # type: ignore[attr-defined]
        yield facade

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_success(self, facade_with_mocks, mock_service):
        """Should resolve WebSocket connection info for active Arca device."""
        mock_service.get_credentials.return_value.template_id = 42

        result = await facade_with_mocks.resolve_ws_conn_info(
            paas_device_id="sandbox-abc123@42",
            port=20003,
            path="/api/openclaw/ws",
        )

        assert result.ws_url == (
            "wss://proxy.example.com/proxypass/"
            "ARCA_sandbox-abc123@42:20003/api/openclaw/ws"
        )
        assert result.token == "jwt-token-xyz"
        assert result.target == "ARCA_sandbox-abc123@42:20003"
        assert result.expires_at is not None

        facade_with_mocks._mock_get_template.assert_called_once_with(template_id=42)
        facade_with_mocks._mock_factory.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_device_not_found(self, facade_with_mocks):
        """Should raise DeviceFacadeException when template is not found."""
        facade_with_mocks._mock_get_template.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade_with_mocks.resolve_ws_conn_info(
                paas_device_id="sandbox-abc@42",
                port=20003,
                path="/ws",
            )

        assert exc_info.value.operation == "resolve_ws_conn_info"
        assert "Template not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_device_not_active(
        self, facade_with_mocks, mock_service
    ):
        """Should resolve connection info regardless of device status."""
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        mock_service.get_credentials.return_value.template_id = 42
        # Override to return dynamic response based on the device_id
        mock_service.resolve_ws_conn_info.return_value = WsConnectionInfo(
            ws_url="wss://proxy.example.com/proxypass/ARCA_sandbox-abc@42:20003/ws",
            token="jwt-token-xyz",
            target="ARCA_sandbox-abc@42:20003",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        result = await facade_with_mocks.resolve_ws_conn_info(
            paas_device_id="sandbox-abc@42",
            port=20003,
            path="/ws",
        )

        assert result is not None
        assert result.target == "ARCA_sandbox-abc@42:20003"

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_missing_sandbox_id(
        self, facade_with_mocks, mock_service
    ):
        """Should resolve connection info without sandbox_id in device props."""
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        mock_service.get_credentials.return_value.template_id = 42
        # Override to return dynamic response based on the device_id
        mock_service.resolve_ws_conn_info.return_value = WsConnectionInfo(
            ws_url="wss://proxy.example.com/proxypass/ARCA_no-sandbox@42:20003/ws",
            token="jwt-token-xyz",
            target="ARCA_no-sandbox@42:20003",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        result = await facade_with_mocks.resolve_ws_conn_info(
            paas_device_id="no-sandbox@42",
            port=20003,
            path="/ws",
        )

        assert result is not None
        assert "no-sandbox" in result.target

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_sigma_not_implemented(
        self, facade_with_mocks, mock_service
    ):
        """Should raise NotImplementedError for Sigma platform."""
        # Override the service class name so _get_platform_type returns "SIGMA"
        mock_service.__class__.__name__ = "SigmaPaasService"
        facade_with_mocks._mock_factory.create.return_value = mock_service
        # Set up resolve_ws_conn_info to raise NotImplementedError
        mock_service.resolve_ws_conn_info.side_effect = NotImplementedError(
            "Sigma platform does not support WebSocket connections"
        )

        # Facade wraps NotImplementedError in DeviceFacadeException
        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade_with_mocks.resolve_ws_conn_info(
                paas_device_id="sigma-device@42",
                port=20003,
                path="/ws",
            )

        assert "Sigma" in str(exc_info.value)


@pytest.mark.unit
class TestLocalPlatform:
    """Test Local platform facade operations."""

    @patch(
        "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_default_or_explicit_template"
    )
    @pytest.mark.asyncio
    async def test_get_platform_type_returns_local(self, mock_get_template):
        """_get_platform_type returns LOCAL for LocalPaasService."""
        from secbaas.community.api.tenant_manage import TenantType

        mock_template = make_mock_local_template()
        mock_get_template.return_value = mock_template

        # Create a mock PaasServiceFacade with mocked deps
        mock_service = MagicMock()
        mock_service.get_platform_type = AsyncMock(return_value=TenantType.LOCAL)

        facade = PaasServiceFacade(
            device_repository=MagicMock(),
            device_template_service=MagicMock(),
            factory=MagicMock(),
        )
        # Call any method that uses _get_platform_type
        # Just verify the mock returns LOCAL
        platform = await facade._get_platform_type(mock_service)
        assert platform == "LOCAL"

    def test_local_device_config_to_create_config(self):
        """LocalDeviceConfig correctly converts to LocalCreateConfig."""
        config = LocalDeviceConfig(
            user_id="user123",
            machine_id="machine456",
            tc_bot_id="bot789",
            agent_code="agent-abc",
            name="test-device",
            description="Test local device",
            envs={"KEY": "value"},
        )

        create_config = config.to_create_config()
        assert isinstance(create_config, LocalCreateConfig)
        assert create_config.user_id == "user123"
        assert create_config.machine_id == "machine456"
        assert create_config.tc_bot_id == "bot789"
        assert create_config.agent_code == "agent-abc"


@pytest.mark.unit
class TestInvokeHttpInDevice:
    """Test invoke_http_in_device with mocked LocalPaasService."""

    @pytest.fixture
    def mock_local_service(self):
        """Create a mock LocalPaasService with async invoke_http_in_device."""
        mock = MagicMock()
        mock.get_platform_type = AsyncMock(return_value=TenantType.LOCAL)
        mock.invoke_http_in_device = AsyncMock()
        return mock

    @pytest.fixture
    def mock_template_service(self):
        """Mock DeviceTemplateService for template lookups."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock:
            yield mock

    @pytest.fixture
    def mock_factory(self, facade):
        """Mock facade factory."""
        yield facade._factory

    @pytest.mark.asyncio
    async def test_invoke_http_success_with_local_platform(
        self, facade, mock_local_service, mock_factory
    ):
        """invoke_http_in_device succeeds with LocalPaasService."""
        # Setup mock template for LOCAL platform via injected template service
        mock_template = make_mock_local_template()
        facade._device_template_service.get_by_template_id.return_value = mock_template
        facade._factory.create.return_value = mock_local_service

        # Setup async mock return value
        mock_response = {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"result": "ok"}').decode(),
        }
        mock_local_service.invoke_http_in_device.return_value = mock_response

        # Execute
        result = await facade.invoke_http_in_device(
            paas_device_id="container--machine--user@42",
            method="GET",
            port=8080,
            path="/api/health",
            query_string=None,
            headers={"Accept": "application/json"},
            body=b"",
        )

        # Assert
        assert result["status_code"] == 200
        assert result["headers"]["Content-Type"] == "application/json"
        decoded_body = base64.b64decode(result["body"])
        assert b'"result": "ok"' in decoded_body

        # Verify mock called correctly
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=42
        )
        facade._factory.create.assert_called_once()
        mock_local_service.invoke_http_in_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoke_http_not_implemented_arca(
        self, facade, mock_template_service
    ):
        """invoke_http_in_device raises DeviceFacadeException for ARCA platform."""
        # Setup mock template for ARCA platform
        mock_template = make_mock_template(platform_type="ARCA")
        mock_template_service.return_value = mock_template

        # Setup mock service returning ARCA platform type
        mock_arca_service = MagicMock()
        mock_arca_service.get_platform_type = AsyncMock(return_value=TenantType.ARCA)
        mock_arca_service.invoke_http_in_device = MagicMock(
            side_effect=NotImplementedError(
                "ARCA platform does not support HTTP invocation"
            )
        )
        facade._factory.create.return_value = mock_arca_service

        # Execute and Assert
        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.invoke_http_in_device(
                paas_device_id="sandbox-abc@42",
                method="GET",
                port=8080,
                path="/api/health",
                query_string=None,
                headers={},
                body=b"",
            )

        assert "ARCA platform does not support HTTP invocation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invoke_http_not_implemented_sigma(
        self, facade, mock_template_service
    ):
        """invoke_http_in_device raises DeviceFacadeException for SIGMA platform."""
        # Setup mock template for SIGMA platform
        mock_template = make_mock_template(platform_type="SIGMA")
        mock_template_service.return_value = mock_template

        # Setup mock service returning SIGMA platform type
        mock_sigma_service = MagicMock()
        mock_sigma_service.get_platform_type = AsyncMock(return_value=TenantType.SIGMA)
        mock_sigma_service.invoke_http_in_device = MagicMock(
            side_effect=NotImplementedError(
                "SIGMA platform does not support HTTP invocation"
            )
        )
        facade._factory.create.return_value = mock_sigma_service

        # Execute and Assert
        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.invoke_http_in_device(
                paas_device_id="sigma-device@42",
                method="GET",
                port=8080,
                path="/api/health",
                query_string=None,
                headers={},
                body=b"",
            )

        assert "SIGMA platform does not support HTTP invocation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invoke_http_device_not_found(self, facade, mock_factory):
        """invoke_http_in_device raises DeviceFacadeException when template not found."""
        # Setup template lookup to return None via injected template service
        facade._device_template_service.get_by_template_id.return_value = None

        # Execute and Assert
        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.invoke_http_in_device(
                paas_device_id="container--machine--user@42",
                method="GET",
                port=8080,
                path="/api/health",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.operation == "invoke_http_in_device"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_invoke_http_service_error(
        self, facade, mock_local_service, mock_template_service, mock_factory
    ):
        """invoke_http_in_device wraps service errors in DeviceFacadeException."""
        # Setup mock template for LOCAL platform
        mock_template = make_mock_local_template()
        mock_template_service.return_value = mock_template
        facade._factory.create.return_value = mock_local_service

        # Setup async mock to raise PaasError
        paas_error = PaasError(ErrorCode.DEVICE_UNAVAILABLE, "Device is not responding")
        mock_local_service.invoke_http_in_device.side_effect = paas_error

        # Execute and Assert
        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.invoke_http_in_device(
                paas_device_id="container--machine--user@42",
                method="GET",
                port=8080,
                path="/api/health",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.operation == "invoke_http_in_device"
        assert exc_info.value.original_error.code == ErrorCode.DEVICE_UNAVAILABLE
        assert "Device is not responding" in str(exc_info.value.original_error.message)


# =============================================================================
# Tests for UNCOVERED methods: get_device_info, update_outbound_operation_rule,
# update_device_ttl, restart_device, plus edge cases
# =============================================================================


@pytest.mark.unit
class TestGetDeviceInfo:
    """Test get_device_info with mocked service."""

    @pytest.mark.asyncio
    async def test_get_device_info_success(self, facade, mock_service):
        """get_device_info resolves template and returns DeviceInfo."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        from secbaas.community.api.device_manage._device_info import DeviceInfo

        mock_device_info = DeviceInfo(platform="arca", status="RUNNING")
        mock_service.get_device_info = AsyncMock(return_value=mock_device_info)

        result = await facade.get_device_info("sandbox-abc123@42")

        assert result.platform == "arca"
        assert result.status == "RUNNING"
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=42
        )
        mock_service.get_device_info.assert_called_once_with("sandbox-abc123")

    @pytest.mark.asyncio
    async def test_get_device_info_template_not_found(self, facade):
        """get_device_info raises DeviceFacadeException when template not found."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.get_device_info("sandbox-abc@42")

        assert exc_info.value.operation == "get_device_info"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_device_info_wraps_paas_error(self, facade, mock_service):
        """get_device_info wraps PaasError as DeviceFacadeException."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.__class__.__name__ = "ArcaPaasService"

            paas_error = PaasError(
                ErrorCode.DEVICE_NOT_FOUND, "Device not found on platform"
            )
            mock_service.get_device_info = AsyncMock(side_effect=paas_error)

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.get_device_info("sandbox-gone@42")

            assert exc_info.value.operation == "get_device_info"
            assert exc_info.value.original_error.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_device_info_no_suffix(self, facade, mock_service):
        """get_device_info works without @template_id suffix."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service

        from secbaas.community.api.device_manage._device_info import DeviceInfo

        mock_device_info = DeviceInfo(platform="arca", status="RUNNING")
        mock_service.get_device_info = AsyncMock(return_value=mock_device_info)

        result = await facade.get_device_info("legacy-device")
        assert result.platform == "arca"
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=0
        )


@pytest.mark.unit
class TestUpdateOutboundOperationRule:
    """Test update_outbound_operation_rule method."""

    @pytest.mark.asyncio
    async def test_update_outbound_rule_success(self, facade, mock_service):
        """update_outbound_operation_rule succeeds."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service
        mock_service.update_outbound_operation_rule = AsyncMock(return_value=True)

        from secbaas.community.api.device_manage import OutBoundOperationRule

        rule = OutBoundOperationRule(header_operation_rules=[])

        result = await facade.update_outbound_operation_rule("sandbox-abc123@42", rule)

        assert result is True
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=42
        )
        mock_service.update_outbound_operation_rule.assert_called_once_with(
            "sandbox-abc123", rule
        )

    @pytest.mark.asyncio
    async def test_update_outbound_rule_template_not_found(self, facade):
        """update_outbound_operation_rule raises DeviceFacadeException when no template."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.update_outbound_operation_rule("sandbox-abc@42", MagicMock())

        assert exc_info.value.operation == "update_outbound_operation_rule"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_outbound_rule_wraps_paas_error(self, facade, mock_service):
        """update_outbound_operation_rule wraps PaasError."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.__class__.__name__ = "ArcaPaasService"

            paas_error = PaasError(ErrorCode.DEVICE_UNAVAILABLE, "Device unavailable")
            mock_service.update_outbound_operation_rule = AsyncMock(
                side_effect=paas_error
            )

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.update_outbound_operation_rule(
                    "sandbox-down@42", MagicMock()
                )

            assert exc_info.value.operation == "update_outbound_operation_rule"
            assert exc_info.value.original_error.code == ErrorCode.DEVICE_UNAVAILABLE


@pytest.mark.unit
class TestUpdateDeviceTTL:
    """Test update_device_ttl method."""

    @pytest.mark.asyncio
    async def test_update_device_ttl_success(self, facade, mock_service):
        """update_device_ttl succeeds and restores @template_id in result."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service

            from secbaas.community.api.health_check.bot import TTLInfo

            mock_ttl = TTLInfo(
                paas_device_id="sandbox-abc123",
                old_expiration_time=datetime.now(UTC) - timedelta(minutes=5),
                new_expiration_time=datetime.now(UTC) + timedelta(minutes=55),
                success=True,
            )
            mock_service.update_device_ttl = AsyncMock(return_value=mock_ttl)

            result = await facade.update_device_ttl("sandbox-abc123@42")

            assert result.success is True
            assert result.paas_device_id == "sandbox-abc123@42"
            mock_service.update_device_ttl.assert_called_once_with("sandbox-abc123")

    @pytest.mark.asyncio
    async def test_update_device_ttl_template_not_found(self, facade):
        """update_device_ttl raises DeviceFacadeException when no template."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.update_device_ttl("sandbox-abc@42")

        assert exc_info.value.operation == "update_device_ttl"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_device_ttl_wraps_paas_error(self, facade, mock_service):
        """update_device_ttl wraps PaasError."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.__class__.__name__ = "ArcaPaasService"

            paas_error = PaasError(ErrorCode.DEVICE_UNAVAILABLE, "Cannot extend TTL")
            mock_service.update_device_ttl = AsyncMock(side_effect=paas_error)

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.update_device_ttl("sandbox-dead@42")

            assert exc_info.value.operation == "update_device_ttl"
            assert exc_info.value.original_error.code == ErrorCode.DEVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_update_device_ttl_no_suffix(self, facade, mock_service):
        """update_device_ttl works with legacy device IDs."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service

        from secbaas.community.api.health_check.bot import TTLInfo

        mock_ttl = TTLInfo(
            paas_device_id="legacy-device",
            old_expiration_time=None,
            new_expiration_time=datetime.now(UTC) + timedelta(minutes=60),
            success=True,
        )
        mock_service.update_device_ttl = AsyncMock(return_value=mock_ttl)

        result = await facade.update_device_ttl("legacy-device")

        assert result.success is True
        assert result.paas_device_id == "legacy-device"
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=0
        )


@pytest.mark.unit
class TestRestartDevice:
    """Test restart_device method."""

    @pytest.mark.asyncio
    async def test_restart_device_success(self, facade, mock_service):
        """restart_device succeeds."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.restart_device = AsyncMock(return_value=True)

            result = await facade.restart_device("sandbox-abc123@42")

            assert result is True
            mock_service.restart_device.assert_called_once_with("sandbox-abc123")

    @pytest.mark.asyncio
    async def test_restart_device_template_not_found(self, facade):
        """restart_device raises DeviceFacadeException when no template."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.restart_device("sandbox-abc@42")

        assert exc_info.value.operation == "restart_device"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_restart_device_not_implemented(self, facade, mock_service):
        """restart_device wraps NotImplementedError."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.restart_device = AsyncMock(
                side_effect=NotImplementedError("Sigma does not support restart")
            )

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.restart_device("sigma-device@42")

            assert exc_info.value.operation == "restart_device"
            assert "not support" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_restart_device_wraps_paas_error(self, facade, mock_service):
        """restart_device wraps PaasError."""
        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_get_template:
            mock_get_template.return_value = make_mock_template()

            facade._factory.create.return_value = mock_service
            mock_service.__class__.__name__ = "ArcaPaasService"

            paas_error = PaasError(ErrorCode.DEVICE_UNAVAILABLE, "Cannot restart")
            mock_service.restart_device = AsyncMock(side_effect=paas_error)

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.restart_device("sandbox-down@42")

            assert exc_info.value.operation == "restart_device"
            assert exc_info.value.original_error.code == ErrorCode.DEVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_restart_device_no_suffix(self, facade, mock_service):
        """restart_device works with legacy device IDs."""
        facade._device_template_service.get_by_template_id.return_value = (
            make_mock_template()
        )

        facade._factory.create.return_value = mock_service
        mock_service.restart_device = AsyncMock(return_value=True)

        result = await facade.restart_device("legacy-device")
        assert result is True
        facade._device_template_service.get_by_template_id.assert_called_once_with(
            template_id=0
        )


@pytest.mark.unit
class TestCreateDeviceLocal:
    """create_device for LOCAL platform."""

    @pytest.mark.asyncio
    async def test_create_device_local_success(self, facade, mock_service):
        """create_device LOCAL returns LocalCreationResult with suffixed container_id."""
        facade._device_template_service.get_default_or_explicit_template.return_value = make_mock_local_template()

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        mock_result = LocalCreationResult(
            container_id="container--machine--user",
            platform="local",
            status="RUNNING",
        )
        mock_service.create_device.return_value = mock_result

        result = await facade.create_device(
            tenant_name="test-tenant",
            device_template_uuid="local-template-uuid",
            detail_config=LocalDeviceConfig(
                user_id="test-user",
                machine_id="test-machine",
                tc_bot_id="test-bot",
                agent_code="test-agent",
            ),
        )

        assert isinstance(result, LocalCreationResult)
        assert result.container_id == "container--machine--user@42"
        assert result.platform == "local"

    @pytest.mark.asyncio
    async def test_create_device_local_wraps_paas_error(self, facade, mock_service):
        """create_device LOCAL wraps PaasError."""
        facade._device_template_service.get_default_or_explicit_template.return_value = make_mock_local_template()

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        paas_error = PaasError(
            ErrorCode.DEVICE_CREATION_FAILED, "Local creation failed"
        )
        mock_service.create_device.side_effect = paas_error

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="local-template-uuid",
                detail_config=LocalDeviceConfig(
                    user_id="test-user",
                    machine_id="test-machine",
                    tc_bot_id="test-bot",
                    agent_code="test-agent",
                ),
            )

        assert exc_info.value.operation == "create_device"
        assert exc_info.value.platform_type == "LOCAL"


@pytest.mark.unit
class TestCreateDeviceSigma:
    """create_device for SIGMA platform."""

    @pytest.mark.asyncio
    async def test_create_device_sigma_raises_not_implemented(
        self, facade, mock_service
    ):
        """create_device for SIGMA raises PaasError (not implemented)."""
        facade._device_template_service.get_default_or_explicit_template.return_value = make_mock_template(
            platform_type="SIGMA"
        )

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.create_device(
                tenant_name="test-tenant",
                device_template_uuid="sigma-template-uuid",
            )

        assert exc_info.value.operation == "create_device"
        assert "SIGMA" in exc_info.value.platform_type


@pytest.mark.unit
class TestCreateDeviceUnknownPlatform:
    """create_device for unknown platform types."""

    @pytest.mark.asyncio
    async def test_create_device_unknown_platform(self, facade, mock_service):
        """create_device raises ValueError for unknown platform type."""
        unknown_template = make_mock_template()
        unknown_template.type = "UNKNOWN_PLATFORM"

        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_default_or_explicit_template"
        ) as mock_resolve:
            mock_resolve.return_value = unknown_template

            facade._factory.create.return_value = mock_service
            mock_service.get_credentials.return_value.template_id = 42

            with pytest.raises(ValueError) as exc_info:
                await facade.create_device(tenant_name="test-tenant")

            assert "Unknown platform" in str(exc_info.value)


@pytest.mark.unit
class TestMergeConfigLocalAndSigma:
    """_merge_config for LOCAL and SIGMA platforms."""

    def test_merge_config_local_platform(self, facade):
        """_merge_config works with LocalDeviceConfig."""
        template_config = LocalTemplateConfig(type="LOCAL")

        detail_config = LocalDeviceConfig(
            user_id="override-user-id",
            machine_id="override-machine",
            tc_bot_id="override-bot-id",
            agent_code="override-agent-code",
        )

        merged = facade._merge_config(template_config, detail_config, "LOCAL")
        assert merged["user_id"] == "override-user-id"
        assert merged["machine_id"] == "override-machine"

    def test_merge_config_local_mismatch_raises(self, facade):
        """_merge_config raises ValueError when detail doesn't match LOCAL."""
        template_config = LocalTemplateConfig(type="LOCAL")
        detail_config = ArcaDeviceConfig(arca_template_id="test")

        with pytest.raises(ValueError) as exc_info:
            facade._merge_config(template_config, detail_config, "LOCAL")

        assert "LOCAL" in str(exc_info.value)

    def test_merge_config_sigma_platform(self, facade):
        """_merge_config works with SigmaDeviceConfig."""
        template_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://template.example.com",
            access_key="ak",
            secret_key="sk",
        )

        detail_config = SigmaDeviceConfig(
            endpoint="https://sigma.example.com",
            access_key="sigma-ak",
            secret_key="sigma-sk",
            name="sigma-device",
            description="Sigma test device",
        )

        merged = facade._merge_config(template_config, detail_config, "SIGMA")
        assert merged["name"] == "sigma-device"
        assert merged["description"] == "Sigma test device"
        assert merged["endpoint"] == "https://template.example.com"

    def test_merge_config_sigma_mismatch_raises(self, facade):
        """_merge_config raises ValueError for SIGMA with wrong type."""
        template_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )
        detail_config = ArcaDeviceConfig(arca_template_id="test")

        with pytest.raises(ValueError) as exc_info:
            facade._merge_config(template_config, detail_config, "SIGMA")

        assert "SIGMA" in str(exc_info.value)

    def test_merge_config_sigma_filters_credentials(self, facade):
        """_merge_config filters credentials fields for SIGMA platform."""
        template_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://template.example.com",
            access_key="template-ak",
            secret_key="template-sk",
        )

        detail_config = SigmaDeviceConfig(
            endpoint="https://sigma.example.com",
            access_key="sigma-ak",
            secret_key="sigma-sk",
            name="override-name",
            description="override-desc",
        )

        merged = facade._merge_config(template_config, detail_config, "SIGMA")

        assert merged["name"] == "override-name"
        assert merged["description"] == "override-desc"
        assert merged["endpoint"] == "https://template.example.com"
        assert merged["access_key"] == "template-ak"
        assert merged["secret_key"] == "template-sk"

    def test_merge_config_local_ignores_disallowed(self, facade):
        """_merge_config passes allowed fields for LOCAL platform."""
        template_config = LocalTemplateConfig(type="LOCAL")

        detail_config = LocalDeviceConfig(
            user_id="override-user",
            machine_id="override-machine",
            tc_bot_id="override-bot",
            agent_code="override-agent",
            name="override-name",
        )

        merged = facade._merge_config(template_config, detail_config, "LOCAL")
        assert merged["user_id"] == "override-user"
        assert merged["machine_id"] == "override-machine"
        assert merged["name"] == "override-name"

    def test_merge_config_local_engine_type_in_whitelist(self, facade):
        """_merge_config allows engine_type through LOCAL whitelist."""
        template_config = LocalTemplateConfig(type="LOCAL")

        detail_config = LocalDeviceConfig(
            user_id="u-1",
            machine_id="m-1",
            tc_bot_id="bot-1",
            agent_code="agent-x",
            engine_type="openclaw",
        )

        merged = facade._merge_config(template_config, detail_config, "LOCAL")
        assert merged["engine_type"] == "openclaw", (
            "engine_type should survive _merge_config — ensure it is in "
            "_LOCAL_ALLOWED_OVERRIDE_FIELDS whitelist"
        )


@pytest.mark.unit
class TestResolveWsConnInfoEdgeCases:
    """Edge case tests for resolve_ws_conn_info."""

    @pytest.mark.asyncio
    async def test_port_validation_too_low(self, facade):
        """resolve_ws_conn_info raises ValueError for port < 1."""
        with pytest.raises(ValueError) as exc_info:
            await facade.resolve_ws_conn_info(
                paas_device_id="sandbox-abc@42", port=0, path="/ws"
            )
        assert "port" in str(exc_info.value).lower()
        assert "1-65535" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_port_validation_too_high(self, facade):
        """resolve_ws_conn_info raises ValueError for port > 65535."""
        with pytest.raises(ValueError) as exc_info:
            await facade.resolve_ws_conn_info(
                paas_device_id="sandbox-abc@42", port=99999, path="/ws"
            )
        assert "port" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_port_validation_not_int(self, facade):
        """resolve_ws_conn_info raises ValueError for non-int port."""
        with pytest.raises(ValueError) as exc_info:
            await facade.resolve_ws_conn_info(
                paas_device_id="sandbox-abc@42",
                port="8080",  # type: ignore[arg-type]
                path="/ws",
            )
        assert "port" in str(exc_info.value).lower()


@pytest.mark.unit
class TestInvokeHttpEdgeCases:
    """Edge case tests for invoke_http_in_device."""

    @pytest.mark.asyncio
    async def test_invoke_http_paas_error_with_params(self, facade):
        """invoke_http_in_device wraps PaasError with query_string and custom headers."""
        from secbaas.community.api.tenant_manage import TenantType

        with patch(
            "secbaas.community.core.service.template_manage.DefaultDeviceTemplateService.get_by_template_id"
        ) as mock_template_service:
            mock_template_service.return_value = make_mock_local_template()

            mock_local_service = MagicMock()
            mock_local_service.get_platform_type = AsyncMock(
                return_value=TenantType.LOCAL
            )

            paas_error = PaasError(
                ErrorCode.DEVICE_UNAVAILABLE, "Device not responding"
            )
            mock_local_service.invoke_http_in_device = AsyncMock(side_effect=paas_error)
            facade._factory.create.return_value = mock_local_service

            with pytest.raises(DeviceFacadeException) as exc_info:
                await facade.invoke_http_in_device(
                    paas_device_id="container--machine--user@42",
                    method="POST",
                    port=8080,
                    path="/api/fail",
                    query_string="?key=value",
                    headers={"X-Custom": "test"},
                    body=b'{"data": "value"}',
                )

            assert exc_info.value.operation == "invoke_http_in_device"
            assert exc_info.value.original_error.code == ErrorCode.DEVICE_UNAVAILABLE


@pytest.mark.unit
class TestDestroyDeviceTemplateNotFound:
    """Test destroy_device when template is not found."""

    @pytest.mark.asyncio
    async def test_destroy_device_template_not_found(self, facade):
        """destroy_device raises DeviceFacadeException when no template."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.destroy_device("sandbox-abc@42")

        assert exc_info.value.operation == "destroy_device"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND


@pytest.mark.unit
class TestExecuteCommandTemplateNotFound:
    """Test execute_command when template is not found."""

    @pytest.mark.asyncio
    async def test_execute_command_template_not_found(self, facade):
        """execute_command raises DeviceFacadeException when no template."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade.execute_command("sandbox-abc@42", "echo hello")

        assert exc_info.value.operation == "execute_command"
        assert exc_info.value.original_error.code == ErrorCode.TEMPLATE_NOT_FOUND


@pytest.mark.unit
class TestGetPlatformTypeEdgeCases:
    """_get_platform_type edge cases."""

    @pytest.mark.asyncio
    async def test_enum_with_string_value(self, facade):
        """Returns uppercase string from enum.value."""
        mock_service = MagicMock()
        mock_enum = MagicMock()
        mock_enum.value = "arca"
        mock_service.get_platform_type = AsyncMock(return_value=mock_enum)

        result = await facade._get_platform_type(mock_service)
        assert result == "ARCA"

    @pytest.mark.asyncio
    async def test_enum_with_non_string_value_falls_back(self, facade):
        """Falls back to class name when enum.value is not string."""
        mock_service = MagicMock()
        mock_service.__class__.__name__ = "ArcaPaasService"
        mock_enum = MagicMock()
        mock_enum.value = 123
        mock_service.get_platform_type = AsyncMock(return_value=mock_enum)

        result = await facade._get_platform_type(mock_service)
        assert result == "ARCA"

    @pytest.mark.asyncio
    async def test_no_value_attribute_falls_back(self, facade):
        """Falls back to class name when result has no .value."""
        mock_service = MagicMock()
        mock_service.__class__.__name__ = "ArcaPaasService"
        mock_service.get_platform_type = AsyncMock(return_value="some_string")

        result = await facade._get_platform_type(mock_service)
        assert result == "ARCA"

    @pytest.mark.asyncio
    async def test_platform_type_not_callable_falls_back(self, facade):
        """Falls back to class name when get_platform_type is not callable."""
        mock_service = MagicMock()
        mock_service.__class__.__name__ = "LocalPaasService"
        mock_service.get_platform_type = "not_callable"

        result = await facade._get_platform_type(mock_service)
        assert result == "LOCAL"


@pytest.mark.unit
class TestFacadeAdditionalEdgeCases:
    """Additional edge cases for the facade."""

    def test_facade_accepts_required_constructor_args(self):
        """facade initializes with all 3 required constructor args."""
        mock_repo = MagicMock()
        mock_tpl_svc = MagicMock()
        mock_factory = MagicMock()
        f = PaasServiceFacade(
            device_repository=mock_repo,
            device_template_service=mock_tpl_svc,
            factory=mock_factory,
        )
        assert f.device_repository is mock_repo
        assert f._device_template_service is mock_tpl_svc
        assert f._factory is mock_factory

    @pytest.mark.asyncio
    async def test_create_device_with_detail_config_overrides(
        self, facade, mock_service
    ):
        """create_device merges detail_config into ArcaCreateConfig."""
        template = make_mock_template()
        facade._device_template_service.get_default_or_explicit_template.return_value = template

        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        mock_result = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="tpl-123",
            sandbox_id="sandbox-custom",
        )
        mock_service.create_device.return_value = mock_result

        detail_config = ArcaDeviceConfig(
            arca_template_id="detail-tpl-456",
            ttl_in_minutes=120,
            name="custom-name",
            description="Custom description",
        )

        result = await facade.create_device(
            tenant_name="test-tenant",
            detail_config=detail_config,
        )

        assert result.sandbox_id == "sandbox-custom@42"
        call_args = mock_service.create_device.call_args[0][0]
        assert isinstance(call_args, ArcaCreateConfig)
        assert call_args.template_id == "detail-tpl-456"
        assert call_args.ttl_in_minutes == 120
        assert call_args.name == "custom-name"

    @pytest.mark.asyncio
    async def test_create_device_with_docker_image_override(self, facade, mock_service):
        """create_device passes docker_image through to ArcaCreateConfig."""
        template = make_mock_template()
        facade._device_template_service.get_default_or_explicit_template.return_value = template
        facade._factory.create.return_value = mock_service
        mock_service.get_credentials.return_value.template_id = 42

        mock_result = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="tpl-123",
            sandbox_id="sandbox-custom",
        )
        mock_service.create_device.return_value = mock_result

        detail_config = ArcaDeviceConfig(
            docker_image="custom-image:v2",
        )

        result = await facade.create_device(
            tenant_name="test-tenant",
            detail_config=detail_config,
        )

        assert result.sandbox_id == "sandbox-custom@42"
        call_args = mock_service.create_device.call_args[0][0]
        assert isinstance(call_args, ArcaCreateConfig)
        assert call_args.docker_image == "custom-image:v2"

    def test_is_provided_zero_is_true(self, facade):
        """_is_provided(0) returns True."""
        assert facade._is_provided(0) is True

    def test_is_provided_empty_dict_is_true(self, facade):
        """_is_provided({}) returns True."""
        assert facade._is_provided({}) is True

    def test_is_provided_false_bool_is_true(self, facade):
        """_is_provided(False) returns True (not None/empty)."""
        assert facade._is_provided(False) is True


@pytest.mark.unit
class TestFetchStartProgress:
    """Test cases for PaasServiceFacade.fetch_start_progress."""

    @pytest.fixture
    def facade_with_mocks_fsp(self, mock_service):
        """Create facade with mocks for fetch_start_progress flow."""
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        mock_template_svc = MagicMock()
        mock_device_repo = MagicMock()
        mock_factory = MagicMock()
        facade = PaasServiceFacade(
            device_repository=mock_device_repo,
            device_template_service=mock_template_svc,
            factory=mock_factory,
        )

        mock_service.__class__.__name__ = "LocalPaasService"
        mock_service.fetch_start_progress = AsyncMock(
            return_value=FetchStartProgressResult(
                progress="completed",
            )
        )
        mock_template_svc.get_by_template_id.return_value = make_mock_local_template()
        mock_factory.create.return_value = mock_service

        facade._mock_get_template = mock_template_svc.get_by_template_id  # type: ignore[attr-defined]
        facade._mock_factory = mock_factory  # type: ignore[attr-defined]
        yield facade

    @pytest.mark.asyncio
    async def test_fetch_start_progress_ok(self, facade_with_mocks_fsp, mock_service):
        """Fetch start progress for LOCAL platform returns FetchStartProgressResult."""
        result = await facade_with_mocks_fsp.fetch_start_progress(
            paas_device_id="container--machine-001--user-001@42"
        )

        assert result.progress == "completed"

        # Verify template lookup used correct template_id
        facade_with_mocks_fsp._mock_get_template.assert_called_once_with(template_id=42)

        # Verify factory created service
        facade_with_mocks_fsp._mock_factory.create.assert_called_once()

        # Verify service.fetch_start_progress called with raw ID (no @template_id)
        mock_service.fetch_start_progress.assert_called_once_with(
            "container--machine-001--user-001"
        )

    @pytest.mark.asyncio
    async def test_fetch_start_progress_unsupported_platform(
        self, facade_with_mocks_fsp, mock_service
    ):
        """Raises DeviceFacadeException for unsupported (non-LOCAL) platforms."""
        mock_service.__class__.__name__ = "ArcaPaasService"
        mock_service.fetch_start_progress.side_effect = NotImplementedError(
            "fetch_start_progress is not supported on ArcaPaasService"
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await facade_with_mocks_fsp.fetch_start_progress(
                paas_device_id="arca-device@42"
            )

        assert exc_info.value.operation == "fetch_start_progress"
        assert "Arca" in str(exc_info.value)

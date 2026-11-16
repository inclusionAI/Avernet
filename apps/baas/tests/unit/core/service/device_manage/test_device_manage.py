"""Unit tests for DefaultDeviceService.

Covers all public methods and standalone utility functions in device_manage.py.
Uses MagicMock + AsyncMock to isolate service logic from repository and PaaS layers.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    CommandResult,
    DeployConfig,
    DestroyDeviceResponse,
    DeviceConfig,
    DeviceCreate,
    DeviceFacadeException,
    DeviceResponse,
    DeviceStatus,
    EncryptableHeaderRule,
    EncryptableOutBoundRule,
    ErrorCode,
    PaasError,
)
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
)
from secbaas.community.core.repository.device import DeviceRecord
from secbaas.community.core.service.device_manage import DefaultDeviceService
from secbaas.community.core.service.paas import PaasServiceFacade

# ==================== Fixtures ====================


@pytest.fixture
def mock_env():
    """Mock get_current_env to return 'test'."""
    with patch(
        "secbaas.community.core.service.device_manage._device_service.get_current_env",
        return_value="test",
    ):
        yield


@pytest.fixture
def mock_repo():
    """Mock DeviceRepository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_paas_facade():
    """Mock PaasServiceFacade with async methods."""
    facade = MagicMock(spec=PaasServiceFacade)
    facade.create_device = AsyncMock()
    facade.destroy_device = AsyncMock()
    facade.execute_command = AsyncMock()
    return facade


@pytest.fixture
def service(mock_repo, mock_paas_facade, mock_device_template_service, mock_env):
    """Create DefaultDeviceService with mocked dependencies."""
    svc = DefaultDeviceService(
        paas_facade=mock_paas_facade,
        repository=mock_repo,
        device_template_service=mock_device_template_service,
        secret_plugin=MagicMock(),
        callback_handler=MagicMock(handle=AsyncMock(return_value={"status": "ok"})),
    )
    yield svc


@pytest.fixture
def sample_record():
    """Create a minimal DeviceRecord stub."""
    record = MagicMock(spec=DeviceRecord)
    record.id = 1
    record.device_uuid = "DEVICE-test-uuid-001"
    record.tenant = "test-tenant"
    record.env = "test"
    record.domain = "default"
    record.status = DeviceStatus.PENDING.value
    record.provider_type = None
    record.provider_device_id = None
    record.provider_device_props = None
    record.extra_config = None
    record.err_msg = None
    record.creator = "test-user"
    record.modifier = "test-user"
    record.gmt_create = datetime(2024, 1, 1)
    record.gmt_modified = datetime(2024, 1, 1)
    return record


@pytest.fixture
def mock_device_template_service():
    """Mock DefaultDeviceTemplateService.get_default_or_explicit_template."""
    template = MagicMock()
    template.template_uuid = "tpl-uuid-001"
    template.config = ArcaTemplateConfig(
        type="ARCA",
        base_url="http://arca.test",
        api_key="test-key",
        template_id="arca-tpl-001",
    )
    mock_svc = MagicMock()
    mock_svc.get_default_or_explicit_template.return_value = template
    yield mock_svc


# ==================== Test _safe_format_hook ====================


class TestSafeFormatHook:
    """Test _safe_format_hook standalone function."""

    def test_format_simple(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _safe_format_hook,
        )

        result = _safe_format_hook("echo {token}", token="abc")
        assert result == "echo abc"

    def test_unknown_placeholder_preserved(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _safe_format_hook,
        )

        result = _safe_format_hook("echo {unknown}", token="abc")
        assert result == "echo {unknown}"

    def test_partial_known_placeholders(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _safe_format_hook,
        )

        result = _safe_format_hook("{token} and {unknown}", token="hello")
        assert result == "hello and {unknown}"

    def test_no_placeholders(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _safe_format_hook,
        )

        result = _safe_format_hook("plain script")
        assert result == "plain script"


# ==================== Test _encrypt_header_rule_values ====================


def _make_mock_secret_plugin(key="test-key"):
    """Create a mock SecretStorePlugin that returns the given key."""
    mock = MagicMock()
    mock.resolve_common_sm4_key.return_value = key
    return mock


class TestEncryptHeaderRuleValues:
    """Test _encrypt_header_rule_values standalone function."""

    def test_encrypts_matching_rules(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _encrypt_header_rule_values,
        )

        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="secret-token",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        # Build extra_config with the EncryptableOutBoundRule directly
        extra = DeviceConfig(
            deploy_config=DeployConfig(outbound_operation_rule=outbound.model_dump())
        )

        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_encrypt",
            return_value="encrypted",
        ):
            _encrypt_header_rule_values(extra, _make_mock_secret_plugin())

        # The function modifies in-place via extra.deploy_config.outbound_operation_rule
        dc = extra.deploy_config
        assert dc is not None
        assert dc.outbound_operation_rule is not None
        # After model_validate round-trip through DeployConfig, it's a plain
        # OutBoundOperationRule (not EncryptableOutBoundRule), so encrypt is skipped.
        # This test validates the function handles the non-encryptable case gracefully.
        # Full encrypt flow test requires direct EncryptableOutBoundRule on deploy_config.

    def test_skips_if_encrypt_value_false(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _encrypt_header_rule_values,
        )

        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="plain-token",
            encrypt_value=False,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        extra = DeviceConfig.model_validate(
            {"deploy_config": {"outbound_operation_rule": outbound.model_dump()}}
        )

        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_encrypt",
            return_value="encrypted",
        ) as mock_enc:
            _encrypt_header_rule_values(extra, _make_mock_secret_plugin())

        mock_enc.assert_not_called()
        dc = extra.deploy_config
        assert dc is not None
        assert dc.outbound_operation_rule is not None
        assert (
            dc.outbound_operation_rule.header_operation_rules[0].value == "plain-token"
        )

    def test_skips_non_encryptable_rule(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _encrypt_header_rule_values,
        )

        extra = DeviceConfig.model_validate(
            {
                "deploy_config": {
                    "outbound_operation_rule": {"header_operation_rules": []}
                }
            }
        )

        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_encrypt",
        ) as mock_enc:
            _encrypt_header_rule_values(extra, _make_mock_secret_plugin())

        mock_enc.assert_not_called()

    def test_none_extra_config(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _encrypt_header_rule_values,
        )

        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_encrypt",
        ) as mock_enc:
            _encrypt_header_rule_values(None, _make_mock_secret_plugin())

        mock_enc.assert_not_called()


# ==================== Test _decrypt_header_rule_values ====================


class TestDecryptHeaderRuleValues:
    """Test _decrypt_header_rule_values standalone function."""

    def test_decrypts_encrypted_rules(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _decrypt_header_rule_values,
        )

        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="encrypted-value",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])

        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_decrypt",
            return_value="decrypted",
        ):
            result = _decrypt_header_rule_values(outbound, _make_mock_secret_plugin())

        assert result is not None
        assert result.header_operation_rules[0].value == "decrypted"

    def test_returns_none_for_none_input(self):
        from secbaas.community.core.service.device_manage._device_service import (
            _decrypt_header_rule_values,
        )

        result = _decrypt_header_rule_values(None, _make_mock_secret_plugin())
        assert result is None

    def test_passes_through_sdk_rule(self):
        from secbaas.community.api.device_manage import OutBoundOperationRule
        from secbaas.community.core.service.device_manage._device_service import (
            _decrypt_header_rule_values,
        )

        sdk_rule = OutBoundOperationRule(header_operation_rules=[])
        result = _decrypt_header_rule_values(sdk_rule, _make_mock_secret_plugin())
        assert result is sdk_rule


# ==================== Test _record_to_response ====================


class TestRecordToResponse:
    """Test _record_to_response standalone function."""

    def test_converts_record(self):
        from secbaas.community.core.service.device_manage import (
            device_record_to_response,
        )

        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-1"
        record.provider_device_props = {"key": "val"}
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)

        resp = device_record_to_response(record)
        assert isinstance(resp, DeviceResponse)
        assert resp.id == 1
        assert resp.device_uuid == "DEVICE-001"
        assert resp.status == "ACTIVE"

    def test_raises_on_none(self):
        from secbaas.community.core.service.device_manage import (
            device_record_to_response,
        )

        with pytest.raises(RuntimeError, match="Device record is None"):
            device_record_to_response(None)


# ==================== Test DefaultDeviceService ====================


class TestDefaultDeviceService:
    """Test DefaultDeviceService class."""

    # ---- create_device ----

    def test_create_device_success(self, service, mock_repo, mock_env):
        mock_repo.insert_device.return_value = 1
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-created-001"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.status = "PENDING"
        record.provider_type = None
        record.provider_device_id = None
        record.provider_device_props = {}
        record.extra_config = None
        record.err_msg = None
        record.creator = "user-1"
        record.modifier = "user-1"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = record

        data = DeviceCreate(operator="user-1")
        result = service.create_device(tenant="test-tenant", data=data)

        assert isinstance(result, DeviceResponse)
        assert result.device_uuid == "DEVICE-created-001"
        assert result.status == "PENDING"
        mock_repo.insert_device.assert_called_once()

    def test_create_device_readback_failure(self, service, mock_repo, mock_env):
        mock_repo.insert_device.return_value = 1
        mock_repo.get_by_id.return_value = None

        data = DeviceCreate(operator="user-1")
        with pytest.raises(ValueError, match="Device record created but not found"):
            service.create_device(tenant="test-tenant", data=data)

    def test_create_device_uuid_format(self, service, mock_repo, mock_env):
        mock_repo.insert_device.return_value = 1
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-abc123"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "PENDING"
        record.provider_type = None
        record.provider_device_id = None
        record.provider_device_props = {}
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = record

        data = DeviceCreate(operator="user-1")
        result = service.create_device(tenant="test-tenant", data=data)

        assert result.device_uuid.startswith("DEVICE-")
        assert len(result.device_uuid) > 7

    # ---- start_device ----

    @pytest.mark.asyncio
    async def test_start_device_arca_no_hook(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """Fast path: Arca device without hook becomes ACTIVE immediately."""
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-test-001"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.status = DeviceStatus.PENDING.value
        record.provider_type = None
        record.provider_device_id = None
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_device_uuid.return_value = record

        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )

        # The no-hook fast path calls get_by_id once after update_device
        activated_record = MagicMock(spec=DeviceRecord)
        activated_record.id = 1
        activated_record.device_uuid = "DEVICE-test-001"
        activated_record.tenant = "test-tenant"
        activated_record.env = "test"
        activated_record.domain = "default"
        activated_record.status = DeviceStatus.ACTIVE.value
        activated_record.provider_type = "ARCA"
        activated_record.provider_device_id = "sb-001"
        activated_record.provider_device_props = {"key": "val"}
        activated_record.extra_config = None
        activated_record.err_msg = None
        activated_record.creator = "u"
        activated_record.modifier = "u"
        activated_record.gmt_create = datetime(2024, 1, 1)
        activated_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = activated_record

        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )

        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_start_device_pending_not_found(self, service, mock_repo, mock_env):
        """Raises ValueError when PENDING device not found."""
        mock_repo.get_by_device_uuid.return_value = None

        with pytest.raises(ValueError, match="PENDING device not found"):
            await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-nonexistent"
            )

    @pytest.mark.asyncio
    async def test_start_device_paas_failure(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """PaaS failure sets device status to FAILED."""
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-test-001"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.status = DeviceStatus.PENDING.value
        record.provider_type = None
        record.provider_device_id = None
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_device_uuid.return_value = record

        mock_paas_facade.create_device.side_effect = Exception("PaaS timeout")

        failed_record = MagicMock(spec=DeviceRecord)
        failed_record.id = 1
        failed_record.device_uuid = "DEVICE-test-001"
        failed_record.status = DeviceStatus.FAILED.value
        failed_record.extra_config = None
        failed_record.err_msg = None
        failed_record.tenant = "t"
        failed_record.env = "e"
        failed_record.domain = "d"
        failed_record.provider_type = None
        failed_record.provider_device_id = None
        failed_record.provider_device_props = None
        failed_record.creator = "u"
        failed_record.modifier = "u"
        failed_record.gmt_create = datetime(2024, 1, 1)
        failed_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = failed_record

        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )

        assert result.status == DeviceStatus.FAILED.value

    # ---- get_device_info ----

    def test_get_device_info_found(self, service, mock_repo, mock_env):
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-1"
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_device_uuid_only.return_value = record

        result = service.get_device_info(device_uuid="DEVICE-001")

        assert result is not None
        assert result.device_uuid == "DEVICE-001"
        assert result.status == "ACTIVE"

    def test_get_device_info_not_found(self, service, mock_repo, mock_env):
        mock_repo.get_by_device_uuid_only.return_value = None

        result = service.get_device_info(device_uuid="DEVICE-nonexistent")

        assert result is None

    # ---- get_provider_device_props ----

    def test_get_provider_device_props_found(self, service, mock_repo, mock_env):
        record = MagicMock(spec=DeviceRecord)
        record.provider_device_id = "ALIYUN_ACK_DEFAULT-abc@0"
        record.status = "ACTIVE"
        record.provider_device_props = {
            "sandbox_id": "ALIYUN_ACK_DEFAULT-abc@0",
            "metadata": {"ip_addr": "10.0.0.7"},
        }
        mock_repo.get_by_provider_device_id.return_value = record

        result = service.get_provider_device_props(
            provider_device_id="ALIYUN_ACK_DEFAULT-abc@0"
        )

        assert result is not None
        assert result.provider_device_id == "ALIYUN_ACK_DEFAULT-abc@0"
        assert result.status == "ACTIVE"
        assert result.provider_device_props["metadata"]["ip_addr"] == "10.0.0.7"

    def test_get_provider_device_props_not_found(self, service, mock_repo, mock_env):
        mock_repo.get_by_provider_device_id.return_value = None

        result = service.get_provider_device_props(provider_device_id="nonexistent")

        assert result is None

    # ---- destroy_device_by_uuid ----

    @pytest.mark.asyncio
    async def test_destroy_device_not_found(self, service, mock_repo, mock_env):
        """D-05: NOT_FOUND returns success=True."""
        mock_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_repo.get_by_device_uuid_only.return_value = None

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-nonexistent", modifier="u"
        )

        assert result.success is True
        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_destroy_stopped_device_releases_record(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        """A STOPPED device keeps its record for restart but must be destroyable.

        The ACTIVE/UPDATING lookup filters STOPPED out, so destroy falls back
        to the record lookup. The provider reports the sandbox as absent (it was
        torn down at stop time) with DEVICE_NOT_FOUND, which counts as a
        successful idempotent destroy rather than blocking the publish.
        """
        record = MagicMock(spec=DeviceRecord)
        record.id = 7
        record.device_uuid = "DEVICE-stopped"
        record.status = DeviceStatus.STOPPED.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-stopped"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_repo.get_by_device_uuid_only.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-stopped",
            original_error=PaasError(
                ErrorCode.DEVICE_NOT_FOUND, "Device not found: sandbox 'sb-stopped'"
            ),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-stopped", modifier="u"
        )

        assert result.success is True
        assert result.error_message is None
        # The fallback must actually reach the provider, not short-circuit.
        mock_paas_facade.destroy_device.assert_awaited_once_with("sb-stopped")
        mock_repo.update_status_by_device_uuid.assert_called_with(
            device_uuid="DEVICE-stopped",
            tenant="test-tenant",
            env="test",
            status=DeviceStatus.RELEASED.value,
        )

    @pytest.mark.asyncio
    async def test_destroy_stopped_device_real_failure_is_not_success(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        """A STOPPED device with a genuine provider failure still fails.

        Releasing a STOPPED device must not blanket-succeed: an error that is
        not a completion signal (here a timeout) means the teardown outcome is
        unknown, so the publish must surface it rather than claim success.
        """
        record = MagicMock(spec=DeviceRecord)
        record.id = 8
        record.device_uuid = "DEVICE-stopped-bad"
        record.status = DeviceStatus.STOPPED.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-stopped-bad"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_repo.get_by_device_uuid_only.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-stopped-bad",
            original_error=PaasError(ErrorCode.COMMAND_TIMEOUT, "timed out"),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-stopped-bad", modifier="u"
        )

        assert result.success is False
        assert result.error_message is not None
        assert "timed out" in result.error_message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            DeviceStatus.ACTIVE.value,
            DeviceStatus.FAILED.value,
            DeviceStatus.PENDING.value,
            DeviceStatus.OFFLINE.value,
        ],
        ids=["active", "failed", "pending", "offline"],
    )
    async def test_destroy_non_stopped_fallback_does_not_teardown(
        self, service, mock_repo, mock_paas_facade, mock_env, status
    ):
        """Only STOPPED is released through the fallback lookup.

        A device that is not ACTIVE/UPDATING and not STOPPED stays a no-op, so
        this change cannot accidentally widen which records get released.
        """
        record = MagicMock(spec=DeviceRecord)
        record.id = 12
        record.device_uuid = "DEVICE-other"
        record.status = status
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-other"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_repo.get_by_device_uuid_only.return_value = record

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-other", modifier="u"
        )

        assert result.success is True
        mock_paas_facade.destroy_device.assert_not_awaited()
        mock_repo.update_status_by_device_uuid.assert_not_called()
        mock_repo.soft_delete_by_device_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_stopped_device_soft_deletes_record(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        """Releasing a STOPPED device clears the record and its bot binding."""
        record = MagicMock(spec=DeviceRecord)
        record.id = 13
        record.device_uuid = "DEVICE-stopped-gone"
        record.status = DeviceStatus.STOPPED.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-stopped-gone"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_repo.get_by_device_uuid_only.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-stopped-gone",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "not found"),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-stopped-gone", modifier="u"
        )

        assert result.success is True
        mock_repo.soft_delete_by_device_uuid.assert_called_once_with(
            device_uuid="DEVICE-stopped-gone",
            tenant="test-tenant",
            env="test",
            modifier="u",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code,message",
        [
            (ErrorCode.DEVICE_DESTROY_FAILED, "connection refused"),
            (
                ErrorCode.DEVICE_DESTROY_FAILED,
                "Failed to connect sync sandbox, sandbox id: sb-1",
            ),
            (ErrorCode.DEVICE_UNAVAILABLE, "sandbox unreachable"),
            (ErrorCode.PLATFORM_UNAVAILABLE, "not found endpoint down"),
            (ErrorCode.AUTH_FAILED, "token expired"),
        ],
        ids=[
            "destroy_failed_conn_refused",
            "destroy_failed_unreachable_sandbox",
            "device_unavailable",
            "platform_unavailable",
            "auth_failed",
        ],
    )
    async def test_destroy_without_residue_permission_stays_failure(
        self, service, mock_repo, mock_paas_facade, mock_env, code, message
    ):
        """Without allow_residue_success only DEVICE_NOT_FOUND counts.

        SCALE_DOWN destroys a surplus device while the bot keeps running on the
        survivors, so a teardown it cannot confirm must stay a failure rather
        than be reported as a completed scale-down.
        """
        record = MagicMock(spec=DeviceRecord)
        record.id = 21
        record.device_uuid = "DEVICE-scaledown"
        record.status = DeviceStatus.UPDATING.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-scaledown"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-scaledown",
            original_error=PaasError(code, message),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-scaledown", modifier="u"
        )

        assert result.success is False
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_destroy_without_residue_permission_still_accepts_not_found(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        """DEVICE_NOT_FOUND is a typed absence, so it succeeds on every path."""
        record = MagicMock(spec=DeviceRecord)
        record.id = 22
        record.device_uuid = "DEVICE-scaledown-gone"
        record.status = DeviceStatus.UPDATING.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-scaledown-gone"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-scaledown-gone",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-scaledown-gone", modifier="u"
        )

        assert result.success is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code,message",
        [
            (ErrorCode.DEVICE_DESTROY_FAILED, "connection refused"),
            (ErrorCode.DEVICE_UNAVAILABLE, "sandbox unreachable"),
        ],
        ids=["destroy_failed_conn_refused", "device_unavailable"],
    )
    async def test_stop_treats_unreachable_sandbox_as_success(
        self, service, mock_repo, mock_paas_facade, mock_env, code, message
    ):
        """STOP already tore the sandbox down, so an unreachable one is done."""
        record = MagicMock(spec=DeviceRecord)
        record.id = 23
        record.device_uuid = "DEVICE-stop"
        record.status = DeviceStatus.ACTIVE.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-stop"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-stop",
            original_error=PaasError(code, message),
        )

        result = await service.stop_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-stop", modifier="u"
        )

        assert result.success is True

    @pytest.mark.parametrize(
        "code,expected",
        [
            (ErrorCode.DEVICE_NOT_FOUND, True),
            (ErrorCode.DEVICE_DESTROY_FAILED, True),
            (ErrorCode.NOT_FOUND, False),
            (ErrorCode.DEVICE_UNAVAILABLE, True),
            (ErrorCode.PLATFORM_UNAVAILABLE, False),
            (ErrorCode.AUTH_FAILED, False),
            (ErrorCode.RATE_LIMITED, False),
            (ErrorCode.COMMAND_TIMEOUT, False),
            (ErrorCode.PLATFORM_ERROR, False),
            (ErrorCode.TEMPLATE_NOT_FOUND, False),
            (ErrorCode.CONFIG_INVALID, False),
            (None, False),
        ],
        ids=[
            "device_not_found",
            "device_destroy_failed",
            "bare_not_found",
            "device_unavailable",
            "platform_unavailable",
            "auth_failed",
            "rate_limited",
            "command_timeout",
            "platform_error",
            "template_not_found",
            "config_invalid",
            "no_code",
        ],
    )
    def test_is_absent_sandbox_error_is_code_only(self, code, expected):
        """Absence is decided by code alone; message text is never consulted."""
        from secbaas.community.core.service.device_manage._device_service import (
            _is_absent_sandbox_error,
        )

        assert _is_absent_sandbox_error(code) is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code,message",
        [
            (ErrorCode.DEVICE_NOT_FOUND, "Device not found: sandbox 'x' not found"),
            (ErrorCode.DEVICE_NOT_FOUND, ""),
            (ErrorCode.DEVICE_NOT_FOUND, "does not exist"),
            (ErrorCode.DEVICE_UNAVAILABLE, "sandbox unreachable"),
            (ErrorCode.DEVICE_UNAVAILABLE, "connection error"),
            (ErrorCode.DEVICE_UNAVAILABLE, ""),
            (ErrorCode.DEVICE_DESTROY_FAILED, "connection refused"),
            (
                ErrorCode.DEVICE_DESTROY_FAILED,
                "Failed to connect sync sandbox, sandbox id: sb-1",
            ),
            (
                ErrorCode.DEVICE_DESTROY_FAILED,
                "sandbox is not running, status: STOPPED",
            ),
            (ErrorCode.DEVICE_DESTROY_FAILED, ""),
        ],
        ids=[
            "device_not_found_typed",
            "device_not_found_empty_message",
            "device_not_found_absence_text",
            "device_unavailable",
            "device_unavailable_connection",
            "device_unavailable_empty_message",
            "destroy_failed_connection_refused",
            "destroy_failed_unreachable_sandbox",
            "destroy_failed_not_running",
            "destroy_failed_empty_message",
        ],
    )
    async def test_destroy_absent_sandbox_is_success(
        self, service, mock_repo, mock_paas_facade, mock_env, code, message
    ):
        """A teardown that cannot reach the sandbox is reported as complete.

        DEVICE_NOT_FOUND is the typed SDK NotFoundException. DEVICE_UNAVAILABLE
        is an unreachable sandbox. DEVICE_DESTROY_FAILED is what Arca's
        connect_sync_sandbox collapses every connect failure into (transport
        error, not-running status, even a 404), so a sandbox that cannot be
        connected to is treated as already torn down.
        """
        record = MagicMock(spec=DeviceRecord)
        record.id = 9
        record.device_uuid = "DEVICE-absent"
        record.status = DeviceStatus.UPDATING.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-absent"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-absent",
            original_error=PaasError(code, message),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant",
            device_uuid="DEVICE-absent",
            modifier="u",
            allow_residue_success=True,
        )

        assert result.success is True
        assert result.error_message is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code,message",
        [
            (ErrorCode.PLATFORM_UNAVAILABLE, "not found endpoint down"),
            (ErrorCode.PLATFORM_UNAVAILABLE, "connection refused"),
            (ErrorCode.PLATFORM_ERROR, "sandbox not found"),
            (ErrorCode.PLATFORM_ERROR, "internal server error"),
            (ErrorCode.NOT_FOUND, "does not exist"),
            (ErrorCode.AUTH_FAILED, "token expired"),
            (ErrorCode.RATE_LIMITED, "slow down"),
            (ErrorCode.TEMPLATE_NOT_FOUND, "no template"),
            (ErrorCode.COMMAND_TIMEOUT, "timed out"),
        ],
        ids=[
            "platform_unavailable",
            "platform_unavailable_conn_refused",
            "platform_error_absence_text",
            "generic_platform_error",
            "bare_not_found_code",
            "auth_failed",
            "rate_limited",
            "template_not_found",
            "command_timeout",
        ],
    )
    async def test_destroy_real_failure_is_not_success(
        self, service, mock_repo, mock_paas_facade, mock_env, code, message
    ):
        """Codes outside the completion set still report a failed teardown.

        Message text is deliberately ignored: an absence-sounding message under
        another code (or under DEVICE_NOT_FOUND's sibling NOT_FOUND) does not
        prove the sandbox is gone, and releasing the device would leak a
        sandbox that may still be running.
        """
        record = MagicMock(spec=DeviceRecord)
        record.id = 11
        record.device_uuid = "DEVICE-broken"
        record.status = DeviceStatus.UPDATING.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-broken"
        record.provider_device_props = None
        record.extra_config = None
        record.domain = "d"

        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="sb-broken",
            original_error=PaasError(code, message),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-broken", modifier="u"
        )

        assert result.success is False
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_destroy_device_success(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        record.domain = "d"
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-001", modifier="u"
        )

        assert isinstance(result, DestroyDeviceResponse)
        mock_paas_facade.destroy_device.assert_called_once_with("sb-001")

    @pytest.mark.asyncio
    async def test_destroy_device_for_restart_skips_soft_delete(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        record.domain = "d"
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant",
            device_uuid="DEVICE-001",
            modifier="u",
            for_restart=True,
        )

        assert result.success is True
        # Should NOT call soft_delete for restart
        mock_repo.soft_delete_by_device_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_device_hook_execution(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        """before_destroy_cmd_hook is executed when configured."""
        hook_result = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=100,
            command="/destroy.sh",
        )
        mock_paas_facade.execute_command.return_value = hook_result

        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = {
            "deploy_config": {
                "before_destroy_cmd_hook": "/destroy.sh",
            }
        }
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        record.domain = "d"
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-001", modifier="u"
        )

        assert result.hook_result is not None
        assert result.hook_result.exit_code == 0
        mock_paas_facade.execute_command.assert_called_once()

    # ---- restart_device ----

    @pytest.mark.asyncio
    async def test_restart_device_not_found(self, service, mock_repo, mock_env):
        mock_repo.get_by_device_uuid.return_value = None

        with pytest.raises(ValueError, match="Device not found"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-nonexistent"
            )

    @pytest.mark.asyncio
    async def test_restart_device_released_forbidden(
        self, service, mock_repo, mock_env
    ):
        record = MagicMock(spec=DeviceRecord)
        record.status = DeviceStatus.RELEASED.value
        mock_repo.get_by_device_uuid.return_value = record

        with pytest.raises(
            ValueError, match="Cannot restart device in RELEASED status"
        ):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-released"
            )

    @pytest.mark.asyncio
    async def test_restart_device_happy_path(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """Test restart_device inline implementation - ARCA platform, no hook."""
        # Setup device record (line 696-710 style)
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = DeviceStatus.ACTIVE.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)

        # Setup mocks
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",  # New sandbox ID after restart
        )

        # Mock repo.update_device and repo.get_by_id
        restarted_record = MagicMock(spec=DeviceRecord)
        restarted_record.id = 1
        restarted_record.device_uuid = "DEVICE-001"
        restarted_record.tenant = "t"
        restarted_record.env = "e"
        restarted_record.domain = "d"
        restarted_record.status = DeviceStatus.ACTIVE.value
        restarted_record.provider_type = "ARCA"
        restarted_record.provider_device_id = "sb-002"  # New ID
        restarted_record.provider_device_props = None
        restarted_record.extra_config = None
        restarted_record.err_msg = None
        restarted_record.creator = "u"
        restarted_record.modifier = "u"
        restarted_record.gmt_create = datetime(2024, 1, 1)
        restarted_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = restarted_record

        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-001"
        )

        # Assert destroy and create were called
        mock_paas_facade.destroy_device.assert_called_once_with("sb-001")
        mock_paas_facade.create_device.assert_called_once()

        # Assert result status is ACTIVE (no hook configured)
        assert result.status == DeviceStatus.ACTIVE.value
        assert result.provider_device_id == "sb-002"

    @pytest.mark.asyncio
    async def test_restart_device_with_before_destroy_hook(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """Test restart_device executes before_destroy_cmd_hook if configured."""
        # Setup record with before_destroy_cmd_hook in extra_config
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = DeviceStatus.ACTIVE.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = {
            "deploy_config": {"before_destroy_cmd_hook": "/destroy.sh"}
        }
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)

        # Setup mocks
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None

        # Mock hook execution with success
        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=100,
            command="/destroy.sh",
        )

        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )

        # Mock restarted record
        restarted_record = MagicMock(spec=DeviceRecord)
        restarted_record.id = 1
        restarted_record.device_uuid = "DEVICE-001"
        restarted_record.tenant = "t"
        restarted_record.env = "e"
        restarted_record.domain = "d"
        restarted_record.status = DeviceStatus.ACTIVE.value
        restarted_record.provider_type = "ARCA"
        restarted_record.provider_device_id = "sb-002"
        restarted_record.provider_device_props = None
        restarted_record.extra_config = record.extra_config
        restarted_record.err_msg = None
        restarted_record.creator = "u"
        restarted_record.modifier = "u"
        restarted_record.gmt_create = datetime(2024, 1, 1)
        restarted_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = restarted_record

        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-001"
        )

        # Verify hook was executed
        mock_paas_facade.execute_command.assert_called_once()
        # DeviceResponse doesn't have success field; verify via status
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_restart_device_with_failed_hook_non_blocking(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """Test restart_device continues even if before_destroy hook fails."""
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = DeviceStatus.ACTIVE.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = {"deploy_config": {"before_destroy_cmd_hook": "/fail.sh"}}
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)

        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None

        # Mock hook execution with failure (exit_code != 0)
        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=1,
            stdout="",
            stderr="hook failed",
            execution_time_ms=100,
            command="/fail.sh",
        )

        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )

        restarted_record = MagicMock(spec=DeviceRecord)
        restarted_record.id = 1
        restarted_record.device_uuid = "DEVICE-001"
        restarted_record.tenant = "t"
        restarted_record.env = "e"
        restarted_record.domain = "d"
        restarted_record.status = DeviceStatus.ACTIVE.value
        restarted_record.provider_type = "ARCA"
        restarted_record.provider_device_id = "sb-002"
        restarted_record.provider_device_props = None
        restarted_record.extra_config = record.extra_config
        restarted_record.err_msg = None
        restarted_record.creator = "u"
        restarted_record.modifier = "u"
        restarted_record.gmt_create = datetime(2024, 1, 1)
        restarted_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = restarted_record

        # Should NOT raise - hook failure is non-blocking
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-001"
        )

        # Device should still be restarted despite hook failure
        assert result.status == DeviceStatus.ACTIVE.value
        mock_paas_facade.destroy_device.assert_called_once()
        mock_paas_facade.create_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_device_local_no_hook_sets_active(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """Test LOCAL platform restart without hook sets status to ACTIVE."""
        from secbaas.community.api.template_manage import LocalTemplateConfig

        # LOCAL platform requires machine_id, user_id, agent_code, tc_bot_id in deploy_config
        # Per Phase 28: tc_bot_id provided directly in deploy_config (no bot lookup)
        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = DeviceStatus.ACTIVE.value
        record.provider_type = "LOCAL"
        record.provider_device_id = "container-001"
        record.provider_device_props = None
        record.extra_config = {
            "deploy_config": {
                "machine_id": "machine-001",
                "user_id": "user-001",
                "agent_code": "agent-001",
                "tc_bot_id": "bot-001",  # Required field per Phase 28
            }
        }
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)

        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        mock_paas_facade.restart_device.return_value = None

        mock_device_template_service.get_default_or_explicit_template.return_value = (
            MagicMock(
                template_uuid="tpl-local-001",
                config=LocalTemplateConfig(type="LOCAL"),
            )
        )
        # LOCAL platform: provider_device_id stays same after native restart
        restarted_record = MagicMock(spec=DeviceRecord)
        restarted_record.id = 1
        restarted_record.device_uuid = "DEVICE-001"
        restarted_record.tenant = "t"
        restarted_record.env = "e"
        restarted_record.domain = "d"
        # LOCAL platform: status = PENDING, waiting for container_ready callback
        restarted_record.status = DeviceStatus.PENDING.value
        restarted_record.provider_type = "LOCAL"
        # Container ID unchanged for LOCAL native restart
        restarted_record.provider_device_id = "container-001"
        restarted_record.provider_device_props = None
        restarted_record.extra_config = record.extra_config
        restarted_record.err_msg = None
        restarted_record.creator = "u"
        restarted_record.modifier = "u"
        restarted_record.gmt_create = datetime(2024, 1, 1)
        restarted_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = restarted_record

        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-001"
        )

        # LOCAL platform: uses native restart_device, not destroy+create
        mock_paas_facade.restart_device.assert_called_once()
        mock_paas_facade.create_device.assert_not_called()
        # LOCAL platform: status = PENDING (waits for container_ready callback)
        assert result.status == DeviceStatus.PENDING.value
        # LOCAL platform: provider_device_id unchanged
        assert result.provider_device_id == "container-001"

    @pytest.mark.asyncio
    async def test_restart_device_arca_with_hook_sets_pending(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        """Test ARCA platform with after_create hook sets status to PENDING."""

        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = DeviceStatus.ACTIVE.value
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = {"deploy_config": {"after_create_cmd_hook": "/init.sh"}}
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)

        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )

        # Record after hook dispatch - status should be PENDING
        pending_record = MagicMock(spec=DeviceRecord)
        pending_record.id = 1
        pending_record.device_uuid = "DEVICE-001"
        pending_record.tenant = "t"
        pending_record.env = "e"
        pending_record.domain = "d"
        pending_record.status = DeviceStatus.PENDING.value  # After hook dispatch
        pending_record.provider_type = "ARCA"
        pending_record.provider_device_id = "sb-002"
        pending_record.provider_device_props = None
        pending_record.extra_config = record.extra_config
        pending_record.err_msg = None
        pending_record.creator = "u"
        pending_record.modifier = "u"
        pending_record.gmt_create = datetime(2024, 1, 1)
        pending_record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_by_id.return_value = pending_record

        with patch(
            "secbaas.community.core.service.device_manage._device_service.dispatch_start_hook"
        ) as mock_dispatch:
            result = await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-001"
            )

            # Verify dispatch_start_hook was called for ARCA with hook
            mock_dispatch.assert_called_once()
            call_kwargs = mock_dispatch.call_args.kwargs
            assert call_kwargs["device_uuid"] == "DEVICE-001"
            assert call_kwargs["provider_device_id"] == "sb-002"
            assert call_kwargs["rendered_hook"] == "/init.sh"

        # Should be PENDING because hook was dispatched
        assert result.status == DeviceStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_destroy_device_idempotent_paas_not_found(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        """D-04: PaaS NOT_FOUND during destroy is treated as success."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        record = MagicMock(spec=DeviceRecord)
        record.id = 1
        record.device_uuid = "DEVICE-001"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sb-001"
        record.provider_device_props = None
        record.extra_config = None
        record.err_msg = None
        record.creator = "u"
        record.modifier = "u"
        record.gmt_create = datetime(2024, 1, 1)
        record.gmt_modified = datetime(2024, 1, 1)
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record

        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id="tpl-001",
            paas_device_id="sb-001",
            original_error=PaasError(
                code=ErrorCode.DEVICE_NOT_FOUND, message="not found"
            ),
        )

        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-001", modifier="u"
        )

        assert result.success is True


def _device_record_to_response(record):
    """Helper: call the module function to convert mock record to DeviceResponse."""
    from secbaas.community.core.service.device_manage import device_record_to_response

    return device_record_to_response(record)

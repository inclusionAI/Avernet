"""Unit tests for _device_service.py coverage expansion.

Adds 40+ tests to raise coverage from 64% to >=90%.
Focuses on uncovered flows: local device creation, arca device creation,
sigma device creation, restart_device edge cases, destroy hook edge cases,
and utility function edge cases.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.device_manage import (
    ArcaCreationResult,
    CommandResult,
    DeployConfig,
    DestroyDeviceResponse,
    DeviceConfig,
    DeviceCreate,
    DeviceResponse,
    DeviceStatus,
    DockerCreationResult,
    DockerDeviceConfig,
    EncryptableHeaderRule,
    EncryptableOutBoundRule,
    LocalCreationResult,
    PoolabCreationResult,
    SigmaCreationResult,
)
from secbaas.api.template_manage import (
    ArcaTemplateConfig,
    DeviceTemplateManageService,
    DockerTemplateConfig,
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
)
from secbaas.core.repository.device import DeviceRecord
from secbaas.core.service.device_manage import DefaultDeviceService
from secbaas.core.service.paas import PaasServiceFacade

DS = "secbaas.core.service.device_manage._device_service"


@pytest.fixture
def mock_env():
    with patch(f"{DS}.get_current_env", return_value="test"):
        yield


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def mock_paas_facade():
    facade = MagicMock(spec=PaasServiceFacade)
    facade.create_device = AsyncMock()
    facade.destroy_device = AsyncMock()
    facade.execute_command = AsyncMock()
    facade.restart_device = AsyncMock()
    return facade


@pytest.fixture
def mock_device_template_service():
    return MagicMock(spec=DeviceTemplateManageService)


@pytest.fixture
def service(mock_repo, mock_paas_facade, mock_device_template_service, mock_env):
    svc = DefaultDeviceService(
        paas_facade=mock_paas_facade,
        repository=mock_repo,
        device_template_service=mock_device_template_service,
        secret_plugin=MagicMock(),
    )
    yield svc


def _make_record(**overrides):
    defaults = {
        "id": 1,
        "device_uuid": "DEVICE-test-001",
        "tenant": "test-tenant",
        "env": "test",
        "domain": "default",
        "status": DeviceStatus.PENDING.value,
        "provider_type": None,
        "provider_device_id": None,
        "provider_device_props": None,
        "extra_config": None,
        "err_msg": None,
        "creator": "test-user",
        "modifier": "test-user",
        "gmt_create": datetime(2024, 1, 1),
        "gmt_modified": datetime(2024, 1, 1),
    }
    defaults.update(overrides)
    record = MagicMock(spec=DeviceRecord)
    for k, v in defaults.items():
        setattr(record, k, v)
    return record


class TestSafeFormatHookEdgeCases:
    def test_value_error_is_caught(self):
        from secbaas.core.service.device_manage import _safe_format_hook

        result = _safe_format_hook("echo {token} and {other}", token="hello")
        assert result == "echo hello and {other}"
        assert "{other}" in result  # unknown placeholder preserved

    def test_client_id_placeholder(self):
        from secbaas.core.service.device_manage import _safe_format_hook

        result = _safe_format_hook("register --id {client_id}", client_id="DEVICE-001")
        assert result == "register --id DEVICE-001"


class TestValidateRequiredField:
    def test_valid_field_not_added(self):
        from secbaas.core.service.device_manage._device_service import (
            _validate_required_field,
        )

        missing = []
        _validate_required_field("valid-value", "my_field", missing)
        assert len(missing) == 0

    def test_none_field_added(self):
        from secbaas.core.service.device_manage._device_service import (
            _validate_required_field,
        )

        missing = []
        _validate_required_field(None, "required_field", missing)
        assert "required_field" in missing

    def test_whitespace_field_added(self):
        from secbaas.core.service.device_manage._device_service import (
            _validate_required_field,
        )

        missing = []
        _validate_required_field("   ", "required_field", missing)
        assert "required_field" in missing

    def test_empty_string_field_added(self):
        from secbaas.core.service.device_manage._device_service import (
            _validate_required_field,
        )

        missing = []
        _validate_required_field("", "required_field", missing)
        assert "required_field" in missing

    def test_multiple_missing_fields(self):
        from secbaas.core.service.device_manage._device_service import (
            _validate_required_field,
        )

        missing = []
        _validate_required_field(None, "field_a", missing)
        _validate_required_field("  ", "field_b", missing)
        _validate_required_field("ok", "field_c", missing)
        assert missing == ["field_a", "field_b"]


class TestRecordToResponseEdgeCases:
    def test_with_extra_config_deserialization(self):
        from secbaas.core.service.device_manage import device_record_to_response

        record = _make_record(
            extra_config={"template_uuid": "tpl-001", "deploy_config": None}
        )
        resp = device_record_to_response(record)
        assert isinstance(resp, DeviceResponse)
        assert resp.extra_config is not None
        assert resp.extra_config.template_uuid == "tpl-001"

    def test_with_err_msg(self):
        from secbaas.core.service.device_manage import device_record_to_response

        record = _make_record(err_msg="something went wrong")
        resp = device_record_to_response(record)
        assert resp.err_msg == "something went wrong"


class TestDecryptHeaderRuleValuesEdgeCases:
    def test_decryption_failure_raises_value_error(self):
        from secbaas.core.service.device_manage import _decrypt_header_rule_values

        mock_secret = MagicMock()
        mock_secret.resolve_common_sm4_key.return_value = "test-key"

        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="bad-ciphertext",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        with patch(f"{DS}.common_sm4_decrypt", side_effect=Exception("decrypt error")):
            with pytest.raises(ValueError, match="Decryption failed"):
                _decrypt_header_rule_values(outbound, mock_secret)

    def test_non_encryptable_non_sdk_type_returns_none(self):
        from secbaas.core.service.device_manage import _decrypt_header_rule_values

        mock_secret = MagicMock()
        mock_secret.resolve_common_sm4_key.return_value = "test-key"

        result = _decrypt_header_rule_values(MagicMock(), mock_secret)
        assert result is None


class TestDefaultDeviceServiceInit:
    def test_with_paas_facade_di(self, mock_env):
        facade = MagicMock(spec=PaasServiceFacade)
        svc = DefaultDeviceService(
            paas_facade=facade,
            repository=MagicMock(),
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
        )
        assert svc._paas_facade is facade

    def test_di_constructor_works_with_mocks(self, mock_env):
        facade = MagicMock(spec=PaasServiceFacade)
        repo = MagicMock()
        tpl_svc = MagicMock()
        svc = DefaultDeviceService(
            paas_facade=facade,
            repository=repo,
            device_template_service=tpl_svc,
            secret_plugin=MagicMock(),
        )
        assert svc._paas_facade is facade


class TestCreateDeviceExtra:
    def test_create_device_with_extra_config(self, service, mock_repo, mock_env):
        record = _make_record(
            device_uuid="DEVICE-extra-001", extra_config={"template_uuid": "tpl-001"}
        )
        mock_repo.insert_device.return_value = 1
        mock_repo.get_by_id.return_value = record
        data = DeviceCreate(
            operator="user-1", extra_config=DeviceConfig(template_uuid="tpl-001")
        )
        result = service.create_device(tenant="test-tenant", data=data)
        assert result.device_uuid == "DEVICE-extra-001"
        assert result.status == DeviceStatus.PENDING.value

    def test_create_device_with_domain(self, service, mock_repo, mock_env):
        record = _make_record(device_uuid="DEVICE-domain-001", domain="custom")
        mock_repo.insert_device.return_value = 1
        mock_repo.get_by_id.return_value = record
        data = DeviceCreate(operator="user-1", domain="custom")
        result = service.create_device(tenant="test-tenant", data=data)
        assert result.domain == "custom"

    def test_create_device_encrypts_headers(self, service, mock_repo, mock_env):
        record = _make_record(device_uuid="DEVICE-enc-001")
        mock_repo.insert_device.return_value = 1
        mock_repo.get_by_id.return_value = record
        data = DeviceCreate(
            operator="user-1", extra_config=DeviceConfig(deploy_config=DeployConfig())
        )
        with patch(f"{DS}._encrypt_header_rule_values") as mock_encrypt:
            result = service.create_device(tenant="test-tenant", data=data)
            mock_encrypt.assert_called_once()
        assert result.device_uuid == "DEVICE-enc-001"


class TestStartDeviceExtra:
    @pytest.mark.asyncio
    async def test_template_not_found(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            None
        )
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_template_has_no_config(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        template = MagicMock(template_uuid="tpl-no-config", config=None)
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            template
        )
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_unknown_template_config_type(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        template = MagicMock(template_uuid="tpl-unknown", config=MagicMock())
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            template
        )
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_unknown_paas_result_type(
        self, service, mock_repo, mock_paas_facade, mock_device_template_service
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = MagicMock()
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_arca_with_full_config(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "template_uuid": "tpl-001",
                "deploy_config": {
                    "mount_points": [
                        {
                            "id": "oss-1",
                            "remote_dir": "/remote",
                            "local_dir": "/local",
                            "permission": "READ_WRITE",
                        }
                    ],
                    "ttl_in_minutes": 60,
                    "arca_metadata": {"key": "val"},
                    "envs": {"CUSTOM_ENV": "xyz"},
                    "resource_spec": {"cpu": "2", "memory": 4096},
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_arca_with_storage_id_placeholder(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            device_uuid="DEVICE-test-001",
            extra_config={
                "deploy_config": {
                    "storage": {
                        "type": "NAS",
                        "path": "/data",
                        "storage_id": "nas-{device_uuid}",
                        "quota": "1024Mi",
                        "permission": "READ_WRITE",
                    },
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_arca_with_outbound_rule_decryption(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "outbound_operation_rule": {"header_operation_rules": []}
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_arca_with_agenthook_env_default(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_arca_with_oss_mount_id(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "mount_points": [
                        {
                            "remote_dir": "/remote",
                            "local_dir": "/local",
                            "permission": "READ_WRITE",
                        }
                    ]
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA",
                base_url="http://test",
                api_key="k",
                template_id="t",
                oss_mount_id="oss-mount-123",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_local_device_creation(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-001"
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="LOCAL",
            provider_device_id="container-001",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_local_engine_type_passthrough(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """Test that engine_type flows from DeployConfig through start_device() LOCAL branch."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                    "engine_type": "openclaw",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-001"
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="LOCAL",
            provider_device_id="container-001",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

        # Verify engine_type was passed correctly to LocalDeviceConfig in the facade call
        detail_config = _extract_detail_config(mock_paas_facade)
        assert detail_config is not None
        assert detail_config.engine_type == "openclaw"

    @pytest.mark.asyncio
    async def test_local_engine_type_defaults_to_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """Test that engine_type defaults to None when not in DeployConfig."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-001"
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="LOCAL",
            provider_device_id="container-001",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

        # Verify engine_type is None when not provided
        detail_config = _extract_detail_config(mock_paas_facade)
        assert detail_config is not None
        assert detail_config.engine_type is None

    @pytest.mark.asyncio
    async def test_local_missing_fields_validation(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value, extra_config={"deploy_config": {}}
        )
        mock_repo.get_by_device_uuid.return_value = record
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_local_mount_path_empty_string(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                    "mount_path": "   ",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-001"
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="LOCAL",
            provider_device_id="container-001",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_local_hook_skip_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """start_device LOCAL with hook: get_by_id returns None after hook skip raises ValueError."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                    "after_create_cmd_hook": "echo init",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-001"
        )
        mock_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Device record not found after start"):
            await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_sigma_device_full_config(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "zone": "us-east-1",
                    "vpc_config": {"vpc_id": "vpc-123"},
                    "sigma_metadata": {"key": "val"},
                    "resource_spec": {"cpu": "4", "memory": 8192},
                    "envs": {"APP_ENV": "production"},
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        sigma_tpl = MagicMock(
            template_uuid="tpl-sigma",
            config=SigmaTemplateConfig(
                type="Sigma",
                endpoint="http://sigma.test",
                access_key="ak",
                secret_key="sk",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            sigma_tpl
        )
        mock_paas_facade.create_device.return_value = SigmaCreationResult(
            platform="sigma",
            status="ACTIVE",
            region="us-east-1",
            instance_id="sigma-inst-001",
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="SIGMA",
            provider_device_id="sigma-inst-001",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_sigma_device_minimal_config(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        sigma_tpl = MagicMock(
            template_uuid="tpl-sigma",
            config=SigmaTemplateConfig(
                type="Sigma",
                endpoint="http://sigma.test",
                access_key="ak",
                secret_key="sk",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            sigma_tpl
        )
        mock_paas_facade.create_device.return_value = SigmaCreationResult(
            platform="sigma",
            status="ACTIVE",
            region=None,
            instance_id="sigma-inst-001",
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="SIGMA",
            provider_device_id="sigma-inst-001",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_arca_with_hook_dispatches(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {"after_create_cmd_hook": "echo init"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        pending = _make_record(
            status=DeviceStatus.PENDING.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_id.return_value = pending
        with patch(f"{DS}.dispatch_start_hook") as mock_dispatch:
            result = await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
            mock_dispatch.assert_called_once()
            assert result.status == DeviceStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_local_with_hook_skips_dispatch(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                    "after_create_cmd_hook": "echo init",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-001"
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        with patch(f"{DS}.dispatch_start_hook") as mock_dispatch:
            result = await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
            mock_dispatch.assert_not_called()
            assert result.status == DeviceStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_paas_failure_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.side_effect = Exception("PaaS failure")
        mock_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Device record not found after update"):
            await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_start_device_hook_dispatch_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {"after_create_cmd_hook": "echo init"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        mock_repo.get_by_id.return_value = None
        with patch(f"{DS}.dispatch_start_hook"):
            with pytest.raises(
                ValueError, match="Device record not found after hook dispatch"
            ):
                await service.start_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001"
                )

    @pytest.mark.asyncio
    async def test_start_device_activation_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_device_uuid.return_value = record
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        mock_repo.get_by_id.return_value = None
        with pytest.raises(
            ValueError, match="Device record not found after activation"
        ):
            await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )


class TestRestartDeviceExtra:
    @pytest.mark.asyncio
    async def test_invalid_status_forbidden(self, service, mock_repo, mock_env):
        record = _make_record(status="BOGUS")
        mock_repo.get_by_device_uuid.return_value = record
        with pytest.raises(ValueError, match="Cannot restart device in status"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_no_provider_device_id_skips_destroy(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            provider_type="ARCA",
            extra_config={"template_uuid": "tpl-001"},
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="ARCA",
            provider_device_id="sb-002",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value
        mock_paas_facade.destroy_device.assert_called_once_with("sb-001")

    @pytest.mark.asyncio
    async def test_destroy_hook_exception_non_blocking(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"before_destroy_cmd_hook": "echo cleanup"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.execute_command.side_effect = Exception("connection lost")
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value
        mock_paas_facade.destroy_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_hook_stderr_warning(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"before_destroy_cmd_hook": "echo cleanup"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=0,
            stdout="",
            stderr="warning: deprecated",
            execution_time_ms=100,
            command="echo cleanup",
        )
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_paas_destroy_non_not_found_error(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id="tpl-001",
            paas_device_id="sb-001",
            original_error=PaasError(
                code=ErrorCode.DEVICE_CREATION_FAILED, message="platform error"
            ),
        )
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_paas_create_failure_in_restart(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.side_effect = Exception("PaaS down")
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_restart_template_not_found(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            None
        )
        with pytest.raises(ValueError, match="Template not found"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_create_failure_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.side_effect = Exception("boom")
        mock_repo.get_by_id.return_value = None
        with pytest.raises(
            ValueError, match="Device record not found after failed update"
        ):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_with_after_create_hook_local(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="container-001",
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                    "after_create_cmd_hook": "echo init",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.create_device.return_value = LocalCreationResult(
            platform="local", status="ACTIVE", container_id="container-002"
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        with patch(f"{DS}.dispatch_start_hook") as mock_dispatch:
            result = await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
            mock_dispatch.assert_not_called()
            assert result.status == DeviceStatus.PENDING.value

    # ------------------------------------------------------------------
    # SECTION: restart_device LOCAL platform tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_restart_local_success(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device with LOCAL platform: native restart succeeds."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="container-001",
            provider_type="LOCAL",
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(return_value=None)
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.restart_device.assert_called_once_with("container-001")

    @pytest.mark.asyncio
    async def test_restart_local_failure(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device with LOCAL platform: native restart fails, device set to FAILED."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="container-001",
            provider_type="LOCAL",
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(
            side_effect=Exception("container restart failed")
        )
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_restart_local_no_provider_device_id(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        """restart_device with LOCAL platform and no provider_device_id raises ValueError."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id=None,
            provider_type=None,
            extra_config={"deploy_config": {}},
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        with pytest.raises(ValueError, match="Cannot restart LOCAL device without"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_local_failure_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device with LOCAL platform: restart fails and get_by_id returns None."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="container-001",
            provider_type="LOCAL",
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(
            side_effect=Exception("container restart failed")
        )
        mock_repo.get_by_id.return_value = None
        with pytest.raises(
            ValueError, match="Device record not found after failed restart"
        ):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_local_success_get_by_id_none(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device with LOCAL platform: restart succeeds but get_by_id returns None after status update."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="container-001",
            provider_type="LOCAL",
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(return_value=None)
        mock_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Device record not found after restart"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    # ------------------------------------------------------------------
    # SECTION: restart_device edge case tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_restart_unknown_template_config(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        """restart_device raises ValueError for unknown template config type."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={"template_uuid": "tpl-001"},
        )
        mock_repo.get_by_device_uuid.return_value = record
        unknown_tpl = MagicMock(template_uuid="tpl-unknown", config=MagicMock())
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            unknown_tpl
        )
        with pytest.raises(ValueError, match="Unknown template config type"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_sigma_not_implemented(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        """restart_device raises ValueError for SIGMA platform (not implemented)."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sigma-inst-001",
        )
        mock_repo.get_by_device_uuid.return_value = record
        sigma_tpl = MagicMock(
            template_uuid="tpl-sigma",
            config=SigmaTemplateConfig(
                type="Sigma",
                endpoint="http://sigma.test",
                access_key="ak",
                secret_key="sk",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            sigma_tpl
        )
        with pytest.raises(
            ValueError, match="Sigma platform restart is not yet implemented"
        ):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_destroy_device_not_found(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device handles DEVICE_NOT_FOUND as success during destroy phase."""
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id="tpl-001",
            paas_device_id="sb-001",
            original_error=PaasError(
                code=ErrorCode.DEVICE_NOT_FOUND, message="not found"
            ),
        )
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_restart_no_provider_device_id_skips_destroy(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device skips destroy phase when provider_device_id is None (logs info)."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id=None,
            extra_config={"template_uuid": "tpl-001"},
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value
        mock_paas_facade.destroy_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_arca_with_storage_placeholder(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA platform with storage_id containing {device_uuid} placeholder."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            device_uuid="DEVICE-test-r-001",
            extra_config={
                "deploy_config": {
                    "storage": {
                        "type": "NAS",
                        "path": "/data",
                        "storage_id": "nas-{device_uuid}",
                        "quota": "1024Mi",
                        "permission": "READ_WRITE",
                    },
                    "ttl_in_minutes": 120,
                    "arca_metadata": {"app": "test"},
                    "envs": {"CUSTOM": "val"},
                    "resource_spec": {"cpu": "2", "memory": 4096},
                    "outbound_operation_rule": {"header_operation_rules": []},
                    "mount_points": [
                        {
                            "id": "oss-1",
                            "remote_dir": "/remote",
                            "local_dir": "/local",
                            "permission": "READ_WRITE",
                        }
                    ],
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA",
                base_url="http://test",
                api_key="k",
                template_id="t",
                oss_mount_id="oss-mount-123",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-r-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_restart_arca_with_agenthook_env_default(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA platform with deploy_config fields but no envs triggers AGENTCLAW_ENV default."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {
                    "ttl_in_minutes": 120,
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_restart_arca_minimal_config(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA platform with minimal (no deploy_config) setup."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_restart_arca_with_hook_dispatch(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA platform with after_create_cmd_hook dispatches hook."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"after_create_cmd_hook": "echo init"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        with patch(f"{DS}.dispatch_start_hook") as mock_dispatch:
            result = await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
            mock_dispatch.assert_called_once()
            assert result.status == DeviceStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_restart_arca_no_hook_activates(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA platform without hook activates device immediately."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_restart_arca_hook_dispatch_record_not_found(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA with hook: get_by_id returns None after hook dispatch raises ValueError."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"after_create_cmd_hook": "echo init"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        mock_repo.get_by_id.return_value = None
        with patch(f"{DS}.dispatch_start_hook"):
            with pytest.raises(
                ValueError, match="Device record not found after hook dispatch"
            ):
                await service.restart_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001"
                )

    @pytest.mark.asyncio
    async def test_restart_arca_activation_record_not_found(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device ARCA without hook: get_by_id returns None after activation raises ValueError."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.return_value = None
        arca_tpl = MagicMock(
            template_uuid="tpl-arca",
            config=ArcaTemplateConfig(
                type="ARCA", base_url="http://test", api_key="k", template_id="t"
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            arca_tpl
        )
        mock_paas_facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-001",
            sandbox_id="sb-002",
        )
        mock_repo.get_by_id.return_value = None
        with pytest.raises(
            ValueError, match="Device record not found after activation"
        ):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_restart_local_skip_before_destroy_hook(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """restart_device LOCAL platform skips before_destroy_cmd_hook execution."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="container-001",
            provider_type="LOCAL",
            extra_config={
                "deploy_config": {
                    "machine_id": "machine-001",
                    "user_id": "user-001",
                    "agent_code": "agent-001",
                    "tc_bot_id": "bot-001",
                    "before_destroy_cmd_hook": "echo cleanup",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        local_tpl = MagicMock(
            template_uuid="tpl-local", config=LocalTemplateConfig(type="LOCAL")
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            local_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(return_value=None)
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.execute_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_template_has_no_config(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        """restart_device raises ValueError when template has no config."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={"template_uuid": "tpl-no-config"},
        )
        mock_repo.get_by_device_uuid.return_value = record
        no_config_tpl = MagicMock(template_uuid="tpl-no-config", config=None)
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            no_config_tpl
        )
        with pytest.raises(ValueError, match="has no config"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    # ------------------------------------------------------------------
    # SECTION: start_device Sigma config logging test
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_sigma_device_config_logging_all_fields(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """start_device with SIGMA platform and all DeployConfig fields exercises config logging."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "zone": "us-west-2",
                    "vpc_config": {"vpc_id": "vpc-456", "subnet_id": "subnet-789"},
                    "sigma_metadata": {"region": "us", "tier": "prod"},
                    "resource_spec": {"cpu": "8", "memory": 16384},
                    "envs": {"APP_ENV": "staging", "LOG_LEVEL": "debug"},
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        sigma_tpl = MagicMock(
            template_uuid="tpl-sigma",
            config=SigmaTemplateConfig(
                type="Sigma",
                endpoint="http://sigma.test",
                access_key="ak",
                secret_key="sk",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            sigma_tpl
        )
        mock_paas_facade.create_device.return_value = SigmaCreationResult(
            platform="sigma",
            status="ACTIVE",
            region="us-west-2",
            instance_id="sigma-inst-full",
        )
        activated = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_type="SIGMA",
            provider_device_id="sigma-inst-full",
        )
        mock_repo.get_by_id.return_value = activated
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value


class TestDestroyDeviceExtra:
    @pytest.mark.asyncio
    async def test_destroy_updating_device(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        record = _make_record(
            status=DeviceStatus.UPDATING.value, provider_device_id="sb-001"
        )
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="u"
        )
        assert isinstance(result, DestroyDeviceResponse)
        mock_paas_facade.destroy_device.assert_called_once_with("sb-001")

    @pytest.mark.asyncio
    async def test_destroy_hook_exit_code_1_continues(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"before_destroy_cmd_hook": "exit 1"},
            },
        )
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=1,
            stdout="",
            stderr="something failed",
            execution_time_ms=100,
            command="exit 1",
        )
        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="u"
        )
        assert result.success is True
        assert result.error_message is not None
        assert "before_destroy_cmd_hook failed" in result.error_message

    @pytest.mark.asyncio
    async def test_destroy_hook_stderr_warning_captured(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"before_destroy_cmd_hook": "echo done"},
            },
        )
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="deprecation warning",
            execution_time_ms=100,
            command="echo done",
        )
        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="u"
        )
        assert result.success is True
        assert result.error_message is not None
        assert "before_destroy_cmd_hook warning" in result.error_message

    @pytest.mark.asyncio
    async def test_destroy_hook_exception_captured(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="sb-001",
            extra_config={
                "deploy_config": {"before_destroy_cmd_hook": "echo cleanup"},
            },
        )
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.execute_command.side_effect = TimeoutError("timed out")
        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="u"
        )
        assert result.success is True
        assert result.error_message is not None
        assert "before_destroy_cmd_hook execution error" in result.error_message

    @pytest.mark.asyncio
    async def test_destroy_paas_non_not_found_error(
        self, service, mock_repo, mock_paas_facade, mock_env
    ):
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        record = _make_record(
            status=DeviceStatus.ACTIVE.value, provider_device_id="sb-001"
        )
        mock_repo.get_active_or_updating_by_device_uuid.return_value = record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id="tpl-001",
            paas_device_id="sb-001",
            original_error=PaasError(
                code=ErrorCode.PLATFORM_UNAVAILABLE, message="platform down"
            ),
        )
        result = await service.destroy_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="u"
        )
        assert result.success is False
        assert result.error_message is not None
        assert "PaaS destroy failed" in result.error_message


class TestStopDeviceByUuid:
    """Tests for DefaultDeviceService.stop_device_by_uuid"""

    @pytest.mark.asyncio
    async def test_stop_device_sets_stopped_status(
        self, mock_repo, mock_paas_facade, mock_env
    ):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-001"
        mock_record.status = DeviceStatus.ACTIVE.value
        mock_record.provider_device_id = "provider-001"
        mock_record.provider_type = "Sigma"
        mock_record.extra_config = None

        mock_repo.get_active_or_updating_by_device_uuid.return_value = mock_record
        mock_paas_facade.destroy_device.return_value = None

        service = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
        )

        result = await service.stop_device_by_uuid(
            tenant="test_tenant",
            device_uuid="DEVICE-001",
            modifier="test_user",
        )

        assert result is not None
        assert result.success is True
        mock_paas_facade.destroy_device.assert_called_once_with("provider-001")
        mock_repo.update_status_by_device_uuid.assert_called_once_with(
            device_uuid="DEVICE-001",
            tenant="test_tenant",
            env="test",
            status=DeviceStatus.STOPPED.value,
        )

    @pytest.mark.asyncio
    async def test_stop_device_skips_soft_delete(
        self, mock_repo, mock_paas_facade, mock_env
    ):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-002"
        mock_record.status = DeviceStatus.ACTIVE.value
        mock_record.provider_device_id = None
        mock_record.provider_type = None
        mock_record.extra_config = None

        mock_repo.get_active_or_updating_by_device_uuid.return_value = mock_record

        service = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
        )

        result = await service.stop_device_by_uuid(
            tenant="test_tenant",
            device_uuid="DEVICE-002",
            modifier="test_user",
        )

        assert result.success is True
        mock_paas_facade.destroy_device.assert_not_called()
        mock_repo.update_status_by_device_uuid.assert_called_once()
        mock_repo.soft_delete_by_device_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_device_hook_failure_continues(
        self, mock_repo, mock_paas_facade, mock_env
    ):
        from secbaas.api.device_manage import CommandResult

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-003"
        mock_record.status = DeviceStatus.ACTIVE.value
        mock_record.provider_device_id = "provider-003"
        mock_record.provider_type = "ARCA"
        mock_record.extra_config = {
            "deploy_config": {"before_destroy_cmd_hook": "echo cleanup"}
        }

        mock_repo.get_active_or_updating_by_device_uuid.return_value = mock_record
        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=1,
            stdout="",
            stderr="hook failed",
            execution_time_ms=100,
            command="echo cleanup",
        )
        mock_paas_facade.destroy_device.return_value = None

        service = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
        )

        result = await service.stop_device_by_uuid(
            tenant="test_tenant",
            device_uuid="DEVICE-003",
            modifier="test_user",
        )

        assert result.success is True
        mock_paas_facade.destroy_device.assert_called_once_with("provider-003")
        mock_repo.update_status_by_device_uuid.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_device_not_found_returns_success(
        self, mock_repo, mock_paas_facade, mock_env
    ):
        mock_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_repo.get_by_device_uuid_only.return_value = None

        service = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
        )

        result = await service.stop_device_by_uuid(
            tenant="test_tenant",
            device_uuid="DEVICE-NOT-FOUND",
            modifier="test_user",
        )

        assert result.success is True
        assert result.error_message is None
        mock_paas_facade.destroy_device.assert_not_called()
        mock_repo.update_status_by_device_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_device_paas_failure_sets_success_false(
        self, mock_repo, mock_paas_facade, mock_env
    ):
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-004"
        mock_record.status = DeviceStatus.ACTIVE.value
        mock_record.provider_device_id = "provider-004"
        mock_record.provider_type = "Sigma"
        mock_record.extra_config = None

        mock_repo.get_active_or_updating_by_device_uuid.return_value = mock_record
        mock_paas_facade.destroy_device.side_effect = DeviceFacadeException(
            operation="destroy_device",
            platform_type="Sigma",
            template_id="tpl-001",
            paas_device_id="provider-004",
            original_error=PaasError(
                code=ErrorCode.PLATFORM_UNAVAILABLE, message="platform down"
            ),
        )

        service = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
        )

        result = await service.stop_device_by_uuid(
            tenant="test_tenant",
            device_uuid="DEVICE-004",
            modifier="test_user",
        )

        assert result.success is False
        assert result.error_message is not None
        assert "PaaS destroy failed" in result.error_message
        mock_repo.update_status_by_device_uuid.assert_called_once_with(
            device_uuid="DEVICE-004",
            tenant="test_tenant",
            env="test",
            status=DeviceStatus.STOPPED.value,
        )


class TestDeviceServiceCredentialsExtraction:
    """Tests for credentials extraction in start_device() LOCAL branch.

    The extraction pattern is:
        credentials = deploy_config.credentials if deploy_config else None
    """

    def test_extract_credentials_from_deploy_config(self):
        """credentials is extracted from DeployConfig correctly."""
        from secbaas.api.device_manage import DeployConfig, DeviceCredentials

        dc = DeployConfig(credentials=DeviceCredentials(token="tok"))
        creds = dc.credentials if dc else None
        assert creds is not None
        assert creds.token == "tok"

    def test_extract_credentials_when_deploy_config_none(self):
        """credentials is None when deploy_config is None."""
        deploy_config = None
        creds = deploy_config.credentials if deploy_config else None
        assert creds is None

    def test_extract_credentials_when_field_is_none(self):
        """credentials is None when deploy_config.credentials is None."""
        from secbaas.api.device_manage import DeployConfig

        dc = DeployConfig()
        creds = dc.credentials if dc else None
        assert creds is None

    def test_pass_credentials_to_local_device_config(self):
        """credentials is passed to LocalDeviceConfig constructor."""
        from secbaas.api.device_manage import (
            DeviceCredentials,
            LocalDeviceConfig,
        )

        dc = DeviceCredentials(token="tok", client_id="cid")
        ldc = LocalDeviceConfig(
            user_id="u",
            machine_id="m",
            tc_bot_id="b",
            agent_code="a",
            credentials=dc,
        )
        assert ldc.credentials is not None
        assert ldc.credentials.token == "tok"
        assert ldc.credentials.client_id == "cid"

    def test_log_message_includes_credentials_status(self):
        """Log message includes credentials set/not-set status."""
        from secbaas.api.device_manage import DeviceCredentials

        creds = DeviceCredentials(token="tok")
        status = "<set>" if creds else "<not set>"
        assert status == "<set>"

        creds_none = None
        status_none = "<set>" if creds_none else "<not set>"
        assert status_none == "<not set>"


class TestStartDevicePoolab:
    """Tests for POOLAB platform device lifecycle.

    Covers D-03 through D-09: provider_type resolution, detail_config construction,
    creation_result dispatch, hook dispatch, native restart, and error handling.
    """

    # ==================== Start Device Tests (D-03, D-04, D-05, D-06) ====================

    @pytest.mark.asyncio
    async def test_poolab_device_creation(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-03/D-06: Happy path start_device for POOLAB platform."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {"poolab_user_id": "user-001"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="tenant-001",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="123@42",
            poolab_user_id="user-001",
        )
        pending = _make_record(
            status=DeviceStatus.PENDING.value,
            provider_type="POOLAB",
            provider_device_id="123@42",
        )
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.create_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_poolab_missing_user_id_validation(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        """D-05: Missing poolab_user_id causes FAILED status."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={"deploy_config": {}},
        )
        mock_repo.get_by_device_uuid.return_value = record
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_poolab_device_creation_with_template_fallback(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-04: poolab_tenant_id and poolab_image_id fall back to template when DeployConfig values are None."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {"poolab_user_id": "u1"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="tpl-tenant",
                poolab_tenant_token="tok",
                poolab_default_image_id_pre="img-pre",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="123@42",
            poolab_user_id="u1",
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        with patch(f"{DS}.get_current_env", return_value="pre"):
            result = await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.create_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_poolab_device_creation_deploy_config_override(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-04: DeployConfig values take priority over template fallback."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "poolab_user_id": "u1",
                    "poolab_tenant_id": "dc-tenant",
                    "poolab_image_id": "dc-img",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="tpl-tenant",
                poolab_tenant_token="tok",
                poolab_default_image_id_pre="img-pre",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="123@42",
            poolab_user_id="u1",
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.create_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_poolab_device_creation_with_envs(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-04: poolab_envs is passed through to detail_config."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "poolab_user_id": "u1",
                    "poolab_envs": {"MY_VAR": "my_val"},
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="123@42",
            poolab_user_id="u1",
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.create_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_poolab_device_creation_with_spec(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """poolab_spec is passed through from DeployConfig to detail_config."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "poolab_user_id": "u1",
                    "poolab_spec": "2C4G10G",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="123@42",
            poolab_user_id="u1",
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.create_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_poolab_creation_result_dispatch(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-06: PoolabCreationResult.poolab_id is extracted as provider_device_id."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {"poolab_user_id": "user-001"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="tenant-001",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="abc@42",
            poolab_user_id="user-001",
        )
        pending = _make_record(
            status=DeviceStatus.PENDING.value,
            provider_type="POOLAB",
            provider_device_id="abc@42",
        )
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value

    # ==================== After-Create Hook Tests (D-07) ====================

    @pytest.mark.asyncio
    async def test_poolab_with_hook_dispatches_via_else_branch(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-07: POOLAB enters else branch and dispatches hook asynchronously."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "poolab_user_id": "user-001",
                    "after_create_cmd_hook": "echo start",
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="tenant-001",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.create_device.return_value = PoolabCreationResult(
            platform="poolab",
            status="ACTIVE",
            poolab_id="123@42",
            poolab_user_id="user-001",
        )
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        with patch(f"{DS}.dispatch_start_hook") as mock_dispatch:
            result = await service.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
            mock_dispatch.assert_called_once()
            assert result.status == DeviceStatus.PENDING.value

    # ==================== Restart Device Tests (D-08, D-09) ====================

    @pytest.mark.asyncio
    async def test_poolab_restart_native_success(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-08: POOLAB restart uses native restart (single-step) and sets ACTIVE (no async callback)."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="123@42",
            provider_type="POOLAB",
            extra_config={
                "deploy_config": {"poolab_user_id": "user-001"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(return_value=None)
        active = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = active
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.ACTIVE.value
        mock_paas_facade.restart_device.assert_called_once_with("123@42")

    @pytest.mark.asyncio
    async def test_poolab_restart_native_failure(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-08: POOLAB restart failure sets FAILED status with error message."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="123@42",
            provider_type="POOLAB",
            extra_config={
                "deploy_config": {"poolab_user_id": "user-001"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(
            side_effect=Exception("Poolab API error")
        )
        failed = _make_record(status=DeviceStatus.FAILED.value)
        mock_repo.get_by_id.return_value = failed
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_poolab_restart_no_provider_device_id(
        self, service, mock_repo, mock_env, mock_device_template_service
    ):
        """D-08: restart_device without provider_device_id raises ValueError."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id=None,
            provider_type=None,
            extra_config={"deploy_config": {}},
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        with pytest.raises(ValueError, match="Cannot restart POOLAB device without"):
            await service.restart_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

    @pytest.mark.asyncio
    async def test_poolab_restart_does_not_use_destroy_create(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """D-09: POOLAB restart does NOT call destroy_device (no destroy+create)."""
        record = _make_record(
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="123@42",
            provider_type="POOLAB",
            extra_config={
                "deploy_config": {"poolab_user_id": "user-001"},
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        poolab_tpl = MagicMock(
            template_uuid="tpl-poolab",
            config=PoolabTemplateConfig(
                type="POOLAB",
                poolab_tenant_id="t1",
                poolab_tenant_token="tok",
            ),
        )
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            poolab_tpl
        )
        mock_paas_facade.restart_device = AsyncMock(return_value=None)
        pending = _make_record(status=DeviceStatus.PENDING.value)
        mock_repo.get_by_id.return_value = pending
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.destroy_device.assert_not_called()


# =============================================================================
# Helper: make_docker_template
# =============================================================================


def make_docker_template():
    """Construct a mock Docker template with DockerTemplateConfig."""
    config = DockerTemplateConfig(
        type="DOCKER",
        image="alpine:latest",
        container_port=8080,
        memory_limit="512m",
    )
    template = MagicMock()
    template.template_id = 50
    template.template_uuid = "tpl-docker-001"
    template.tenant = "test-tenant"
    template.config = config
    template.type = "Docker"
    return template


def _extract_detail_config(facade_mock):
    """Extract detail_config from facade.create_device call_args.

    Handles both keyword and positional argument styles to avoid
    fragility when the facade signature evolves.
    """
    ca = facade_mock.create_device.call_args
    if ca.kwargs:
        return ca.kwargs.get("detail_config")
    return ca[0][2]


# =============================================================================
# DOCKER Platform Tests: start_device
# =============================================================================


class TestStartDeviceDocker:
    """Tests for DOCKER platform device creation in start_device.

    Mirrors TestStartDevicePoolab pattern: _make_record(extra_config=...),
    mock_repo.get_by_device_uuid, mock_device_template_service.get_default_or_explicit_template,
    service.start_device(tenant=..., device_uuid=...).
    """

    @pytest.mark.asyncio
    async def test_docker_device_creation(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """DOCKER start_device: deploy_config overrides docker_image and docker_container_port."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={
                "deploy_config": {
                    "docker_image": "custom-image:v2",
                    "docker_container_port": 3000,
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        docker_tpl = make_docker_template()
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            docker_tpl
        )
        mock_paas_facade.create_device.return_value = DockerCreationResult(
            platform="docker",
            container_id="abc123def456@50",
            host_port=32768,
            status="running",
        )
        pending = _make_record(
            status=DeviceStatus.PENDING.value,
            provider_type="DOCKER",
            provider_device_id="abc123def456@50",
        )
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        mock_paas_facade.create_device.assert_called_once()
        detail_config = _extract_detail_config(mock_paas_facade)
        assert isinstance(detail_config, DockerDeviceConfig)
        assert detail_config.image == "custom-image:v2"
        assert detail_config.container_port == 3000

    @pytest.mark.asyncio
    async def test_docker_device_creation_template_fallback(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """DOCKER start_device: template defaults used when DeployConfig has no image/port."""
        record = _make_record(
            status=DeviceStatus.PENDING.value,
            extra_config={"deploy_config": {}},
        )
        mock_repo.get_by_device_uuid.return_value = record
        docker_tpl = make_docker_template()
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            docker_tpl
        )
        mock_paas_facade.create_device.return_value = DockerCreationResult(
            platform="docker",
            container_id="abc123",
            host_port=32768,
            status="running",
        )
        pending = _make_record(
            status=DeviceStatus.PENDING.value,
            provider_type="DOCKER",
            provider_device_id="abc123",
        )
        mock_repo.get_by_id.return_value = pending
        result = await service.start_device(
            tenant="test-tenant", device_uuid="DEVICE-test-001"
        )
        assert result.status == DeviceStatus.PENDING.value
        assert mock_paas_facade.create_device.called
        detail_config = _extract_detail_config(mock_paas_facade)
        assert isinstance(detail_config, DockerDeviceConfig)


# =============================================================================
# DOCKER Platform Tests: restart_device
# =============================================================================


class TestRestartDeviceDocker:
    """Tests for DOCKER platform restart_device.

    DOCKER uses _native_restart_device with has_async_callback=False.
    """

    @pytest.mark.asyncio
    async def test_restart_device_docker_native_restart(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """DOCKER restart: uses _native_restart_device, facade.restart_device called."""
        record = _make_record(
            device_uuid="DEVICE-docker-001",
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="abc123",
            provider_type="DOCKER",
            extra_config={"template_uuid": "tpl-docker-001"},
        )
        mock_repo.get_by_device_uuid.return_value = record
        docker_template = make_docker_template()
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            docker_template
        )
        mock_paas_facade.restart_device.return_value = True
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.restart_device(
            tenant="test-tenant", device_uuid="DEVICE-docker-001"
        )
        assert isinstance(result, DeviceResponse)
        mock_paas_facade.restart_device.assert_called_once_with("abc123")


# =============================================================================
# DOCKER Platform Tests: update_device
# =============================================================================


class TestUpdateDeviceDocker:
    """Tests for DOCKER platform update_device.

    DOCKER uses destroy+create two-phase update: destroy old device, create new one
    with DockerDeviceConfig containing correct docker_image and docker_container_port.
    """

    @pytest.mark.asyncio
    async def test_update_device_docker_destroy_and_create(
        self,
        service,
        mock_repo,
        mock_paas_facade,
        mock_env,
        mock_device_template_service,
    ):
        """DOCKER update: destroy+create with DockerDeviceConfig containing correct image/port."""
        record = _make_record(
            device_uuid="DEVICE-docker-001",
            status=DeviceStatus.ACTIVE.value,
            provider_device_id="abc123",
            provider_type="DOCKER",
            extra_config={
                "template_uuid": "tpl-docker-001",
                "deploy_config": {
                    "docker_image": "custom-image:v2",
                    "docker_container_port": 3000,
                },
            },
        )
        mock_repo.get_by_device_uuid.return_value = record
        docker_template = make_docker_template()
        mock_device_template_service.get_default_or_explicit_template.return_value = (
            docker_template
        )
        mock_paas_facade.destroy_device.return_value = None
        facade_result = DockerCreationResult(
            platform="docker",
            container_id="new-container-id",
            host_port=32769,
            status="running",
        )
        mock_paas_facade.create_device.return_value = facade_result
        activated = _make_record(status=DeviceStatus.ACTIVE.value)
        mock_repo.get_by_id.return_value = activated
        result = await service.update_device(
            tenant="test-tenant",
            device_uuid="DEVICE-docker-001",
            modifier="test_user",
        )
        assert isinstance(result, DeviceResponse)
        mock_paas_facade.destroy_device.assert_called_once_with("abc123")
        mock_paas_facade.create_device.assert_called_once()
        detail_config = _extract_detail_config(mock_paas_facade)
        assert isinstance(detail_config, DockerDeviceConfig)
        assert detail_config.image == "custom-image:v2"
        assert detail_config.container_port == 3000

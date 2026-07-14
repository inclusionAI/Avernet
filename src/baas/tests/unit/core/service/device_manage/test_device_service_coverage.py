"""Coverage tests for DefaultDeviceService and module-level helpers."""

import re
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    CommandResult,
    DestroyDeviceResponse,
    DeviceConfig,
    DeviceCreate,
    DeviceResponse,
    DeviceStatus,
    DockerCreationResult,
    EncryptableHeaderRule,
    EncryptableOutBoundRule,
    K8sCreationResult,
    LocalCreationResult,
    PoolabCreationResult,
    TeClawCreationResult,
)
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DockerTemplateConfig,
    K8sTemplateConfig,
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
    TeClawTemplateConfig,
)
from secbaas.community.core.repository.device import DeviceRecord
from secbaas.community.core.service.device_manage._device_service import (
    DefaultDeviceService,
    _build_arca_detail_config,
    _decrypt_header_rule_values,
    _encrypt_header_rule_values,
    _native_restart_device,
    _native_update_device,
    _safe_format_hook,
    _validate_required_field,
    device_record_to_response,
)
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    id=1,
    device_uuid="DEVICE-abc123",
    tenant="t1",
    env="dev",
    status="ACTIVE",
    provider_type="ARCA",
    provider_device_id="dev@42",
    extra_config=None,
    err_msg=None,
):
    return DeviceRecord(
        id=id,
        gmt_create=datetime.now(UTC),
        gmt_modified=datetime.now(UTC),
        device_uuid=device_uuid,
        tenant=tenant,
        env=env,
        domain="default",
        is_deleted=0,
        creator="system",
        modifier="system",
        status=status,
        provider_type=provider_type,
        provider_device_id=provider_device_id,
        provider_device_props={},
        extra_config=extra_config or {},
        err_msg=err_msg,
    )


def _make_template(platform_type="ARCA"):
    type_map = {
        "ARCA": (
            ArcaTemplateConfig,
            dict(
                type="ARCA",
                base_url="https://arca.test",
                api_key="key",
                template_id="tpl-123",
                oss_mount_id="mount-1",
                arca_template_id_pre=None,
                arca_template_id_prod=None,
            ),
        ),
        "SIGMA": (
            SigmaTemplateConfig,
            dict(
                type="Sigma",
                endpoint="https://sigma.test",
                access_key="ak",
                secret_key="sk",
            ),
        ),
        "LOCAL": (LocalTemplateConfig, dict(type="LOCAL")),
        "POOLAB": (
            PoolabTemplateConfig,
            dict(
                type="POOLAB",
                poolab_tenant_id="ptid",
                poolab_tenant_token="ptoken",
            ),
        ),
        "TECLAW": (
            TeClawTemplateConfig,
            dict(
                type="TECLAW",
                teclaw_endpoint="https://teclaw.test",
            ),
        ),
        "K8S": (
            K8sTemplateConfig,
            dict(
                type="K8s",
                kubeconfig="kc",
                namespace="ns",
                image="img",
            ),
        ),
        "DOCKER": (
            DockerTemplateConfig,
            dict(
                type="DOCKER",
                image="img",
                container_port=8080,
                memory_limit="512m",
            ),
        ),
    }
    config_cls, kwargs = type_map.get(platform_type, type_map["ARCA"])
    config = config_cls(**kwargs)
    t = MagicMock()
    t.id = 1
    t.template_id = 42
    t.template_uuid = "tpl-uuid"
    t.type = platform_type
    t.tenant = "t1"
    t.name = "Test"
    t.config = config
    return t


def _make_service(platform_type="ARCA"):
    mock_facade = MagicMock()
    mock_facade.create_device = AsyncMock()
    mock_facade.destroy_device = AsyncMock()
    mock_facade.restart_device = AsyncMock()
    mock_facade.update_device = AsyncMock()
    mock_facade.execute_command = AsyncMock()
    mock_repo = MagicMock()
    mock_template_svc = MagicMock()
    mock_secret = MagicMock()
    mock_secret.resolve_common_sm4_key.return_value = "MDEyMzQ1Njc4OWFiY2RlZg=="

    svc = DefaultDeviceService(
        paas_facade=mock_facade,
        repository=mock_repo,
        device_template_service=mock_template_svc,
        secret_plugin=mock_secret,
    )

    template = _make_template(platform_type)
    mock_template_svc.get_default_or_explicit_template.return_value = template
    mock_template_svc.get_by_template_id.return_value = template

    return svc, mock_facade, mock_repo, mock_template_svc, mock_secret


# ---------------------------------------------------------------------------
# _safe_format_hook
# ---------------------------------------------------------------------------


class TestSafeFormatHook:
    def test_no_placeholders(self):
        assert _safe_format_hook("echo hello") == "echo hello"

    def test_known_placeholder(self):
        result = _safe_format_hook("echo {client_id}", client_id="abc")
        assert result == "echo abc"

    def test_unknown_placeholder_preserved(self):
        result = _safe_format_hook("echo {unknown_key}", client_id="abc")
        assert result == "echo {unknown_key}"

    def test_none_value_placeholder_preserved(self):
        result = _safe_format_hook("echo {client_id}", client_id=None)
        assert result == "echo {client_id}"

    def test_multiple_placeholders_mixed(self):
        result = _safe_format_hook(
            "echo {client_id} {token} {unknown}",
            client_id="abc",
            token="xyz",
        )
        assert result == "echo abc xyz {unknown}"

    def test_integer_value(self):
        result = _safe_format_hook("port={port}", port=8080)
        assert result == "port=8080"

    def test_empty_script(self):
        assert _safe_format_hook("") == ""


# ---------------------------------------------------------------------------
# _validate_required_field
# ---------------------------------------------------------------------------


class TestValidateRequiredField:
    def test_valid_value(self):
        missing = []
        _validate_required_field("value", "field", missing)
        assert missing == []

    def test_none_value(self):
        missing = []
        _validate_required_field(None, "field", missing)
        assert "field" in missing

    def test_empty_string(self):
        missing = []
        _validate_required_field("", "field", missing)
        assert "field" in missing

    def test_whitespace_only(self):
        missing = []
        _validate_required_field("   ", "field", missing)
        assert "field" in missing

    def test_multiple_fields(self):
        missing = []
        _validate_required_field(None, "field1", missing)
        _validate_required_field("", "field2", missing)
        _validate_required_field("ok", "field3", missing)
        assert "field1" in missing
        assert "field2" in missing
        assert "field3" not in missing


# ---------------------------------------------------------------------------
# device_record_to_response
# ---------------------------------------------------------------------------


class TestDeviceRecordToResponse:
    def test_valid_record(self):
        record = _make_record()
        result = device_record_to_response(record)
        assert isinstance(result, DeviceResponse)
        assert result.device_uuid == "DEVICE-abc123"
        assert result.status == "ACTIVE"

    def test_none_record_raises(self):
        with pytest.raises(RuntimeError, match="Device record is None"):
            device_record_to_response(None)

    def test_record_with_extra_config(self):
        record = _make_record(extra_config={"template_uuid": "tpl-123"})
        result = device_record_to_response(record)
        assert result.extra_config.template_uuid == "tpl-123"

    def test_record_with_empty_extra_config(self):
        record = _make_record(extra_config={})
        result = device_record_to_response(record)
        assert result.extra_config is not None

    def test_record_with_err_msg(self):
        record = _make_record(err_msg="some error")
        result = device_record_to_response(record)
        assert result.err_msg == "some error"

    def test_record_with_none_err_msg(self):
        record = _make_record(err_msg=None)
        result = device_record_to_response(record)
        assert result.err_msg is None


# ---------------------------------------------------------------------------
# _encrypt_header_rule_values
# ---------------------------------------------------------------------------


class TestEncryptHeaderRuleValues:
    def test_none_config(self):
        _encrypt_header_rule_values(None, MagicMock())

    def test_no_deploy_config(self):
        config = MagicMock()
        config.deploy_config = None
        _encrypt_header_rule_values(config, MagicMock())

    def test_no_outbound_rule(self):
        config = MagicMock()
        config.deploy_config.outbound_operation_rule = None
        _encrypt_header_rule_values(config, MagicMock())

    def test_non_encryptable_rule(self):
        config = MagicMock()
        config.deploy_config.outbound_operation_rule = MagicMock()
        _encrypt_header_rule_values(config, MagicMock())

    def test_encryptable_rule_with_encrypt_value(self):

        rule = EncryptableHeaderRule(
            domains=["*"],
            action="SET",
            header_name="X-Token",
            value="secret-value",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        config = MagicMock()
        config.deploy_config.outbound_operation_rule = outbound

        secret = MagicMock()
        secret.resolve_common_sm4_key.return_value = "MDEyMzQ1Njc4OWFiY2RlZg=="
        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_encrypt",
            return_value="ENCRYPTED",
        ):
            _encrypt_header_rule_values(config, secret)
        assert rule.value == "ENCRYPTED"
        assert rule.encrypt_value is True

    def test_encryptable_rule_no_encrypt_value(self):

        rule = EncryptableHeaderRule(
            domains=["*"],
            action="SET",
            header_name="X-Token",
            value="plain-value",
            encrypt_value=False,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        config = MagicMock()
        config.deploy_config.outbound_operation_rule = outbound

        _encrypt_header_rule_values(config, MagicMock())
        assert rule.value == "plain-value"

    def test_encryptable_rule_empty_value(self):

        rule = EncryptableHeaderRule(
            domains=["*"],
            action="SET",
            header_name="X-Token",
            value="",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        config = MagicMock()
        config.deploy_config.outbound_operation_rule = outbound

        _encrypt_header_rule_values(config, MagicMock())
        assert rule.value == ""


# ---------------------------------------------------------------------------
# _decrypt_header_rule_values
# ---------------------------------------------------------------------------


class TestDecryptHeaderRuleValues:
    def test_none_rule(self):
        secret = MagicMock()
        assert _decrypt_header_rule_values(None, secret) is None

    def test_non_encryptable_rule_returns_as_is(self):
        from secbaas.community.api.device_manage import OutBoundOperationRule

        rule = OutBoundOperationRule(header_operation_rules=[])
        secret = MagicMock()
        result = _decrypt_header_rule_values(rule, secret)
        assert result is rule

    def test_non_encryptable_non_sdk_rule_returns_none(self):
        secret = MagicMock()
        rule = MagicMock()  # Not EncryptableOutBoundRule, not OutBoundOperationRule
        result = _decrypt_header_rule_values(rule, secret)
        assert result is None

    def test_decrypt_encryptable_rule(self):

        rule = EncryptableHeaderRule(
            domains=["*"],
            action="SET",
            header_name="X-Token",
            value="encrypted-blob",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        secret = MagicMock()
        secret.resolve_common_sm4_key.return_value = "MDEyMzQ1Njc4OWFiY2RlZg=="
        with patch(
            "secbaas.community.core.service.device_manage._device_service.common_sm4_decrypt",
            return_value="secret",
        ):
            result = _decrypt_header_rule_values(outbound, secret)
        assert result is not None
        assert result.header_operation_rules[0].value == "secret"

    def test_decrypt_rule_no_encrypt_value(self):

        rule = EncryptableHeaderRule(
            domains=["*"],
            action="SET",
            header_name="X-Token",
            value="plain",
            encrypt_value=False,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        secret = MagicMock()
        result = _decrypt_header_rule_values(outbound, secret)
        assert result.header_operation_rules[0].value == "plain"

    def test_decrypt_failure_raises(self):

        rule = EncryptableHeaderRule(
            domains=["*"],
            action="SET",
            header_name="X-Token",
            value="invalid-encrypted",
            encrypt_value=True,
        )
        outbound = EncryptableOutBoundRule(header_operation_rules=[rule])
        secret = MagicMock()
        secret.resolve_common_sm4_key.return_value = "a2V5MTIzNDU2Nzg5MDEyMzQ1Ng=="
        with pytest.raises(ValueError, match="Decryption failed"):
            _decrypt_header_rule_values(outbound, secret)


# ---------------------------------------------------------------------------
# _build_arca_detail_config
# ---------------------------------------------------------------------------


class TestBuildArcaDetailConfig:
    def test_none_deploy_config(self):
        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            None,
            None,
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert config is not None
        assert mounts is None

    def test_with_mount_points(self):
        from secbaas.community.api.device_manage import MountPoint

        deploy = MagicMock()
        mp = MountPoint(
            id="", remote_dir="/remote", local_dir="/local", permission="rw"
        )
        deploy.mount_points = [mp]
        deploy.envs = None
        deploy.ttl_in_minutes = None
        deploy.arca_metadata = None
        deploy.outbound_operation_rule = None
        deploy.storage = None
        deploy.resource_spec = None
        deploy.docker_image = None

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            "oss-mount-1",
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert len(mounts) == 1
        assert mounts[0].id == "oss-mount-1"

    def test_with_envs(self):
        deploy = MagicMock()
        deploy.mount_points = None
        deploy.envs = {"KEY": "val"}
        deploy.ttl_in_minutes = None
        deploy.arca_metadata = None
        deploy.outbound_operation_rule = None
        deploy.storage = None
        deploy.resource_spec = None
        deploy.docker_image = None

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            None,
            "pre",
            "DEVICE-abc",
            secret,
        )
        assert config.envs is not None
        assert config.envs["AGENTCLAW_ENV"] == "pre"
        assert config.envs["KEY"] == "val"

    def test_with_ttl_and_metadata(self):
        deploy = MagicMock()
        deploy.mount_points = None
        deploy.envs = None
        deploy.ttl_in_minutes = 1440
        deploy.arca_metadata = {"k": "v"}
        deploy.outbound_operation_rule = None
        deploy.storage = None
        deploy.resource_spec = None
        deploy.docker_image = None

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            None,
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert config.ttl_in_minutes == 1440
        assert config.metadata == {"k": "v"}

    def test_with_storage_placeholder(self):
        from secbaas.community.api.device_manage import Storage

        deploy = MagicMock()
        deploy.mount_points = None
        deploy.envs = None
        deploy.ttl_in_minutes = None
        deploy.arca_metadata = None
        deploy.outbound_operation_rule = None
        deploy.storage = Storage(
            type="oss",
            path="/data",
            storage_id="vol-{device_uuid}",
            quota="10g",
            permission="rw",
        )
        deploy.resource_spec = None
        deploy.docker_image = None

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            None,
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert config.storage is not None
        assert config.storage.storage_id == "vol-DEVICE-abc"

    def test_with_storage_no_placeholder(self):
        from secbaas.community.api.device_manage import Storage

        deploy = MagicMock()
        deploy.mount_points = None
        deploy.envs = None
        deploy.ttl_in_minutes = None
        deploy.arca_metadata = None
        deploy.outbound_operation_rule = None
        deploy.storage = Storage(
            type="oss",
            path="/data",
            storage_id="fixed-id",
            quota="10g",
            permission="rw",
        )
        deploy.resource_spec = None
        deploy.docker_image = None

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            None,
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert config.storage.storage_id == "fixed-id"

    def test_with_resource_spec_and_docker_image(self):
        deploy = MagicMock()
        deploy.mount_points = None
        deploy.envs = None
        deploy.ttl_in_minutes = None
        deploy.arca_metadata = None
        deploy.outbound_operation_rule = None
        deploy.storage = None
        deploy.resource_spec = {"cpu": "1"}
        deploy.docker_image = "custom:latest"

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            None,
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert config.resource_spec == {"cpu": "1"}
        assert config.docker_image == "custom:latest"

    def test_with_none_storage_id(self):
        from secbaas.community.api.device_manage import Storage

        deploy = MagicMock()
        deploy.mount_points = None
        deploy.envs = None
        deploy.ttl_in_minutes = None
        deploy.arca_metadata = None
        deploy.outbound_operation_rule = None
        deploy.storage = Storage(
            type="oss",
            path="/data",
            storage_id=None,
            quota="10g",
            permission="rw",
        )
        deploy.resource_spec = None
        deploy.docker_image = None

        secret = MagicMock()
        config, mounts = _build_arca_detail_config(
            deploy,
            None,
            "dev",
            "DEVICE-abc",
            secret,
        )
        assert config.storage.storage_id is None


# ---------------------------------------------------------------------------
# _native_restart_device
# ---------------------------------------------------------------------------


class TestNativeRestartDevice:
    @pytest.mark.asyncio
    async def test_no_provider_device_id_raises(self):
        facade = MagicMock()
        repo = MagicMock()
        record = _make_record(provider_device_id=None)
        with pytest.raises(ValueError, match="Cannot restart"):
            await _native_restart_device(
                facade,
                repo,
                record,
                "LOCAL",
                "dev-1",
                "t1",
                "dev",
                "user",
            )

    @pytest.mark.asyncio
    async def test_restart_success_active(self):
        facade = MagicMock()
        facade.restart_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated

        result = await _native_restart_device(
            facade,
            repo,
            record,
            "LOCAL",
            "dev-1",
            "t1",
            "dev",
            "user",
            has_async_callback=False,
        )
        assert result.status == "ACTIVE"
        repo.update_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_success_pending_with_callback(self):
        facade = MagicMock()
        facade.restart_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="PENDING")
        repo.get_by_id.return_value = updated

        result = await _native_restart_device(
            facade,
            repo,
            record,
            "LOCAL",
            "dev-1",
            "t1",
            "dev",
            "user",
            has_async_callback=True,
        )
        assert result.status == "PENDING"

    @pytest.mark.asyncio
    async def test_restart_failure_sets_failed(self):
        facade = MagicMock()
        facade.restart_device = AsyncMock(side_effect=RuntimeError("boom"))
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await _native_restart_device(
            facade,
            repo,
            record,
            "LOCAL",
            "dev-1",
            "t1",
            "dev",
            "user",
        )
        assert result.status == "FAILED"
        assert repo.update_device.call_args[1]["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_restart_failure_no_record_after_update(self):
        facade = MagicMock()
        facade.restart_device = AsyncMock(side_effect=RuntimeError("boom"))
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        repo.get_by_id.return_value = None

        with pytest.raises(
            ValueError, match="Device record not found after failed restart"
        ):
            await _native_restart_device(
                facade,
                repo,
                record,
                "LOCAL",
                "dev-1",
                "t1",
                "dev",
                "user",
            )

    @pytest.mark.asyncio
    async def test_restart_success_no_record_after_update(self):
        facade = MagicMock()
        facade.restart_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Device record not found after restart"):
            await _native_restart_device(
                facade,
                repo,
                record,
                "LOCAL",
                "dev-1",
                "t1",
                "dev",
                "user",
            )


# ---------------------------------------------------------------------------
# _native_update_device
# ---------------------------------------------------------------------------


class TestNativeUpdateDevice:
    @pytest.mark.asyncio
    async def test_no_provider_device_id_raises(self):
        facade = MagicMock()
        repo = MagicMock()
        record = _make_record(provider_device_id=None)
        with pytest.raises(ValueError, match="Cannot update"):
            await _native_update_device(
                facade,
                repo,
                record,
                "LOCAL",
                "dev-1",
                "t1",
                "dev",
                "user",
            )

    @pytest.mark.asyncio
    async def test_update_success_active(self):
        facade = MagicMock()
        facade.update_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated

        result = await _native_update_device(
            facade,
            repo,
            record,
            "POOLAB",
            "dev-1",
            "t1",
            "dev",
            "user",
            has_async_callback=False,
        )
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_update_success_pending_with_callback(self):
        facade = MagicMock()
        facade.update_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="PENDING")
        repo.get_by_id.return_value = updated

        result = await _native_update_device(
            facade,
            repo,
            record,
            "LOCAL",
            "dev-1",
            "t1",
            "dev",
            "user",
            has_async_callback=True,
        )
        assert result.status == "PENDING"

    @pytest.mark.asyncio
    async def test_update_failure_sets_failed(self):
        facade = MagicMock()
        facade.update_device = AsyncMock(side_effect=RuntimeError("boom"))
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await _native_update_device(
            facade,
            repo,
            record,
            "LOCAL",
            "dev-1",
            "t1",
            "dev",
            "user",
        )
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_update_failure_no_record_after_update(self):
        facade = MagicMock()
        facade.update_device = AsyncMock(side_effect=RuntimeError("boom"))
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        repo.get_by_id.return_value = None

        with pytest.raises(
            ValueError, match="Device record not found after failed update"
        ):
            await _native_update_device(
                facade,
                repo,
                record,
                "LOCAL",
                "dev-1",
                "t1",
                "dev",
                "user",
            )

    @pytest.mark.asyncio
    async def test_update_success_no_record_after_update(self):
        facade = MagicMock()
        facade.update_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Device record not found after update"):
            await _native_update_device(
                facade,
                repo,
                record,
                "LOCAL",
                "dev-1",
                "t1",
                "dev",
                "user",
            )

    @pytest.mark.asyncio
    async def test_update_with_config(self):
        facade = MagicMock()
        facade.update_device = AsyncMock()
        repo = MagicMock()
        record = _make_record(provider_device_id="dev@42")
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated

        await _native_update_device(
            facade,
            repo,
            record,
            "TECLAW",
            "dev-1",
            "t1",
            "dev",
            "user",
            config=MagicMock(),
        )
        facade.update_device.assert_awaited_once()


# ---------------------------------------------------------------------------
# DefaultDeviceService.create_device
# ---------------------------------------------------------------------------


class TestCreateDevice:
    def test_create_success(self):
        svc, facade, repo, tpl_svc, secret = _make_service()
        record = _make_record(status="PENDING")
        repo.insert_device.return_value = 1
        repo.get_by_id.return_value = record

        data = DeviceCreate(operator="user1")
        result = svc.create_device("t1", data)
        assert isinstance(result, DeviceResponse)
        assert result.status == "PENDING"
        repo.insert_device.assert_called_once()

    def test_create_readback_failure(self):
        svc, facade, repo, tpl_svc, secret = _make_service()
        repo.insert_device.return_value = 1
        repo.get_by_id.return_value = None

        data = DeviceCreate(operator="user1")
        with pytest.raises(ValueError, match="Device record created but not found"):
            svc.create_device("t1", data)

    def test_create_with_extra_config(self):
        svc, facade, repo, tpl_svc, secret = _make_service()
        record = _make_record(
            status="PENDING", extra_config={"template_uuid": "tpl-123"}
        )
        repo.insert_device.return_value = 1
        repo.get_by_id.return_value = record

        data = DeviceCreate(operator="user1")
        result = svc.create_device("t1", data)
        assert result.status == "PENDING"


# ---------------------------------------------------------------------------
# DefaultDeviceService.get_device_info
# ---------------------------------------------------------------------------


class TestGetDeviceInfo:
    def test_found(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record()
        repo.get_by_device_uuid_only.return_value = record
        result = svc.get_device_info("DEVICE-abc123")
        assert result is not None
        assert result.device_uuid == "DEVICE-abc123"

    def test_not_found_returns_none(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_by_device_uuid_only.return_value = None
        result = svc.get_device_info("DEVICE-notfound")
        assert result is None


# ---------------------------------------------------------------------------
# DefaultDeviceService._resolve_device_for_operation
# ---------------------------------------------------------------------------


class TestResolveDeviceForOperation:
    def test_device_not_found(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_by_device_uuid.return_value = None
        with pytest.raises(ValueError, match="Device not found"):
            svc._resolve_device_for_operation("t1", "dev-1", "restart")

    def test_device_released(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status=DeviceStatus.RELEASED.value)
        repo.get_by_device_uuid.return_value = record
        with pytest.raises(ValueError, match="RELEASED"):
            svc._resolve_device_for_operation("t1", "dev-1", "restart")

    def test_device_invalid_status(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="STOPPED")
        repo.get_by_device_uuid.return_value = record
        with pytest.raises(ValueError, match="Cannot restart"):
            svc._resolve_device_for_operation("t1", "dev-1", "restart")

    def test_template_not_found(self):
        svc, facade, repo, tpl_svc, _ = _make_service()
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        tpl_svc.get_default_or_explicit_template.return_value = None
        with pytest.raises(ValueError, match="Template not found"):
            svc._resolve_device_for_operation("t1", "dev-1", "restart")

    def test_template_no_config(self):
        svc, facade, repo, tpl_svc, _ = _make_service()
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        template = MagicMock()
        template.config = None
        template.template_uuid = "tpl-uuid"
        tpl_svc.get_default_or_explicit_template.return_value = template
        with pytest.raises(ValueError, match="no config"):
            svc._resolve_device_for_operation("t1", "dev-1", "restart")

    def test_unknown_template_config_type(self):
        svc, facade, repo, tpl_svc, _ = _make_service()
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        template = MagicMock()
        template.config = MagicMock()  # Not a recognized config type
        template.template_uuid = "tpl-uuid"
        tpl_svc.get_default_or_explicit_template.return_value = template
        with pytest.raises(ValueError, match="Unknown template config type"):
            svc._resolve_device_for_operation("t1", "dev-1", "restart")

    def test_success_arca(self):
        svc, facade, repo, tpl_svc, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "ARCA"

    def test_success_local(self):
        svc, facade, repo, tpl_svc, _ = _make_service("LOCAL")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "update")
        assert pt == "LOCAL"

    def test_success_sigma(self):
        svc, facade, repo, tpl_svc, _ = _make_service("SIGMA")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "SIGMA"

    def test_success_poolab(self):
        svc, facade, repo, tpl_svc, _ = _make_service("POOLAB")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "POOLAB"

    def test_success_teclaw(self):
        svc, facade, repo, tpl_svc, _ = _make_service("TECLAW")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "TECLAW"

    def test_success_k8s(self):
        svc, facade, repo, tpl_svc, _ = _make_service("K8S")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "K8S"

    def test_success_docker(self):
        svc, facade, repo, tpl_svc, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "DOCKER"

    def test_success_pending_status(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status=DeviceStatus.PENDING.value)
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "ARCA"

    def test_success_failed_status(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status=DeviceStatus.FAILED.value)
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "ARCA"

    def test_success_updating_status(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status=DeviceStatus.UPDATING.value)
        repo.get_by_device_uuid.return_value = record
        r, dc, t, pt = svc._resolve_device_for_operation("t1", "dev-1", "restart")
        assert pt == "ARCA"


# ---------------------------------------------------------------------------
# DefaultDeviceService.restart_device
# ---------------------------------------------------------------------------


class TestRestartDevice:
    @pytest.mark.asyncio
    async def test_sigma_raises(self):
        svc, facade, repo, _, _ = _make_service("SIGMA")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        with pytest.raises(
            ValueError, match="Sigma platform restart is not yet implemented"
        ):
            await svc.restart_device("t1", "dev-1")

    @pytest.mark.asyncio
    async def test_teclaw_noop(self):
        svc, facade, repo, _, _ = _make_service("TECLAW")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        result = await svc.restart_device("t1", "dev-1")
        assert result.device_uuid == "DEVICE-abc123"

    @pytest.mark.asyncio
    async def test_local_native_restart(self):
        svc, facade, repo, _, _ = _make_service("LOCAL")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="PENDING")
        repo.get_by_id.return_value = updated
        facade.restart_device = AsyncMock()

        result = await svc.restart_device("t1", "dev-1")
        facade.restart_device.assert_awaited_once_with("dev@42")

    @pytest.mark.asyncio
    async def test_poolab_native_restart(self):
        svc, facade, repo, _, _ = _make_service("POOLAB")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated
        facade.restart_device = AsyncMock()

        result = await svc.restart_device("t1", "dev-1")
        facade.restart_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_k8s_native_restart(self):
        svc, facade, repo, _, _ = _make_service("K8S")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated
        facade.restart_device = AsyncMock()

        result = await svc.restart_device("t1", "dev-1")
        facade.restart_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_docker_native_restart(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated
        facade.restart_device = AsyncMock()

        result = await svc.restart_device("t1", "dev-1")
        facade.restart_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_arca_delegates_to_update(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="new-sb@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="new-sb@42")
        repo.get_by_id.return_value = updated

        result = await svc.restart_device("t1", "dev-1")
        facade.destroy_device.assert_awaited_once()
        facade.create_device.assert_awaited_once()


# ---------------------------------------------------------------------------
# DefaultDeviceService.update_device
# ---------------------------------------------------------------------------


class TestUpdateDevice:
    @pytest.mark.asyncio
    async def test_sigma_raises(self):
        svc, facade, repo, _, _ = _make_service("SIGMA")
        record = _make_record(status="ACTIVE")
        repo.get_by_device_uuid.return_value = record
        with pytest.raises(
            ValueError, match="Sigma platform update is not yet implemented"
        ):
            await svc.update_device("t1", "dev-1")

    @pytest.mark.asyncio
    async def test_local_native_update(self):
        svc, facade, repo, _, _ = _make_service("LOCAL")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="PENDING")
        repo.get_by_id.return_value = updated
        facade.update_device = AsyncMock()

        result = await svc.update_device("t1", "dev-1")
        facade.update_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poolab_native_update(self):
        svc, facade, repo, _, _ = _make_service("POOLAB")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated
        facade.update_device = AsyncMock()

        result = await svc.update_device("t1", "dev-1")
        facade.update_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_teclaw_native_update(self):
        svc, facade, repo, _, _ = _make_service("TECLAW")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated
        facade.update_device = AsyncMock()

        result = await svc.update_device("t1", "dev-1")
        facade.update_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_k8s_native_update(self):
        svc, facade, repo, _, _ = _make_service("K8S")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        updated = _make_record(status="ACTIVE")
        repo.get_by_id.return_value = updated
        facade.update_device = AsyncMock()

        result = await svc.update_device("t1", "dev-1")
        facade.update_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_docker_update_destroy_create(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(
            return_value=DockerCreationResult(
                container_id="new-docker@42",
                host_port=8080,
                platform="DOCKER",
                status="ACTIVE",
            )
        )
        updated = _make_record(status="PENDING", provider_device_id="new-docker@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        facade.destroy_device.assert_awaited_once()
        facade.create_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_docker_update_destroy_not_found_ok(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="DOCKER",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "not found"),
            )
        )
        facade.create_device = AsyncMock(
            return_value=DockerCreationResult(
                container_id="new-docker@42",
                host_port=8080,
                platform="DOCKER",
                status="ACTIVE",
            )
        )
        updated = _make_record(status="PENDING", provider_device_id="new-docker@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_docker_update_destroy_error_reraises(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="DOCKER",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.PLATFORM_ERROR, "err"),
            )
        )

        with pytest.raises(DeviceFacadeException):
            await svc.update_device("t1", "dev-1")

    @pytest.mark.asyncio
    async def test_docker_update_no_provider_device_id(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=DockerCreationResult(
                container_id="new-docker@42",
                host_port=8080,
                platform="DOCKER",
                status="ACTIVE",
            )
        )
        updated = _make_record(status="PENDING", provider_device_id="new-docker@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        facade.destroy_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_docker_update_create_failure(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(side_effect=RuntimeError("create failed"))
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_docker_update_unexpected_result_type(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(return_value=MagicMock())
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_arca_update_success(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="new-sb@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="new-sb@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        facade.destroy_device.assert_awaited_once()
        facade.create_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_arca_update_destroy_not_found_ok(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "not found"),
            )
        )
        facade.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="new-sb@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="new-sb@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_arca_update_destroy_other_error_continues(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.PLATFORM_ERROR, "err"),
            )
        )
        facade.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="new-sb@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="new-sb@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_arca_update_no_provider_device_id(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="new-sb@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="new-sb@42")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        facade.destroy_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_arca_update_create_failure(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(side_effect=RuntimeError("create failed"))
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_arca_update_create_failure_no_record(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(side_effect=RuntimeError("create failed"))
        repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Device record not found"):
            await svc.update_device("t1", "dev-1")

    @pytest.mark.asyncio
    async def test_arca_update_unexpected_result_type(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.create_device = AsyncMock(return_value=MagicMock())
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.update_device("t1", "dev-1")
        assert result.status == "FAILED"


# ---------------------------------------------------------------------------
# DefaultDeviceService.destroy_device_by_uuid
# ---------------------------------------------------------------------------


class TestDestroyDeviceByUuid:
    @pytest.mark.asyncio
    async def test_device_not_found_returns_success(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_active_or_updating_by_device_uuid.return_value = None
        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_destroy_success(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True
        repo.update_status_by_device_uuid.assert_called_once()
        repo.soft_delete_by_device_uuid.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_not_found_in_paas_ok(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "not found"),
            )
        )

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_destroy_paas_error_continues(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.PLATFORM_ERROR, "err"),
            )
        )

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.success is False
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_destroy_no_provider_device_id(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id=None)
        repo.get_active_or_updating_by_device_uuid.return_value = record

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True
        facade.destroy_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_destroy_for_restart_skips_status_update(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()

        result = await svc.destroy_device_by_uuid(
            "t1", "dev-1", "user", for_restart=True
        )
        assert result.success is True
        repo.update_status_by_device_uuid.assert_not_called()
        repo.soft_delete_by_device_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_with_hook_success(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(
            return_value=CommandResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                execution_time_ms=10,
                command="echo",
            )
        )

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True
        assert result.hook_result is not None

    @pytest.mark.asyncio
    async def test_destroy_with_hook_failure_continues(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(
            return_value=CommandResult(
                exit_code=1,
                stdout="",
                stderr="error",
                execution_time_ms=10,
                command="echo",
            )
        )

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_destroy_with_hook_stderr_warning(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(
            return_value=CommandResult(
                exit_code=0,
                stdout="ok",
                stderr="warning",
                execution_time_ms=10,
                command="echo",
            )
        )

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_destroy_with_hook_exception_continues(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(side_effect=RuntimeError("hook failed"))

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_destroy_teclaw_skips_hook(self):
        svc, facade, repo, _, _ = _make_service("TECLAW")
        record = _make_record(
            status="ACTIVE",
            provider_type="TECLAW",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()

        result = await svc.destroy_device_by_uuid("t1", "dev-1", "user")
        facade.execute_command.assert_not_awaited()


# ---------------------------------------------------------------------------
# DefaultDeviceService.stop_device_by_uuid
# ---------------------------------------------------------------------------


class TestStopDeviceByUuid:
    @pytest.mark.asyncio
    async def test_not_found_in_active_returns_success(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_active_or_updating_by_device_uuid.return_value = None
        repo.get_by_device_uuid_only.return_value = None
        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_not_found_in_uuid_only_returns_success(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_active_or_updating_by_device_uuid.return_value = None
        record = _make_record(status="STOPPED")
        repo.get_by_device_uuid_only.return_value = record
        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_success(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True
        repo.update_status_by_device_uuid.assert_called_once()
        assert repo.update_status_by_device_uuid.call_args[1]["status"] == "STOPPED"

    @pytest.mark.asyncio
    async def test_stop_found_in_uuid_only(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_active_or_updating_by_device_uuid.return_value = None
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_by_device_uuid_only.return_value = record
        facade.destroy_device = AsyncMock()

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_paas_not_found_ok(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "not found"),
            )
        )

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_paas_error(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id="dev@42")
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock(
            side_effect=DeviceFacadeException(
                operation="destroy_device",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="dev@42",
                original_error=PaasError(ErrorCode.PLATFORM_ERROR, "err"),
            )
        )

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_stop_no_provider_device_id(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(status="ACTIVE", provider_device_id=None)
        repo.get_active_or_updating_by_device_uuid.return_value = record

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.success is True
        facade.destroy_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_with_hook_success(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(
            return_value=CommandResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                execution_time_ms=10,
                command="echo",
            )
        )

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.hook_result is not None

    @pytest.mark.asyncio
    async def test_stop_with_hook_failure(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(
            return_value=CommandResult(
                exit_code=1,
                stdout="",
                stderr="err",
                execution_time_ms=10,
                command="echo",
            )
        )

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_stop_with_hook_exception(self):
        svc, facade, repo, _, _ = _make_service()
        record = _make_record(
            status="ACTIVE",
            provider_device_id="dev@42",
            extra_config={
                "deploy_config": {
                    "before_destroy_cmd_hook": "echo {client_id}",
                    "before_destroy_hook_wait_seconds": 30,
                },
            },
        )
        repo.get_active_or_updating_by_device_uuid.return_value = record
        facade.destroy_device = AsyncMock()
        facade.execute_command = AsyncMock(side_effect=RuntimeError("hook err"))

        result = await svc.stop_device_by_uuid("t1", "dev-1", "user")
        assert result.error_message is not None


# ---------------------------------------------------------------------------
# DefaultDeviceService.start_device
# ---------------------------------------------------------------------------


class TestStartDevice:
    @pytest.mark.asyncio
    async def test_pending_device_not_found(self):
        svc, facade, repo, _, _ = _make_service()
        repo.get_by_device_uuid.return_value = None
        with pytest.raises(ValueError, match="PENDING device not found"):
            await svc.start_device("t1", "dev-1")

    @pytest.mark.asyncio
    async def test_template_not_found(self):
        svc, facade, repo, tpl_svc, _ = _make_service()
        record = _make_record(status="PENDING")
        repo.get_by_device_uuid.return_value = record
        tpl_svc.get_default_or_explicit_template.return_value = None
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_template_no_config(self):
        svc, facade, repo, tpl_svc, _ = _make_service()
        record = _make_record(status="PENDING")
        repo.get_by_device_uuid.return_value = record
        template = MagicMock()
        template.config = None
        template.template_uuid = "tpl-uuid"
        tpl_svc.get_default_or_explicit_template.return_value = template
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_unknown_template_config_type(self):
        svc, facade, repo, tpl_svc, _ = _make_service()
        record = _make_record(status="PENDING")
        repo.get_by_device_uuid.return_value = record
        template = MagicMock()
        template.config = MagicMock()
        template.template_uuid = "tpl-uuid"
        tpl_svc.get_default_or_explicit_template.return_value = template
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_arca_success_no_hook(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="sb-123@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="sb-123@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_start_local_success(self):
        svc, facade, repo, _, _ = _make_service("LOCAL")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=LocalCreationResult(
                container_id="c-123@42",
                platform="LOCAL",
                status="ACTIVE",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="c-123@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_start_poolab_success(self):
        svc, facade, repo, _, _ = _make_service("POOLAB")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=PoolabCreationResult(
                platform="POOLAB",
                status="ACTIVE",
                poolab_id="pid@42",
                poolab_user_id="puid",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="pid@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_start_teclaw_success(self):
        svc, facade, repo, _, _ = _make_service("TECLAW")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=TeClawCreationResult(
                platform="TECLAW",
                status="ACTIVE",
                teclaw_bot_id="bot@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="bot@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_start_k8s_success(self):
        svc, facade, repo, _, _ = _make_service("K8S")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=K8sCreationResult(
                device_id="k8s-dev@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="k8s-dev@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_start_docker_success(self):
        svc, facade, repo, _, _ = _make_service("DOCKER")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=DockerCreationResult(
                container_id="docker@42",
                host_port=8080,
                platform="DOCKER",
                status="ACTIVE",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="docker@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_start_unknown_result_type_fails(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(return_value=MagicMock())
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_paas_create_failure(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(side_effect=RuntimeError("create failed"))
        updated = _make_record(status="FAILED")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_paas_create_failure_no_record(self):
        svc, facade, repo, _, _ = _make_service("ARCA")
        record = _make_record(status="PENDING", provider_device_id=None)
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(side_effect=RuntimeError("create failed"))
        repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Device record not found after update"):
            await svc.start_device("t1", "dev-1")

    @pytest.mark.asyncio
    async def test_start_with_hook_local_skips_hook(self):
        svc, facade, repo, _, _ = _make_service("LOCAL")
        record = _make_record(
            status="PENDING",
            provider_device_id=None,
            extra_config={
                "deploy_config": {
                    "after_create_cmd_hook": "echo {token}",
                    "after_create_hook_wait_seconds": 30,
                    "machine_id": "m1",
                    "user_id": "u1",
                    "tc_bot_id": "b1",
                    "agent_code": "a1",
                },
            },
        )
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=LocalCreationResult(
                container_id="c-123@42",
                platform="LOCAL",
                status="ACTIVE",
            )
        )
        updated = _make_record(status="PENDING", provider_device_id="c-123@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "PENDING"

    @pytest.mark.asyncio
    async def test_start_with_hook_teclaw_fast_path(self):
        svc, facade, repo, _, _ = _make_service("TECLAW")
        record = _make_record(
            status="PENDING",
            provider_device_id=None,
            extra_config={
                "deploy_config": {
                    "after_create_cmd_hook": "echo {token}",
                    "after_create_hook_wait_seconds": 30,
                    "teclaw_bot_config": {},
                },
            },
        )
        repo.get_by_device_uuid.return_value = record
        facade.create_device = AsyncMock(
            return_value=TeClawCreationResult(
                platform="TECLAW",
                status="ACTIVE",
                teclaw_bot_id="bot@42",
            )
        )
        updated = _make_record(status="ACTIVE", provider_device_id="bot@42")
        repo.get_by_id.return_value = updated

        result = await svc.start_device("t1", "dev-1")
        assert result.status == "ACTIVE"

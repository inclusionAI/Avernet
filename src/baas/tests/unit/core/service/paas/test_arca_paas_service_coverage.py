"""Comprehensive coverage tests for ArcaPaasService.

Covers all methods, branches, error paths, and edge cases not already
tested in test_arca_paas_service.py (which focuses on destroy_device
storage cleanup and _safe_repr).

Areas covered:
- __init__ validation (None credentials, missing base_url, missing api_key)
- get_credentials / get_platform_type
- _translate_error (all branches: NotFound, Timeout with COMMAND_FAILED,
  Timeout with non-COMMAND_FAILED, ArcaSandboxError with "connection",
  ArcaSandboxError with "unavailable", ArcaSandboxError generic, generic Exception)
- _check_sandbox_ready (ready, timeout exceeded, not ready yet)
- _wait_for_sandbox_ready (async, ready on first check, timeout)
- _wait_for_sandbox_ready_sync (ready, timeout)
- _build_mount_points (None, empty, MountPoint objects, dict READ_ONLY,
  dict READ_WRITE, dict default permission)
- create_device / _create_device_sync (success, missing template_id,
  error during creation, PaasError re-raise, info with/without model_dump_json,
  status with/without .value, outbound_operation_rule None vs set, docker_image)
- destroy_device / _destroy_device_sync (storage_id not a string,
  ArcaSandboxError idempotent "not found", ArcaSandboxError idempotent
  "does not exist", ArcaSandboxError non-idempotent, generic Exception,
  destroy returns non-bool, no tenant_name)
- execute_command / _execute_command_sync (success, timeout error,
  generic error, result without stdout/stderr/elapsed_time)
- get_device_info / _get_device_info_sync (success, not found, generic error,
  status with/without .value)
- update_outbound_operation_rule / _update_outbound_operation_rule_sync
  (success, not found, generic error, result falsy)
- resolve_ws_conn_info
- resolve_invoke_http_info (with path, without path)
- invoke_http_in_device (NotImplementedError)
- restart_device (NotImplementedError)
- update_device (NotImplementedError)
- get_info / _get_info_sync (success, not found, generic error,
  ttl_timestamp None, status with/without .value)
- extend_ttl / _extend_ttl_sync (success bool, success object, not found,
  generic error)
- update_device_ttl / _update_device_ttl_sync (success, no TTL info,
  already at target, extension failed, not found, generic error,
  with @ suffix)
- SandboxInfo class
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from secbaas.community.api.device_manage import (
    ArcaCreateConfig,
    ArcaCreationResult,
    ArcaCredentials,
    ArcaDeviceInfo,
    CommandResult,
    DeviceCreateConfig,
    ErrorCode,
    MountPermission,
    MountPoint,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    PaasError,
    ResourceSpecification,
    Storage,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas import ArcaPaasService
from secbaas.community.core.service.paas._arca_paas_service import SandboxInfo
from secbaas.community.spi.sandbox.arca import (
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
    ArcaSandboxTimeoutError,
)

# ──────────────────────────── Fixtures ────────────────────────────


@pytest.fixture
def mock_sandbox():
    """Create a mock ArcaSandbox."""
    mock = MagicMock()
    mock.is_ready = True
    mock.sandbox_id = "sandbox-123"
    return mock


@pytest.fixture
def mock_plugin(mock_sandbox):
    """Create a mock ArcaSandboxPlugin."""
    mock = MagicMock()
    mock.connect_sync_sandbox.return_value = mock_sandbox
    mock.create_sync_sandbox.return_value = mock_sandbox
    mock.delete_storage.return_value = True
    return mock


@pytest.fixture
def arca_credentials():
    """Create test ArcaCredentials."""
    return ArcaCredentials(
        base_url="http://arca.test:8080",
        api_key="test-key",
        timeout=30.0,
        template_id=1,
        template_uuid="tpl-test-001",
        tenant_name="test-tenant",
        arca_template_id="arca-tpl-001",
    )


@pytest.fixture
def service(arca_credentials, mock_plugin):
    """Create an ArcaPaasService with mocked dependencies."""
    return ArcaPaasService(
        credentials=arca_credentials,
        arca_sandbox_plugin=mock_plugin,
    )


def _make_sandbox_info(**overrides):
    """Build a MagicMock sandbox info object with sensible defaults."""
    defaults = dict(
        sandbox_id="sb-001",
        status=MagicMock(value="READY"),
        template_id="tpl-001",
        resources=ResourceSpecification(cpu=2, memory=4096),
        ttl_in_minutes=1440.0,
        envs={"KEY": "VALUE"},
        snapshot_id="snap-001",
        metadata={"meta": "data"},
        outbound_operation_rule=None,
        ip_address="10.0.0.1",
        ttl_seconds=3600,
        ttl_timestamp=int(datetime.now().timestamp() * 1000) + 3600000,
        created_at=datetime.now(),
        name="test-device",
        description="test desc",
        resource_spec={"cpu": 2},
        mount_points=[{"id": "m1"}],
        storage={
            "type": "NAS",
            "path": "/nas",
            "storage_id": "stor-001",
            "quota": "1Gi",
            "permission": "0777",
        },
    )
    defaults.update(overrides)
    info = MagicMock()
    for k, v in defaults.items():
        setattr(info, k, v)
    # Configure model_dump_json for the info mock
    info.model_dump_json = MagicMock(return_value='{"sandbox_id": "sb-001"}')
    return info


# ──────────────────────────── __init__ tests ────────────────────────────


class TestInit:
    """Tests for ArcaPaasService.__init__ validation."""

    def test_init_none_credentials_raises(self, mock_plugin):
        with pytest.raises(ValueError, match="credentials is required"):
            ArcaPaasService(credentials=None, arca_sandbox_plugin=mock_plugin)

    def test_init_missing_base_url_raises(self, mock_plugin):
        creds = ArcaCredentials(
            api_key="key", base_url=None, template_id=1, template_uuid="tpl-test-001"
        )
        with pytest.raises(ValueError, match="credentials.base_url is required"):
            ArcaPaasService(credentials=creds, arca_sandbox_plugin=mock_plugin)

    def test_init_empty_base_url_raises(self, mock_plugin):
        creds = ArcaCredentials(
            api_key="key", base_url="", template_id=1, template_uuid="tpl-test-001"
        )
        with pytest.raises(ValueError, match="credentials.base_url is required"):
            ArcaPaasService(credentials=creds, arca_sandbox_plugin=mock_plugin)

    def test_init_missing_api_key_raises(self, mock_plugin):
        creds = ArcaCredentials(
            base_url="http://test",
            api_key=None,
            template_id=1,
            template_uuid="tpl-test-001",
        )
        with pytest.raises(ValueError, match="credentials.api_key is required"):
            ArcaPaasService(credentials=creds, arca_sandbox_plugin=mock_plugin)

    def test_init_empty_api_key_raises(self, mock_plugin):
        creds = ArcaCredentials(
            base_url="http://test",
            api_key="",
            template_id=1,
            template_uuid="tpl-test-001",
        )
        with pytest.raises(ValueError, match="credentials.api_key is required"):
            ArcaPaasService(credentials=creds, arca_sandbox_plugin=mock_plugin)

    def test_init_success(self, arca_credentials, mock_plugin):
        svc = ArcaPaasService(
            credentials=arca_credentials,
            arca_sandbox_plugin=mock_plugin,
        )
        assert svc._credentials is arca_credentials
        assert svc._arca_sandbox_plugin is mock_plugin
        assert svc._logger is not None


# ──────────────────────────── get_credentials / get_platform_type ──────────────


class TestGetCredentialsAndPlatformType:
    @pytest.mark.asyncio
    async def test_get_credentials(self, service, arca_credentials):
        result = await service.get_credentials()
        assert result is arca_credentials

    @pytest.mark.asyncio
    async def test_get_platform_type(self, service):
        result = await service.get_platform_type()
        assert result == TenantType.ARCA


# ──────────────────────────── _translate_error tests ────────────────────────────


class TestTranslateError:
    """Tests for _translate_error covering all branches."""

    def test_translate_arca_not_found(self, service):
        err = ArcaSandboxNotFoundError("not found")
        result = service._translate_error(err, ErrorCode.DEVICE_DESTROY_FAILED)
        assert result.code == ErrorCode.DEVICE_NOT_FOUND
        assert "Device not found" in result.message
        assert result.platform_error is err

    def test_translate_timeout_with_command_failed(self, service):
        err = ArcaSandboxTimeoutError("timed out")
        result = service._translate_error(err, ErrorCode.COMMAND_FAILED)
        assert result.code == ErrorCode.COMMAND_TIMEOUT
        assert "timed out" in result.message
        assert result.platform_error is err

    def test_translate_timeout_with_non_command_failed(self, service):
        err = ArcaSandboxTimeoutError("timed out")
        result = service._translate_error(err, ErrorCode.DEVICE_CREATION_FAILED)
        assert result.code == ErrorCode.DEVICE_CREATION_FAILED
        assert "Operation timed out" in result.message
        assert result.platform_error is err

    def test_translate_arca_error_with_connection(self, service):
        err = ArcaSandboxError("Connection refused to platform")
        result = service._translate_error(err, ErrorCode.COMMAND_FAILED)
        assert result.code == ErrorCode.PLATFORM_UNAVAILABLE
        assert "Arca platform unavailable" in result.message
        assert result.platform_error is err

    def test_translate_arca_error_with_unavailable(self, service):
        err = ArcaSandboxError("Service unavailable")
        result = service._translate_error(err, ErrorCode.COMMAND_FAILED)
        assert result.code == ErrorCode.PLATFORM_UNAVAILABLE
        assert "Arca platform unavailable" in result.message

    def test_translate_arca_error_generic(self, service):
        err = ArcaSandboxError("some other error")
        result = service._translate_error(err, ErrorCode.DEVICE_DESTROY_FAILED)
        assert result.code == ErrorCode.DEVICE_DESTROY_FAILED
        assert result.message == "some other error"
        assert result.platform_error is err

    def test_translate_generic_exception(self, service):
        err = RuntimeError("boom")
        result = service._translate_error(err, ErrorCode.DEVICE_NOT_FOUND)
        assert result.code == ErrorCode.DEVICE_NOT_FOUND
        assert "boom" in result.message
        assert result.platform_error is err


# ──────────────────────────── _check_sandbox_ready tests ────────────────────────


class TestCheckSandboxReady:
    def test_check_ready(self, service, mock_sandbox):
        mock_sandbox.is_ready = True
        result = service._check_sandbox_ready(mock_sandbox, "sb-001", 5.0, 300)
        assert result is True

    def test_check_not_ready_not_timeout(self, service, mock_sandbox):
        mock_sandbox.is_ready = False
        result = service._check_sandbox_ready(mock_sandbox, "sb-001", 5.0, 300)
        assert result is False

    def test_check_timeout_exceeded(self, service, mock_sandbox):
        mock_sandbox.is_ready = False
        mock_info = MagicMock()
        mock_info.sandbox_id = "sb-001"
        mock_info.status = "PENDING"
        mock_sandbox.get_info.return_value = mock_info
        with pytest.raises(PaasError) as exc_info:
            service._check_sandbox_ready(mock_sandbox, "sb-001", 350.0, 300)
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_READY
        assert "did not become ready" in exc_info.value.message
        assert "300s" in exc_info.value.message


# ──────────────────────────── _wait_for_sandbox_ready tests ────────────────────


class TestWaitForSandboxReady:
    @pytest.mark.asyncio
    async def test_wait_ready_first_check(self, service, mock_sandbox):
        mock_sandbox.is_ready = True
        mock_info = MagicMock()
        mock_info.sandbox_id = "sb-001"
        mock_sandbox.get_info.return_value = mock_info
        await service._wait_for_sandbox_ready(mock_sandbox, timeout_seconds=10)
        # Should not need to sleep
        assert mock_sandbox.is_ready

    @pytest.mark.asyncio
    async def test_wait_timeout(self, service, mock_sandbox):
        mock_sandbox.is_ready = False
        mock_info = MagicMock()
        mock_info.sandbox_id = "sb-001"
        mock_info.status = "PENDING"
        mock_sandbox.get_info.return_value = mock_info

        # Patch time.time to simulate elapsed time exceeding timeout
        call_count = [0]
        original_time = __import__("time").time

        def mock_time():
            call_count[0] += 1
            return original_time() + (call_count[0] * 400)

        with patch("time.time", side_effect=mock_time):
            with pytest.raises(PaasError) as exc_info:
                await service._wait_for_sandbox_ready(
                    mock_sandbox, timeout_seconds=300, poll_interval=0.001
                )
            assert exc_info.value.code == ErrorCode.DEVICE_NOT_READY


class TestWaitForSandboxReadySync:
    def test_sync_ready_first_check(self, service, mock_sandbox):
        mock_sandbox.is_ready = True
        mock_info = MagicMock()
        mock_info.sandbox_id = "sb-001"
        mock_sandbox.get_info.return_value = mock_info
        service._wait_for_sandbox_ready_sync(mock_sandbox, timeout_seconds=10)

    def test_sync_timeout(self, service, mock_sandbox):
        mock_sandbox.is_ready = False
        mock_info = MagicMock()
        mock_info.sandbox_id = "sb-001"
        mock_info.status = "PENDING"
        mock_sandbox.get_info.return_value = mock_info

        import time as real_time

        start = real_time.time()
        call_count = [0]

        def mock_time():
            call_count[0] += 1
            return start + (call_count[0] * 400)

        with patch("time.time", side_effect=mock_time):
            with patch("time.sleep"):
                with pytest.raises(PaasError) as exc_info:
                    service._wait_for_sandbox_ready_sync(
                        mock_sandbox, timeout_seconds=300, poll_interval=0.001
                    )
                assert exc_info.value.code == ErrorCode.DEVICE_NOT_READY


# ──────────────────────────── _build_mount_points tests ────────────────────────


class TestBuildMountPoints:
    def test_none_config(self, service):
        result = service._build_mount_points(None)
        assert result == []

    def test_empty_list(self, service):
        result = service._build_mount_points([])
        assert result == []

    def test_with_mount_point_objects(self, service):
        mp1 = MountPoint(id="mp1", remote_dir="/remote1", local_dir="/local1")
        mp2 = MountPoint(
            id="mp2",
            remote_dir="/remote2",
            local_dir="/local2",
            permission=MountPermission.READ_WRITE,
        )
        result = service._build_mount_points([mp1, mp2])
        assert len(result) == 2
        assert result[0] is mp1
        assert result[1] is mp2

    def test_with_dict_read_write(self, service):
        mp_dict = {
            "id": "mp1",
            "remote_dir": "/remote",
            "local_dir": "/local",
            "permission": "READ_WRITE",
        }
        result = service._build_mount_points([mp_dict])
        assert len(result) == 1
        assert result[0].permission == MountPermission.READ_WRITE
        assert result[0].id == "mp1"

    def test_with_dict_read_only_explicit(self, service):
        mp_dict = {
            "id": "mp1",
            "remote_dir": "/remote",
            "local_dir": "/local",
            "permission": "READ_ONLY",
        }
        result = service._build_mount_points([mp_dict])
        assert len(result) == 1
        assert result[0].permission == MountPermission.READ_ONLY

    def test_with_dict_default_permission(self, service):
        mp_dict = {
            "remote_dir": "/remote",
            "local_dir": "/local",
        }
        result = service._build_mount_points([mp_dict])
        assert len(result) == 1
        assert result[0].permission == MountPermission.READ_ONLY
        assert result[0].id == "default"
        assert result[0].remote_dir == "/remote"
        assert result[0].local_dir == "/local"

    def test_with_dict_permission_case_insensitive(self, service):
        mp_dict = {
            "id": "mp1",
            "remote_dir": "/r",
            "local_dir": "/l",
            "permission": "read_write",
        }
        result = service._build_mount_points([mp_dict])
        assert result[0].permission == MountPermission.READ_WRITE

    def test_mixed_objects_and_dicts(self, service):
        mp_obj = MountPoint(id="obj", remote_dir="/r1", local_dir="/l1")
        mp_dict = {
            "id": "dict",
            "remote_dir": "/r2",
            "local_dir": "/l2",
            "permission": "READ_WRITE",
        }
        result = service._build_mount_points([mp_obj, mp_dict])
        assert len(result) == 2
        assert result[0] is mp_obj
        assert result[1].id == "dict"


# ──────────────────────────── create_device / _create_device_sync tests ────────


class TestCreateDevice:
    def test_create_device_sync_success(self, service, mock_sandbox, mock_plugin):
        """Test successful device creation."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(
            template_id="tpl-001",
            ttl_in_minutes=60,
            envs={"ENV": "val"},
            mount_points=[
                MountPoint(id="mp1", remote_dir="/r", local_dir="/l"),
            ],
            metadata={"k": "v"},
            docker_image="my-image:latest",
        )

        result = service._create_device_sync(config)

        assert isinstance(result, ArcaCreationResult)
        assert result.platform == "arca"
        assert result.status == "READY"
        assert result.sandbox_id == "sb-001"
        assert result.template_id == "tpl-001"
        assert result.ttl_in_minutes == 1440.0
        assert result.envs == {"KEY": "VALUE"}
        assert result.snapshot_id == "snap-001"
        assert result.metadata == {"meta": "data"}

        # Verify create_sync_sandbox was called with correct params
        mock_plugin.create_sync_sandbox.assert_called_once()
        call_kwargs = mock_plugin.create_sync_sandbox.call_args.kwargs
        assert call_kwargs["template_id"] == "tpl-001"
        assert call_kwargs["ttl_in_minutes"] == 60
        assert call_kwargs["image"] == "my-image:latest"

    def test_create_device_sync_uses_creds_template_id(
        self, service, mock_sandbox, mock_plugin
    ):
        """When config.template_id is None, fall back to credentials.arca_template_id."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(template_id=None)

        result = service._create_device_sync(config)
        assert result.sandbox_id == "sb-001"
        call_kwargs = mock_plugin.create_sync_sandbox.call_args.kwargs
        assert call_kwargs["template_id"] == "arca-tpl-001"

    def test_create_device_sync_missing_template_id(self, service, mock_plugin):
        """PaasError when template_id is missing from both config and credentials."""
        creds = ArcaCredentials(
            base_url="http://test",
            api_key="key",
            template_id=1,
            template_uuid="tpl-test-001",
            arca_template_id=None,
        )
        svc = ArcaPaasService(credentials=creds, arca_sandbox_plugin=mock_plugin)
        config = ArcaCreateConfig(template_id=None)
        with pytest.raises(PaasError) as exc_info:
            svc._create_device_sync(config)
        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED
        assert "template_id" in exc_info.value.message

    def test_create_device_sync_info_without_model_dump_json(
        self, service, mock_sandbox
    ):
        """Info object without model_dump_json uses vars() fallback."""
        info = _make_sandbox_info()
        del info.model_dump_json
        # Ensure hasattr(info, "model_dump_json") is False
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(template_id="tpl-001")
        result = service._create_device_sync(config)
        assert result.sandbox_id == "sb-001"

    def test_create_device_sync_status_without_value(self, service, mock_sandbox):
        """Status without .value attribute uses str() conversion."""
        info = _make_sandbox_info(status="RUNNING")
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(template_id="tpl-001")
        result = service._create_device_sync(config)
        assert result.status == "RUNNING"

    def test_create_device_sync_with_outbound_rule(self, service, mock_sandbox):
        """outbound_operation_rule is set in result when info has it."""
        rule = OutBoundOperationRule()
        info = _make_sandbox_info(outbound_operation_rule=rule)
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(template_id="tpl-001")
        result = service._create_device_sync(config)
        assert result.outbound_operation_rule is rule

    def test_create_device_sync_paas_error_reraise(self, service, mock_sandbox):
        """PaasError raised internally is re-raised as-is."""
        mock_sandbox.is_ready = False
        mock_info = MagicMock()
        mock_info.sandbox_id = "sb-001"
        mock_info.status = "PENDING"
        mock_sandbox.get_info.return_value = mock_info

        import time as real_time

        start = real_time.time()
        call_count = [0]

        def mock_time():
            call_count[0] += 1
            return start + (call_count[0] * 400)

        config = ArcaCreateConfig(template_id="tpl-001")
        with patch("time.time", side_effect=mock_time):
            with patch("time.sleep"):
                with pytest.raises(PaasError) as exc_info:
                    service._create_device_sync(config)
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_READY

    def test_create_device_sync_generic_exception_translated(
        self, service, mock_plugin
    ):
        """Generic exception during creation is translated to PaasError."""
        mock_plugin.create_sync_sandbox.side_effect = RuntimeError("SDK error")
        config = ArcaCreateConfig(template_id="tpl-001")
        with pytest.raises(PaasError) as exc_info:
            service._create_device_sync(config)
        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED

    def test_create_device_sync_arca_sandbox_error_translated(
        self, service, mock_plugin
    ):
        """ArcaSandboxError during creation is translated to PaasError."""
        mock_plugin.create_sync_sandbox.side_effect = ArcaSandboxError(
            "creation failed"
        )
        config = ArcaCreateConfig(template_id="tpl-001")
        with pytest.raises(PaasError) as exc_info:
            service._create_device_sync(config)
        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED

    def test_create_device_sync_with_storage(self, service, mock_sandbox, mock_plugin):
        """Create with storage config passes storage to plugin."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info
        storage = Storage(type="nas", path="/nas", quota="100G")

        config = ArcaCreateConfig(template_id="tpl-001", storage=storage)
        result = service._create_device_sync(config)
        assert result.sandbox_id == "sb-001"
        call_kwargs = mock_plugin.create_sync_sandbox.call_args.kwargs
        assert call_kwargs["storage"] is storage

    def test_create_device_sync_no_mount_points(
        self, service, mock_sandbox, mock_plugin
    ):
        """When mount_points is None, None is passed to plugin."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(template_id="tpl-001")
        result = service._create_device_sync(config)
        call_kwargs = mock_plugin.create_sync_sandbox.call_args.kwargs
        assert call_kwargs["mount_points"] is None

    @pytest.mark.asyncio
    async def test_create_device_async(self, service, mock_sandbox):
        """Test the async create_device wrapper."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        config = ArcaCreateConfig(template_id="tpl-001")
        result = await service.create_device(config)
        assert isinstance(result, ArcaCreationResult)
        assert result.sandbox_id == "sb-001"


# ──────────────────────────── destroy_device tests ────────────────────────────


class TestDestroyDevice:
    def test_destroy_sync_storage_id_not_string(self, service, mock_sandbox):
        """storage_id present but not a string — skip cleanup."""
        info = MagicMock()
        info.storage = {"storage_id": 12345}
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.return_value = True

        with patch.object(service._logger, "warning") as mock_warn:
            result = service._destroy_device_sync("dev-001")
        assert result is True
        service._arca_sandbox_plugin.delete_storage.assert_not_called()
        assert any(
            "storage_id is not a string" in str(c) for c in mock_warn.call_args_list
        )

    def test_destroy_sync_arca_error_not_found_idempotent(self, service, mock_sandbox):
        """ArcaSandboxError with 'sandbox not found' is idempotent."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.side_effect = ArcaSandboxError("sandbox not found")

        with patch.object(service._logger, "warning") as mock_warn:
            result = service._destroy_device_sync("dev-001")
        assert result is True
        assert any(
            "not found during destroy" in str(c) for c in mock_warn.call_args_list
        )

    def test_destroy_sync_arca_error_does_not_exist_idempotent(
        self, service, mock_sandbox
    ):
        """ArcaSandboxError with 'does not exist' is idempotent."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.side_effect = ArcaSandboxError(
            "sandbox does not exist anymore"
        )

        with patch.object(service._logger, "warning") as mock_warn:
            result = service._destroy_device_sync("dev-001")
        assert result is True
        assert any(
            "not found during destroy" in str(c) for c in mock_warn.call_args_list
        )

    def test_destroy_sync_arca_error_non_idempotent_raises(self, service, mock_sandbox):
        """ArcaSandboxError with non-idempotent message raises PaasError."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.side_effect = ArcaSandboxError("permission denied")

        with pytest.raises(PaasError) as exc_info:
            service._destroy_device_sync("dev-001")
        assert exc_info.value.code == ErrorCode.DEVICE_DESTROY_FAILED

    def test_destroy_sync_generic_exception_raises(self, service, mock_sandbox):
        """Generic exception during destroy is translated to PaasError."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.side_effect = RuntimeError("unexpected")

        with pytest.raises(PaasError) as exc_info:
            service._destroy_device_sync("dev-001")
        assert exc_info.value.code == ErrorCode.DEVICE_DESTROY_FAILED

    def test_destroy_sync_destroy_returns_object_with_success(
        self, service, mock_sandbox
    ):
        """destroy() returns an object with .success attribute."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        destroy_result = MagicMock()
        destroy_result.success = True
        mock_sandbox.destroy.return_value = destroy_result

        result = service._destroy_device_sync("dev-001")
        assert result is True

    def test_destroy_sync_destroy_returns_object_with_success_false(
        self, service, mock_sandbox
    ):
        """destroy() returns an object with .success=False."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        destroy_result = MagicMock()
        destroy_result.success = False
        mock_sandbox.destroy.return_value = destroy_result

        result = service._destroy_device_sync("dev-001")
        assert result is False

    def test_destroy_sync_no_tenant_name(
        self, arca_credentials, mock_plugin, mock_sandbox
    ):
        """When tenant_name is None, storage cleanup is skipped."""
        creds = ArcaCredentials(
            base_url="http://test",
            api_key="key",
            template_id=1,
            template_uuid="tpl-test-001",
            tenant_name=None,
        )
        svc = ArcaPaasService(credentials=creds, arca_sandbox_plugin=mock_plugin)

        info = MagicMock()
        info.storage = {"storage_id": "stor-001"}
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.return_value = True

        result = svc._destroy_device_sync("dev-001")
        assert result is True
        svc._arca_sandbox_plugin.delete_storage.assert_not_called()

    def test_destroy_sync_destroy_returns_true_bool(self, service, mock_sandbox):
        """destroy() returns True (bool)."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.return_value = True

        result = service._destroy_device_sync("dev-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_destroy_device_async(self, service, mock_sandbox):
        """Test the async destroy_device wrapper."""
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.return_value = True

        result = await service.destroy_device("dev-001")
        assert result is True

    def test_destroy_sync_connect_fails_then_destroy_succeeds(
        self, service, mock_plugin, mock_sandbox
    ):
        """First connect fails (non-NotFoundError), second connect succeeds and destroy works."""
        mock_plugin.connect_sync_sandbox.side_effect = [
            RuntimeError("connect failed"),
            mock_sandbox,
        ]
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.return_value = True

        result = service._destroy_device_sync("dev-001")
        assert result is True

    def test_destroy_sync_connect_fails_then_reconnect_and_destroy(
        self, service, mock_plugin, mock_sandbox
    ):
        """First connect fails (generic), second connect succeeds and destroy works."""
        mock_plugin.connect_sync_sandbox.side_effect = [
            RuntimeError("connect failed"),
            mock_sandbox,
        ]
        info = MagicMock()
        info.storage = None
        mock_sandbox.get_info.return_value = info
        mock_sandbox.destroy.return_value = True

        with patch.object(service._logger, "warning") as mock_warn:
            result = service._destroy_device_sync("dev-001")
        assert result is True
        assert any(
            "Failed to connect to sandbox before destroy" in str(c)
            for c in mock_warn.call_args_list
        )


# ──────────────────────────── execute_command tests ────────────────────────────


class TestExecuteCommand:
    def test_execute_command_sync_success(self, service, mock_sandbox):
        """Test successful command execution."""
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = "output"
        exec_result.stderr = ""
        exec_result.elapsed_time = 500
        mock_sandbox.exec_command.return_value = exec_result

        result = service._execute_command_sync("dev-001", "ls -la", {"ENV": "v"}, 30)

        assert isinstance(result, CommandResult)
        assert result.exit_code == 0
        assert result.stdout == "output"
        assert result.stderr == ""
        assert result.execution_time_ms == 500
        assert result.command == "ls -la"
        assert result.env == {"ENV": "v"}

        mock_sandbox.exec_command.assert_called_once_with(
            cmd="ls -la",
            timeout_in_millis=30000,
            envs={"ENV": "v"},
        )

    def test_execute_command_sync_no_stdout_stderr_elapsed(self, service, mock_sandbox):
        """Result without stdout/stderr/elapsed_time attributes."""
        exec_result = MagicMock()
        del exec_result.stdout
        del exec_result.stderr
        del exec_result.elapsed_time
        exec_result.exit_code = 1
        mock_sandbox.exec_command.return_value = exec_result

        result = service._execute_command_sync("dev-001", "cmd", None, 10)

        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.execution_time_ms == 0
        assert result.env is None

    def test_execute_command_sync_timeout_error(self, service, mock_sandbox):
        """ArcaSandboxTimeoutError is translated to COMMAND_TIMEOUT."""
        mock_sandbox.exec_command.side_effect = ArcaSandboxTimeoutError("timed out")

        with pytest.raises(PaasError) as exc_info:
            service._execute_command_sync("dev-001", "cmd", None, 30)
        assert exc_info.value.code == ErrorCode.COMMAND_TIMEOUT

    def test_execute_command_sync_not_found_error(self, service, mock_plugin):
        """ArcaSandboxNotFoundError is translated to DEVICE_NOT_FOUND."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "not found"
        )
        with pytest.raises(PaasError) as exc_info:
            service._execute_command_sync("dev-001", "cmd", None, 30)
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    def test_execute_command_sync_generic_error(self, service, mock_sandbox):
        """Generic exception is translated to COMMAND_FAILED."""
        mock_sandbox.exec_command.side_effect = RuntimeError("exec failed")

        with pytest.raises(PaasError) as exc_info:
            service._execute_command_sync("dev-001", "cmd", None, 30)
        assert exc_info.value.code == ErrorCode.COMMAND_FAILED

    @pytest.mark.asyncio
    async def test_execute_command_async(self, service, mock_sandbox):
        """Test the async execute_command wrapper."""
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = "ok"
        exec_result.stderr = ""
        exec_result.elapsed_time = 100
        mock_sandbox.exec_command.return_value = exec_result

        result = await service.execute_command("dev-001", "echo hi")
        assert result.exit_code == 0
        assert result.stdout == "ok"
        assert result.command == "echo hi"
        assert result.env is None


# ──────────────────────────── get_device_info tests ────────────────────────────


class TestGetDeviceInfo:
    def test_get_device_info_sync_success(self, service, mock_sandbox):
        """Test successful get_device_info."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        result = service._get_device_info_sync("dev-001")

        assert isinstance(result, ArcaDeviceInfo)
        assert result.platform == "arca"
        assert result.status == "READY"
        assert result.sandbox_id == "sb-001"
        assert result.template_id == "tpl-001"
        assert result.ip_address == "10.0.0.1"
        assert result.ttl_seconds == 3600
        assert result.name == "test-device"
        assert result.description == "test desc"

    def test_get_device_info_sync_status_without_value(self, service, mock_sandbox):
        """Status without .value attribute uses str() conversion."""
        info = _make_sandbox_info(status="RUNNING")
        mock_sandbox.get_info.return_value = info

        result = service._get_device_info_sync("dev-001")
        assert result.status == "RUNNING"

    def test_get_device_info_sync_not_found(self, service, mock_plugin):
        """ArcaSandboxNotFoundError is translated to DEVICE_NOT_FOUND."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "not found"
        )
        with pytest.raises(PaasError) as exc_info:
            service._get_device_info_sync("dev-001")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND
        assert "dev-001" in exc_info.value.message

    def test_get_device_info_sync_generic_error(self, service, mock_sandbox):
        """Generic exception is translated to DEVICE_NOT_FOUND."""
        mock_sandbox.get_info.side_effect = RuntimeError("info failed")
        with pytest.raises(PaasError) as exc_info:
            service._get_device_info_sync("dev-001")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_device_info_async(self, service, mock_sandbox):
        """Test the async get_device_info wrapper."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        result = await service.get_device_info("dev-001")
        assert isinstance(result, ArcaDeviceInfo)
        assert result.sandbox_id == "sb-001"


# ──────────────────────────── update_outbound_operation_rule tests ─────────────


class TestUpdateOutboundOperationRule:
    def test_update_outbound_sync_success(self, service, mock_sandbox):
        """Test successful update_outbound_operation_rule."""
        mock_sandbox.update_outbound_rule.return_value = True

        rule = OutBoundOperationRule()
        result = service._update_outbound_operation_rule_sync("dev-001", rule)

        assert result is True
        mock_sandbox.update_outbound_rule.assert_called_once_with(
            rule=rule,
            updated_mode=OutBoundOperationRuleUpdatedMode.REPLACE,
        )

    def test_update_outbound_sync_returns_object(self, service, mock_sandbox):
        """update_outbound_rule returns truthy non-bool."""
        mock_sandbox.update_outbound_rule.return_value = MagicMock()

        rule = OutBoundOperationRule()
        result = service._update_outbound_operation_rule_sync("dev-001", rule)
        assert result is True

    def test_update_outbound_sync_returns_false(self, service, mock_sandbox):
        """update_outbound_rule returns False."""
        mock_sandbox.update_outbound_rule.return_value = False

        rule = OutBoundOperationRule()
        result = service._update_outbound_operation_rule_sync("dev-001", rule)
        assert result is False

    def test_update_outbound_sync_not_found(self, service, mock_plugin):
        """ArcaSandboxNotFoundError is translated to DEVICE_NOT_FOUND."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "not found"
        )
        rule = OutBoundOperationRule()
        with pytest.raises(PaasError) as exc_info:
            service._update_outbound_operation_rule_sync("dev-001", rule)
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    def test_update_outbound_sync_generic_error(self, service, mock_sandbox):
        """Generic exception is translated to DEVICE_UNAVAILABLE."""
        mock_sandbox.update_outbound_rule.side_effect = RuntimeError("update failed")
        rule = OutBoundOperationRule()
        with pytest.raises(PaasError) as exc_info:
            service._update_outbound_operation_rule_sync("dev-001", rule)
        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE

    def test_update_outbound_sync_append_mode(self, service, mock_sandbox):
        """APPEND mode is forwarded to sandbox.update_outbound_rule."""
        mock_sandbox.update_outbound_rule.return_value = True

        rule = OutBoundOperationRule()
        result = service._update_outbound_operation_rule_sync(
            "dev-001", rule, mode=OutBoundOperationRuleUpdatedMode.APPEND
        )

        assert result is True
        mock_sandbox.update_outbound_rule.assert_called_once_with(
            rule=rule,
            updated_mode=OutBoundOperationRuleUpdatedMode.APPEND,
        )

    @pytest.mark.asyncio
    async def test_update_outbound_async(self, service, mock_sandbox):
        """Test the async update_outbound_operation_rule wrapper."""
        mock_sandbox.update_outbound_rule.return_value = True
        rule = OutBoundOperationRule()
        result = await service.update_outbound_operation_rule("dev-001", rule)
        assert result is True


# ──────────────────────────── resolve_ws_conn_info tests ───────────────────────


class TestResolveWsConnInfo:
    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info(self, service, mock_plugin):
        """Test resolve_ws_conn_info delegates to plugin."""
        mock_ws_info = MagicMock()
        mock_plugin.resolve_ws_conn_info.return_value = mock_ws_info

        result = await service.resolve_ws_conn_info("dev-001", 8080, "/ws/path")

        mock_plugin.resolve_ws_conn_info.assert_called_once_with(
            paas_device_id="dev-001",
            port=8080,
            path="/ws/path",
            template_id=1,
        )
        assert result is mock_ws_info


# ──────────────────────────── resolve_invoke_http_info tests ───────────────────


class TestResolveInvokeHttpInfo:
    @pytest.mark.asyncio
    async def test_resolve_invoke_http_info_with_path(self, service, mock_plugin):
        """Test resolve_invoke_http_info with a path."""
        mock_http_info = MagicMock()
        mock_plugin.resolve_http_connection_info.return_value = mock_http_info

        result = await service.resolve_invoke_http_info("dev-001", 8080, "/api/v1")

        mock_plugin.resolve_http_connection_info.assert_called_once_with(
            paas_device_id="dev-001",
            port=8080,
            path="/api/v1",
            template_id=1,
        )
        assert result is mock_http_info

    @pytest.mark.asyncio
    async def test_resolve_invoke_http_info_without_path(self, service, mock_plugin):
        """Test resolve_invoke_http_info with path=None defaults to '/'."""
        mock_http_info = MagicMock()
        mock_plugin.resolve_http_connection_info.return_value = mock_http_info

        result = await service.resolve_invoke_http_info("dev-001", 8080, None)

        mock_plugin.resolve_http_connection_info.assert_called_once_with(
            paas_device_id="dev-001",
            port=8080,
            path="/",
            template_id=1,
        )
        assert result is mock_http_info


# ──────────────────────────── Stub methods tests ───────────────────────────────


class TestStubMethods:
    @pytest.mark.asyncio
    async def test_invoke_http_in_device_raises(self, service):
        with pytest.raises(
            NotImplementedError, match="does not support HTTP invocation"
        ):
            await service.invoke_http_in_device(
                "dev-001", "GET", 8080, "/api", None, {}, b""
            )

    @pytest.mark.asyncio
    async def test_restart_device_raises(self, service):
        with pytest.raises(
            NotImplementedError, match="restart_device not yet implemented"
        ):
            await service.restart_device("dev-001")

    @pytest.mark.asyncio
    async def test_update_device_raises(self, service):
        with pytest.raises(
            NotImplementedError, match="update_device not yet implemented"
        ):
            await service.update_device("dev-001")


# ──────────────────────────── get_info / _get_info_sync tests ──────────────────


class TestGetInfo:
    def test_get_info_sync_success(self, service, mock_sandbox):
        """Test successful get_info."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        result = service._get_info_sync("sb-001")

        assert isinstance(result, SandboxInfo)
        assert result.sandbox_id == "sb-001"
        assert result.status == "READY"
        assert result.ttl_timestamp is not None

    def test_get_info_sync_status_without_value(self, service, mock_sandbox):
        """Status without .value uses str() conversion."""
        info = _make_sandbox_info(status="PENDING")
        mock_sandbox.get_info.return_value = info

        result = service._get_info_sync("sb-001")
        assert result.status == "PENDING"

    def test_get_info_sync_ttl_timestamp_none(self, service, mock_sandbox):
        """When ttl_timestamp is None, result.ttl_timestamp is None."""
        info = _make_sandbox_info(ttl_timestamp=None)
        mock_sandbox.get_info.return_value = info

        result = service._get_info_sync("sb-001")
        assert result.ttl_timestamp is None

    def test_get_info_sync_ttl_timestamp_string(self, service, mock_sandbox):
        """ttl_timestamp as a string is converted via int(float())."""
        info = _make_sandbox_info(ttl_timestamp="1234567890.0")
        mock_sandbox.get_info.return_value = info

        result = service._get_info_sync("sb-001")
        assert result.ttl_timestamp == 1234567890

    def test_get_info_sync_not_found(self, service, mock_plugin):
        """ArcaSandboxNotFoundError is translated to DEVICE_NOT_FOUND."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "not found"
        )
        with pytest.raises(PaasError) as exc_info:
            service._get_info_sync("sb-001")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND
        assert "sb-001" in exc_info.value.message

    def test_get_info_sync_generic_error(self, service, mock_sandbox):
        """Generic exception is translated to DEVICE_UNAVAILABLE."""
        mock_sandbox.get_info.side_effect = RuntimeError("info failed")
        with pytest.raises(PaasError) as exc_info:
            service._get_info_sync("sb-001")
        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_get_info_async(self, service, mock_sandbox):
        """Test the async get_info wrapper."""
        info = _make_sandbox_info()
        mock_sandbox.get_info.return_value = info

        result = await service.get_info("sb-001")
        assert isinstance(result, SandboxInfo)
        assert result.sandbox_id == "sb-001"


# ──────────────────────────── extend_ttl / _extend_ttl_sync tests ──────────────


class TestExtendTtl:
    def test_extend_ttl_sync_success_bool(self, service, mock_sandbox):
        """Test successful TTL extension returning bool."""
        mock_sandbox.extend_ttl.return_value = True

        result = service._extend_ttl_sync("sb-001", 60)
        assert result is True
        mock_sandbox.extend_ttl.assert_called_once_with(60)

    def test_extend_ttl_sync_success_object(self, service, mock_sandbox):
        """extend_ttl returns object with .success attribute."""
        result_obj = MagicMock()
        result_obj.success = True
        mock_sandbox.extend_ttl.return_value = result_obj

        result = service._extend_ttl_sync("sb-001", 60)
        assert result is True

    def test_extend_ttl_sync_returns_false(self, service, mock_sandbox):
        """extend_ttl returns False."""
        mock_sandbox.extend_ttl.return_value = False
        result = service._extend_ttl_sync("sb-001", 60)
        assert result is False

    def test_extend_ttl_sync_not_found(self, service, mock_plugin):
        """ArcaSandboxNotFoundError is translated to DEVICE_NOT_FOUND."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "not found"
        )
        with pytest.raises(PaasError) as exc_info:
            service._extend_ttl_sync("sb-001", 60)
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    def test_extend_ttl_sync_generic_error(self, service, mock_sandbox):
        """Generic exception is translated to DEVICE_UNAVAILABLE."""
        mock_sandbox.extend_ttl.side_effect = RuntimeError("extend failed")
        with pytest.raises(PaasError) as exc_info:
            service._extend_ttl_sync("sb-001", 60)
        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_extend_ttl_async(self, service, mock_sandbox):
        """Test the async extend_ttl wrapper."""
        mock_sandbox.extend_ttl.return_value = True
        result = await service.extend_ttl("sb-001", 60)
        assert result is True


# ──────────────────────────── update_device_ttl / _update_device_ttl_sync tests ─


class TestUpdateDeviceTtl:
    def test_update_device_ttl_sync_success(self, service, mock_sandbox):
        """Test successful TTL update."""
        # Set a future ttl_timestamp so extension is needed
        future_ts = int((datetime.now() + timedelta(hours=1)).timestamp() * 1000)
        info = _make_sandbox_info(ttl_timestamp=future_ts)
        mock_sandbox.get_info.return_value = info
        mock_sandbox.extend_ttl.return_value = True

        result = service._update_device_ttl_sync("dev-001")

        assert result.success is True
        assert result.skipped is False
        assert result.error is None
        assert result.paas_device_id == "dev-001"
        assert result.old_expiration_time is not None
        assert result.new_expiration_time is not None
        # New expiration should be ~24 hours from now
        assert result.new_expiration_time > result.old_expiration_time

    def test_update_device_ttl_sync_with_at_suffix(self, service, mock_sandbox):
        """Test with @template_id suffix in paas_device_id."""
        future_ts = int((datetime.now() + timedelta(hours=1)).timestamp() * 1000)
        info = _make_sandbox_info(ttl_timestamp=future_ts)
        mock_sandbox.get_info.return_value = info
        mock_sandbox.extend_ttl.return_value = True

        result = service._update_device_ttl_sync("dev-001@2")

        assert result.paas_device_id == "dev-001@2"
        # The sandbox_id used for internal calls should have @ stripped
        service._arca_sandbox_plugin.connect_sync_sandbox.assert_called_with("dev-001")

    def test_update_device_ttl_sync_no_ttl_info(self, service, mock_sandbox):
        """When ttl_timestamp is None, return skipped TTLInfo."""
        info = _make_sandbox_info(ttl_timestamp=None)
        mock_sandbox.get_info.return_value = info

        result = service._update_device_ttl_sync("dev-001")

        assert result.success is False
        assert result.skipped is False
        assert result.error == "No TTL info available"
        assert result.old_expiration_time is None
        assert result.new_expiration_time is None

    def test_update_device_ttl_sync_already_at_target(self, service, mock_sandbox):
        """When ttl is already at or past target, return early."""
        # Set a future ttl_timestamp beyond the 24h target so ttl_minutes <= 0
        future_ts = int((datetime.now() + timedelta(hours=48)).timestamp() * 1000)
        info = _make_sandbox_info(ttl_timestamp=future_ts)
        mock_sandbox.get_info.return_value = info

        result = service._update_device_ttl_sync("dev-001")

        assert result.success is False
        assert result.error == "Already at or past target expiration"
        assert result.old_expiration_time is not None
        assert result.new_expiration_time == result.old_expiration_time
        # extend_ttl should NOT have been called
        mock_sandbox.extend_ttl.assert_not_called()

    def test_update_device_ttl_sync_extension_failed(self, service, mock_sandbox):
        """When extend_ttl returns False, TTLInfo has success=False."""
        future_ts = int((datetime.now() + timedelta(hours=1)).timestamp() * 1000)
        info = _make_sandbox_info(ttl_timestamp=future_ts)
        mock_sandbox.get_info.return_value = info
        mock_sandbox.extend_ttl.return_value = False

        result = service._update_device_ttl_sync("dev-001")

        assert result.success is False
        assert result.error == "TTL extension failed"
        assert result.new_expiration_time == result.old_expiration_time

    def test_update_device_ttl_sync_not_found(self, service, mock_plugin):
        """ArcaSandboxNotFoundError in connect is translated to DEVICE_UNAVAILABLE.

        The PaasError(DEVICE_NOT_FOUND) from _get_device_info_sync is caught
        by the generic except in _update_device_ttl_sync and re-translated
        with DEVICE_UNAVAILABLE.
        """
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "not found"
        )
        with pytest.raises(PaasError) as exc_info:
            service._update_device_ttl_sync("dev-001")
        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE

    def test_update_device_ttl_sync_generic_error(self, service, mock_sandbox):
        """Generic exception is translated to DEVICE_UNAVAILABLE."""
        mock_sandbox.get_info.side_effect = RuntimeError("info failed")
        with pytest.raises(PaasError) as exc_info:
            service._update_device_ttl_sync("dev-001")
        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_update_device_ttl_async(self, service, mock_sandbox):
        """Test the async update_device_ttl wrapper."""
        future_ts = int((datetime.now() + timedelta(hours=1)).timestamp() * 1000)
        info = _make_sandbox_info(ttl_timestamp=future_ts)
        mock_sandbox.get_info.return_value = info
        mock_sandbox.extend_ttl.return_value = True

        result = await service.update_device_ttl("dev-001")
        assert result.success is True


# ──────────────────────────── SandboxInfo class tests ──────────────────────────


class TestSandboxInfoClass:
    def test_sandbox_info_init(self):
        si = SandboxInfo(
            sandbox_id="sb-001",
            status="READY",
            ttl_timestamp=1234567890,
        )
        assert si.sandbox_id == "sb-001"
        assert si.status == "READY"
        assert si.ttl_timestamp == 1234567890

    def test_sandbox_info_none_ttl(self):
        si = SandboxInfo(
            sandbox_id="sb-002",
            status="PENDING",
            ttl_timestamp=None,
        )
        assert si.ttl_timestamp is None


# ──────────────────────────── _safe_repr additional tests ──────────────────────


class TestSafeRepr:
    def test_safe_repr_normal(self):
        from secbaas.community.core.service.paas._arca_paas_service import _safe_repr

        assert _safe_repr("hello") == "'hello'"

    def test_safe_repr_long_object_no_truncation(self):
        from secbaas.community.core.service.paas._arca_paas_service import _safe_repr

        result = _safe_repr("short", max_len=4096)
        assert result == "'short'"

    def test_safe_repr_custom_max_len(self):
        from secbaas.community.core.service.paas._arca_paas_service import _safe_repr

        result = _safe_repr("abcdef", max_len=10)
        # repr("abcdef") = "'abcdef'" which is 8 chars, under max_len=10
        assert result == "'abcdef'"


# ──────────────────────────── pull_file_from_url / _pull_file_sync tests ────────


class TestPullFileFromUrl:
    def test_pull_file_sync_success(self, service, mock_sandbox):
        """Successful file pull via curl."""
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = ""
        exec_result.stderr = ""
        mock_sandbox.exec_command.return_value = exec_result

        service._pull_file_sync("dev-001", "http://source/file", "/home/file", 300)

        mock_sandbox.exec_command.assert_called_once()
        cmd = mock_sandbox.exec_command.call_args.kwargs["cmd"]
        assert "curl -fSL --create-dirs" in cmd
        assert "http://source/file" in cmd
        assert "/home/file" in cmd

    def test_pull_file_sync_exit_code_nonzero(self, service, mock_sandbox):
        """Non-zero exit code raises PaasError with FILE_TRANSFER_FAILED."""
        exec_result = MagicMock()
        exec_result.exit_code = 1
        exec_result.stdout = ""
        exec_result.stderr = "curl: (6) Could not resolve host"
        mock_sandbox.exec_command.return_value = exec_result

        with pytest.raises(PaasError) as exc_info:
            service._pull_file_sync("dev-001", "http://source/file", "/home/file", 300)

        assert exc_info.value.code == ErrorCode.FILE_TRANSFER_FAILED
        assert "curl pull failed" in exc_info.value.message

    def test_pull_file_sync_timeout(self, service, mock_sandbox):
        """ArcaSandboxTimeoutError raises PaasError with FILE_TRANSFER_FAILED."""
        from secbaas.community.spi.sandbox.arca import ArcaSandboxTimeoutError

        mock_sandbox.exec_command.side_effect = ArcaSandboxTimeoutError("timed out")

        with pytest.raises(PaasError) as exc_info:
            service._pull_file_sync("dev-001", "http://source/file", "/home/file", 300)

        assert exc_info.value.code == ErrorCode.FILE_TRANSFER_FAILED
        assert "timed out" in exc_info.value.message

    def test_pull_file_sync_generic_exception(self, service, mock_sandbox):
        """Generic exception translated to PaasError."""
        mock_sandbox.exec_command.side_effect = RuntimeError("unexpected error")

        with pytest.raises(PaasError) as exc_info:
            service._pull_file_sync("dev-001", "http://source/file", "/home/file", 300)

        assert exc_info.value.code == ErrorCode.FILE_TRANSFER_FAILED

    @pytest.mark.asyncio
    async def test_pull_file_from_url_async(self, service, mock_sandbox):
        """Async wrapper delegates to _pull_file_sync via to_thread."""
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = ""
        exec_result.stderr = ""
        mock_sandbox.exec_command.return_value = exec_result

        await service.pull_file_from_url(
            "dev-001", "http://source/file", "/home/file", 300
        )

        mock_sandbox.exec_command.assert_called_once()


# ──────────────────────────── push_file_to_url / _push_file_sync tests ─────────


class TestPushFileToUrl:
    def test_push_file_sync_success(self, service, mock_sandbox):
        """Successful file push via curl."""
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = ""
        exec_result.stderr = ""
        mock_sandbox.exec_command.return_value = exec_result

        service._push_file_sync("dev-001", "/home/file", "http://target/url", 300)

        mock_sandbox.exec_command.assert_called_once()
        cmd = mock_sandbox.exec_command.call_args.kwargs["cmd"]
        assert "curl -fSL -X PUT -T" in cmd
        assert "http://target/url" in cmd
        assert "/home/file" in cmd

    def test_push_file_sync_exit_code_nonzero(self, service, mock_sandbox):
        """Non-zero exit code raises PaasError with FILE_TRANSFER_FAILED."""
        exec_result = MagicMock()
        exec_result.exit_code = 2
        exec_result.stdout = ""
        exec_result.stderr = "curl: upload failed"
        mock_sandbox.exec_command.return_value = exec_result

        with pytest.raises(PaasError) as exc_info:
            service._push_file_sync("dev-001", "/home/file", "http://target/url", 300)

        assert exc_info.value.code == ErrorCode.FILE_TRANSFER_FAILED
        assert "curl push failed" in exc_info.value.message

    def test_push_file_sync_timeout(self, service, mock_sandbox):
        """ArcaSandboxTimeoutError raises PaasError."""
        from secbaas.community.spi.sandbox.arca import ArcaSandboxTimeoutError

        mock_sandbox.exec_command.side_effect = ArcaSandboxTimeoutError("timed out")

        with pytest.raises(PaasError) as exc_info:
            service._push_file_sync("dev-001", "/home/file", "http://target/url", 300)

        assert exc_info.value.code == ErrorCode.FILE_TRANSFER_FAILED
        assert "timed out" in exc_info.value.message

    def test_push_file_sync_generic_exception(self, service, mock_sandbox):
        """Generic exception translated to PaasError."""
        mock_sandbox.exec_command.side_effect = RuntimeError("unexpected")

        with pytest.raises(PaasError) as exc_info:
            service._push_file_sync("dev-001", "/home/file", "http://target/url", 300)

        assert exc_info.value.code == ErrorCode.FILE_TRANSFER_FAILED

    @pytest.mark.asyncio
    async def test_push_file_to_url_async(self, service, mock_sandbox):
        """Async wrapper delegates to _push_file_sync via to_thread."""
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = ""
        exec_result.stderr = ""
        mock_sandbox.exec_command.return_value = exec_result

        await service.push_file_to_url(
            "dev-001", "/home/file", "http://target/url", 300
        )

        mock_sandbox.exec_command.assert_called_once()

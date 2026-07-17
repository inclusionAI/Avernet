"""Tests for agentclaw.community.core.devices.services.device_service.DeviceService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest

from agentclaw.community.core.devices.services.device_service import (
    DeviceService,
    LOCAL_DEVICE_PROVIDER,
    ARCA_DEVICE_PROVIDER,
)
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceBindingStatus,
    DeviceConnectionInfo,
    OperatorContext,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.errors import (
    DeviceNotFoundError,
    InvalidDeviceStatusError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    id: int = 1,
    entity_id: str = "u001",
    entity_type: str = "staff",
    device_id: str = "staff_u001_default",
    device_provider: str = LOCAL_DEVICE_PROVIDER,
    status: str = DeviceBindingStatus.ACTIVE.value,
    device_props: dict | None = None,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=id,
        entity_id=entity_id,
        entity_type=entity_type,
        device_id=device_id,
        device_provider=device_provider,
        env="dev",
        device_props=device_props if device_props is not None else {"callback_token": "tok123"},
        status=status,
        apply_reason=None,
        applied_by=entity_id,
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


def _make_operator(staff_id: str = "u001") -> OperatorContext:
    return OperatorContext(
        staff_id=staff_id,
        staff=staff_id,
        nick_name="User",
        operator_name="User",
        tenant_id="default",
    )


def _make_sandbox_client() -> MagicMock:
    """A SandboxRuntimeClient double for the ARCA-proxy branch of
    get_device_connection_v2 (proxy base/target)."""
    client = MagicMock()
    client.proxy_base_url.return_value = "https://proxy.example.com"
    client.proxy_target.side_effect = lambda sid, **kw: f"ARCA_{sid}:20003"
    return client


def _make_service(
    repo=None,
    bot_query=None,
    bot_sync=None,
    sandbox_client=None,
    task_queue_service=None,
    oss_record_repo=None,
) -> DeviceService:
    repo = repo or MagicMock()
    bot_query = bot_query or MagicMock()
    bot_sync = bot_sync or MagicMock()
    return DeviceService(
        repository=repo,
        bot_query=bot_query,
        bot_sync=bot_sync,
        oss_record_repo=oss_record_repo or MagicMock(),
        mcp_sync=MagicMock(),
        sandbox_client=sandbox_client or _make_sandbox_client(),
        task_queue_service=task_queue_service,
    )


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


class TestSafeB64Decode:
    def test_normal_padding(self):
        import base64
        data = base64.b64encode(b"hello world").decode()
        assert DeviceService.safe_b64decode(data) == b"hello world"

    def test_missing_padding_1(self):
        import base64
        raw = b"ab"
        encoded = base64.b64encode(raw).decode().rstrip("=")
        # remove padding to simulate missing padding
        result = DeviceService.safe_b64decode(encoded)
        assert result == raw

    def test_missing_padding_2(self):
        import base64
        raw = b"abc"
        encoded = base64.b64encode(raw).decode().rstrip("=")
        result = DeviceService.safe_b64decode(encoded)
        assert result == raw


class TestGenerateCallbackToken:
    def test_returns_non_empty_string(self):
        token = DeviceService._generate_callback_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_tokens_are_unique(self):
        tokens = {DeviceService._generate_callback_token() for _ in range(20)}
        assert len(tokens) == 20


# ---------------------------------------------------------------------------
# _get_collaborator_service
# ---------------------------------------------------------------------------


class TestGetCollaboratorService:
    def test_resolves_from_injector(self):
        """Happy path: get_app_injector returns injector which resolves CollaboratorService."""
        svc = _make_service()
        sentinel = object()
        injector = MagicMock()
        injector.get.return_value = sentinel

        with patch("agentclaw.community.di.get_app_injector", return_value=injector):
            result = svc._get_collaborator_service()

        assert result is sentinel
        injector.get.assert_called_once()

    def test_returns_none_when_injector_not_initialized(self):
        """When get_app_injector raises RuntimeError (not initialized), returns None."""
        svc = _make_service()
        with patch(
            "agentclaw.community.di.get_app_injector",
            side_effect=RuntimeError("DI injector not initialized"),
        ):
            result = svc._get_collaborator_service()

        assert result is None

    def test_returns_none_when_service_resolution_fails(self):
        """When injector.get() fails, returns None gracefully."""
        svc = _make_service()
        injector = MagicMock()
        injector.get.side_effect = Exception("service not registered")

        with patch("agentclaw.community.di.get_app_injector", return_value=injector):
            result = svc._get_collaborator_service()

        assert result is None


# ---------------------------------------------------------------------------
# _generate_device_id
# ---------------------------------------------------------------------------


class TestGenerateDeviceId:
    def test_first_device_uses_default_bolt_id(self):
        svc = _make_service()
        device_id, bolt_id = svc._generate_device_id(
            entity_id="u001", entity_type="staff", is_first=True
        )
        assert bolt_id == "default"
        assert device_id == "staff_u001_default"

    def test_subsequent_device_has_timestamp_suffix(self):
        svc = _make_service()
        device_id, bolt_id = svc._generate_device_id(
            entity_id="u001", entity_type="staff", is_first=False
        )
        assert bolt_id != "default"
        assert "_" in bolt_id
        assert "staff_u001_" in device_id


# ---------------------------------------------------------------------------
# _validate_and_generate_device_id
# ---------------------------------------------------------------------------


class TestValidateAndGenerateDeviceId:
    def test_no_existing_device_no_released(self):
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = None
        svc = _make_service(repo=repo)

        device_id, bot_id, released = svc._validate_and_generate_device_id(
            entity_id="u001", entity_type="staff", env="dev", bot_id="mybot"
        )
        assert bot_id == "mybot"
        assert released is None
        assert "staff_u001_mybot_" in device_id

    def test_released_binding_found_returns_it(self):
        released = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = released
        svc = _make_service(repo=repo)

        _, _, result = svc._validate_and_generate_device_id(
            entity_id="u001", entity_type="staff", env="dev"
        )
        assert result is released

    def test_existing_device_generates_new_id(self):
        repo = MagicMock()
        repo.exists_device_id.return_value = True
        repo.get_released_binding.return_value = None
        svc = _make_service(repo=repo)

        device_id, _, released = svc._validate_and_generate_device_id(
            entity_id="u001", entity_type="staff", env="dev"
        )
        # Two calls to get_released_binding (once for original id, once for new)
        assert repo.get_released_binding.call_count == 2
        assert released is None


# ---------------------------------------------------------------------------
# _resolve_env_dir / _resolve_entity_dir
# ---------------------------------------------------------------------------


class TestResolveDirs:
    def test_resolve_env_dir_prod(self):
        svc = _make_service()
        assert svc._resolve_env_dir("prod") == "aidesktop_prod"

    def test_resolve_env_dir_pre(self):
        svc = _make_service()
        assert svc._resolve_env_dir("pre") == "aidesktop_pre"

    def test_resolve_env_dir_dev(self):
        svc = _make_service()
        assert svc._resolve_env_dir("dev") == "aidesktop_dev"

    def test_resolve_entity_dir(self):
        svc = _make_service()
        assert svc._resolve_entity_dir("u001", "staff") == "staff_u001"


# ---------------------------------------------------------------------------
# _resolve_data_dir
# ---------------------------------------------------------------------------


class TestResolveDataDir:
    def test_returns_correct_paths(self):
        svc = _make_service()
        data_dir, conf_dir = svc._resolve_data_dir(
            aidesktop_root="/aidesktop",
            entity_id="u001",
            entity_type="staff",
            bot_id="default",
            engine="openclaw",
            env="dev",
        )
        assert str(data_dir).endswith("openclaw")
        assert str(conf_dir).endswith("openclaw_conf")
        assert "bolt_data" in str(data_dir)


# ---------------------------------------------------------------------------
# get_device / get_device_by_device_id
# ---------------------------------------------------------------------------


class TestGetDevice:
    def test_found(self):
        record = _make_record()
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        result = svc.get_device(binding_id=1)
        assert result is record

    def test_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.get_device(binding_id=99)


class TestGetDeviceByDeviceId:
    def test_found(self):
        record = _make_record()
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)

        result = svc.get_device_by_device_id(device_id="staff_u001_default")
        assert result is record

    def test_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_device_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.get_device_by_device_id(device_id="nonexistent")


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


class TestListDevices:
    def test_delegates_to_repo(self):
        record = _make_record()
        repo = MagicMock()
        repo.list_bindings.return_value = (1, [record])
        svc = _make_service(repo=repo)

        total, items = svc.list_devices(
            entity_id="u001", entity_type="staff", env="dev", status="ACTIVE"
        )
        assert total == 1
        assert items == [record]
        repo.list_bindings.assert_called_once_with(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            status="ACTIVE",
            page=1,
            page_size=20,
        )

    def test_falls_back_to_current_env_when_env_is_none(self):
        """当 env=None 时,service 应该 fallback 到 get_current_env() 并透传给 repo。"""
        record = _make_record()
        repo = MagicMock()
        repo.list_bindings.return_value = (1, [record])
        svc = _make_service(repo=repo)

        with patch(
            "agentclaw.community.utils.env_utils.get_current_env", return_value="pre"
        ) as mock_get_env:
            total, items = svc.list_devices(
                entity_id="u001", entity_type="staff", env=None, status="ACTIVE"
            )

        assert total == 1
        assert items == [record]
        mock_get_env.assert_called_once()
        repo.list_bindings.assert_called_once_with(
            entity_id="u001",
            entity_type="staff",
            env="pre",
            status="ACTIVE",
            page=1,
            page_size=20,
        )


# ---------------------------------------------------------------------------
# release_device
# ---------------------------------------------------------------------------


class TestReleaseDevice:
    def test_release_active_device(self):
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)

        result = svc.release_device(
            binding_id=1,
            release_reason="testing",
            operator=_make_operator(),
        )

        assert result is released_record
        repo.release_binding.assert_called_once()

    def test_release_pending_device(self):
        record = _make_record(status=DeviceBindingStatus.PENDING.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)

        result = svc.release_device(
            binding_id=1,
            release_reason="testing",
            operator=_make_operator(),
        )
        assert result is released_record

    def test_release_failed_device(self):
        record = _make_record(status=DeviceBindingStatus.FAILED.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)

        result = svc.release_device(
            binding_id=1,
            release_reason=None,
            operator=_make_operator(),
        )
        assert result is released_record

    def test_release_stopped_device(self):
        record = _make_record(status=DeviceBindingStatus.STOPPED.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)

        result = svc.release_device(
            binding_id=1,
            release_reason="finalize stopped binding",
            operator=_make_operator(),
        )

        assert result is released_record
        repo.release_binding.assert_called_once()

    def test_release_released_device_raises(self):
        record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError):
            svc.release_device(
                binding_id=1,
                release_reason=None,
                operator=_make_operator(),
            )

    def test_release_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.release_device(
                binding_id=99,
                release_reason=None,
                operator=_make_operator(),
            )

    def test_physical_release_failure_still_updates_db(self):
        """Physical release error must not prevent DB status update."""
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)

        # Make _do_release raise
        svc._do_release = MagicMock(side_effect=RuntimeError("device unreachable"))

        result = svc.release_device(
            binding_id=1,
            release_reason="test",
            operator=_make_operator(),
        )
        # DB release still called despite physical failure
        repo.release_binding.assert_called_once()
        # Reason includes failure note
        call_kwargs = repo.release_binding.call_args.kwargs
        assert "physical release failed" in call_kwargs["release_reason"]
        assert result is released_record

    def test_reset_flag_triggers_config_reset(self):
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)
        svc._reset_openclaw_config = MagicMock()

        svc.release_device(
            binding_id=1,
            release_reason=None,
            reset=True,
            operator=_make_operator(),
        )
        svc._reset_openclaw_config.assert_called_once()

    def test_reset_failure_does_not_raise(self):
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        released_record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_id.side_effect = [record, released_record]
        svc = _make_service(repo=repo)
        svc._reset_openclaw_config = MagicMock(side_effect=RuntimeError("fail"))

        # Should not raise
        result = svc.release_device(
            binding_id=1,
            release_reason=None,
            reset=True,
            operator=_make_operator(),
        )
        assert result is released_record


# ---------------------------------------------------------------------------
# get_device_connection
# ---------------------------------------------------------------------------


class TestGetDeviceConnection:
    def test_connection_for_own_device(self):
        record = _make_record(entity_id="u001", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        conn = svc.get_device_connection(
            binding_id=1,
            operator=_make_operator("u001"),
        )
        assert isinstance(conn, DeviceConnectionInfo)

    def test_failed_device_raises(self):
        record = _make_record(status=DeviceBindingStatus.FAILED.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("u001"),
            )

    def test_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.get_device_connection(
                binding_id=99,
                operator=_make_operator("u001"),
            )

    def test_other_user_public_bot_allowed(self):
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {"public": "1"}
        svc = _make_service(repo=repo, bot_query=bot_query)

        conn = svc.get_device_connection(
            binding_id=1,
            operator=_make_operator("requester"),
        )
        assert isinstance(conn, DeviceConnectionInfo)

    def test_other_user_non_public_bot_denied(self):
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {"public": "0"}
        svc = _make_service(repo=repo, bot_query=bot_query)

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("requester"),
            )

    def test_other_user_bot_query_exception_denied(self):
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.side_effect = RuntimeError("db error")
        svc = _make_service(repo=repo, bot_query=bot_query)

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("requester"),
            )

    def test_other_user_collaborator_allowed(self):
        """协作者可以获取设备连接信息。"""
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "public": "0",
            "bot_id": "test_bot_id",
            "owner_id": "bot_owner",
        }

        # Mock collaborator service
        mock_collaborator_service = MagicMock()
        mock_collaborator_service.check_collaborator_permission.return_value = {
            "has_permission": True,
            "level": "MEMBER",
            "level_value": 1,
        }

        svc = _make_service(repo=repo, bot_query=bot_query)
        # Mock _get_collaborator_service to return our mock
        svc._get_collaborator_service = lambda: mock_collaborator_service

        conn = svc.get_device_connection(
            binding_id=1,
            operator=_make_operator("collaborator_user"),
        )
        assert isinstance(conn, DeviceConnectionInfo)
        mock_collaborator_service.check_collaborator_permission.assert_called_once_with(
            bot_id="test_bot_id",
            owner_id="bot_owner",
            user_id="collaborator_user",
            required_level=1,  # PermissionLevel.MEMBER.value
        )

    def test_collaborator_permission_check_failed_still_denied(self):
        """协作者权限检查失败（返回无权限）仍然拒绝访问。"""
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "public": "0",
            "bot_id": "test_bot_id",
            "owner_id": "bot_owner",
        }

        # Mock collaborator service returning no permission
        mock_collaborator_service = MagicMock()
        mock_collaborator_service.check_collaborator_permission.return_value = {
            "has_permission": False,
            "level": "NONE",
            "level_value": 0,
        }

        svc = _make_service(repo=repo, bot_query=bot_query)
        svc._get_collaborator_service = lambda: mock_collaborator_service

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("stranger"),
            )

    def test_bot_not_found_raises_error(self):
        """Bot 不存在时抛出异常。"""
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None  # Bot 不存在

        svc = _make_service(repo=repo, bot_query=bot_query)

        with pytest.raises(InvalidDeviceStatusError, match="Bot 信息不存在"):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("other_user"),
            )

    def test_bot_info_incomplete_raises_error(self):
        """Bot 信息不完整（缺少 bot_id 或 owner_id）时抛出异常。"""
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "public": "0",
            "bot_id": None,  # bot_id 为空
            "owner_id": "bot_owner",
        }

        svc = _make_service(repo=repo, bot_query=bot_query)

        with pytest.raises(InvalidDeviceStatusError, match="Bot 信息不完整"):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("other_user"),
            )

    def test_collaborator_service_unavailable_raises_error(self):
        """协作者服务不可用时抛出异常。"""
        record = _make_record(entity_id="bot_owner", status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "public": "0",
            "bot_id": "test_bot_id",
            "owner_id": "bot_owner",
        }

        svc = _make_service(repo=repo, bot_query=bot_query)
        svc._get_collaborator_service = lambda: None  # 协作者服务不可用

        with pytest.raises(InvalidDeviceStatusError, match="协作者服务不可用"):
            svc.get_device_connection(
                binding_id=1,
                operator=_make_operator("other_user"),
            )


# ---------------------------------------------------------------------------
# report_device_alive
# ---------------------------------------------------------------------------


class TestReportDeviceAlive:
    def test_pending_transitions_to_active(self):
        record = _make_record(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"callback_token": "tok123"},
        )
        updated = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None
        svc = _make_service(repo=repo, bot_query=bot_query)

        result = svc.report_device_alive(device_id="staff_u001_default", token="tok123")
        assert result is updated
        repo.update_status_and_alive_at.assert_called_once_with(
            binding_id=1, status=DeviceBindingStatus.ACTIVE.value
        )

    def test_active_updates_alive_at_only(self):
        record = _make_record(
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"callback_token": "tok123"},
        )
        updated = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        svc = _make_service(repo=repo)

        result = svc.report_device_alive(device_id="staff_u001_default", token="tok123")
        assert result is updated
        # Status stays ACTIVE — still same value passed
        repo.update_status_and_alive_at.assert_called_once_with(
            binding_id=1, status=DeviceBindingStatus.ACTIVE.value
        )

    def test_invalid_token_raises(self):
        record = _make_record(device_props={"callback_token": "correct_token"})
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError, match="invalid token"):
            svc.report_device_alive(device_id="staff_u001_default", token="wrong_token")

    def test_released_device_raises(self):
        record = _make_record(
            status=DeviceBindingStatus.RELEASED.value,
            device_props={"callback_token": "tok123"},
        )
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError):
            svc.report_device_alive(device_id="staff_u001_default", token="tok123")

    def test_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_device_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.report_device_alive(device_id="nonexistent", token="tok")

    def test_publishes_device_activated_event_on_pending_to_active(self):
        from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
        from agentclaw.community.core.events.types import DeviceActivatedEvent

        reset_event_bus()
        received: list[DeviceActivatedEvent] = []
        get_event_bus().subscribe(DeviceActivatedEvent, received.append)

        record = _make_record(
            status=DeviceBindingStatus.PENDING.value,
            device_provider="arca",
            device_props={
                "callback_token": "tok123",
                "sandbox_id": "sbx-abc@alt-0",
            },
        )
        updated = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None
        svc = _make_service(repo=repo, bot_query=bot_query)

        svc.report_device_alive(device_id="staff_u001_default", token="tok123")

        assert len(received) == 1
        event = received[0]
        assert event.device_id == "staff_u001_default"
        assert event.binding_id == record.id
        assert event.entity_id == record.entity_id
        assert event.entity_type == record.entity_type
        assert event.device_provider == "arca"
        assert event.sandbox_id == "sbx-abc@alt-0"
        reset_event_bus()

    def test_no_event_published_on_alive_refresh_when_already_active(self):
        from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
        from agentclaw.community.core.events.types import DeviceActivatedEvent

        reset_event_bus()
        received: list[DeviceActivatedEvent] = []
        get_event_bus().subscribe(DeviceActivatedEvent, received.append)

        record = _make_record(
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"callback_token": "tok123"},
        )
        updated = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        svc = _make_service(repo=repo)

        svc.report_device_alive(device_id="staff_u001_default", token="tok123")

        assert received == []
        reset_event_bus()

    def test_event_publish_failure_does_not_break_report_alive(self):
        from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
        from agentclaw.community.core.events.types import DeviceActivatedEvent

        reset_event_bus()

        def bad_handler(_event):
            raise RuntimeError("listener exploded")

        get_event_bus().subscribe(DeviceActivatedEvent, bad_handler)

        record = _make_record(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"callback_token": "tok123", "sandbox_id": "sbx-1"},
        )
        updated = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None
        svc = _make_service(repo=repo, bot_query=bot_query)

        # Should not raise despite the faulty handler
        result = svc.report_device_alive(device_id="staff_u001_default", token="tok123")
        assert result is updated
        reset_event_bus()


# ---------------------------------------------------------------------------
# report_device_status
# ---------------------------------------------------------------------------


class TestReportDeviceStatus:
    def test_starting_status(self):
        record = _make_record(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"callback_token": "tok"},
        )
        updated = _make_record(status=DeviceBindingStatus.PENDING.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        svc = _make_service(repo=repo)

        result = svc.report_device_status(
            device_id="staff_u001_default",
            status="STARTING",
            message="starting up",
            token="tok",
        )
        assert result is updated
        # STARTING status: update_bot_start_status is called, but NOT update_status
        repo.update_status.assert_not_called()

    def test_failed_status_updates_device_and_bot(self):
        record = _make_record(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"callback_token": "tok"},
        )
        updated = _make_record(status=DeviceBindingStatus.FAILED.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        repo.get_by_id.return_value = updated
        svc = _make_service(repo=repo)

        result = svc.report_device_status(
            device_id="staff_u001_default",
            status="FAILED",
            message="boot error",
            token="tok",
        )
        assert result is updated
        repo.update_status.assert_called_once_with(
            binding_id=1, status=DeviceBindingStatus.FAILED.value
        )
        repo.update_bot_status_on_device_failed.assert_called_once_with(binding_id=1)

    def test_invalid_token_raises(self):
        record = _make_record(device_props={"callback_token": "right"})
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError, match="invalid token"):
            svc.report_device_status(
                device_id="staff_u001_default", status="STARTING", message=None, token="wrong"
            )

    def test_released_device_raises(self):
        record = _make_record(
            status=DeviceBindingStatus.RELEASED.value,
            device_props={"callback_token": "tok"},
        )
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError):
            svc.report_device_status(
                device_id="x", status="STARTING", message=None, token="tok"
            )

    def test_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_device_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.report_device_status(
                device_id="x", status="STARTING", message=None, token="tok"
            )


# ---------------------------------------------------------------------------
# exec_shell
# ---------------------------------------------------------------------------


class TestExecShell:
    def test_executes_on_active_device(self):
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)
        svc._exec_shell = MagicMock(return_value="output")

        result = svc.exec_shell("staff_u001_default", "ls -la")
        assert result == "output"

    def test_not_active_raises(self):
        record = _make_record(status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError):
            svc.exec_shell("staff_u001_default", "ls")

    def test_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_device_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(DeviceNotFoundError):
            svc.exec_shell("nonexistent", "ls")


# ---------------------------------------------------------------------------
# batch_set_env
# ---------------------------------------------------------------------------


class TestBatchSetEnv:
    def test_batch_update(self):
        from dataclasses import replace as dc_replace
        repo = MagicMock()
        repo.batch_update_env.return_value = 2

        r1_base = _make_record(id=1)
        r2_base = _make_record(id=2)
        # Simulate r1 updated to "pre", r2 not
        r1_updated = dc_replace(r1_base, env="pre")
        r2_unchanged = dc_replace(r2_base, env="dev")
        repo.get_by_ids.return_value = [r1_updated, r2_unchanged]
        svc = _make_service(repo=repo)

        count, updated_ids = svc.batch_set_env(binding_ids=[1, 2], env="pre")
        assert count == 2
        assert 1 in updated_ids
        assert 2 not in updated_ids


# ---------------------------------------------------------------------------
# list_connectable_devices
# ---------------------------------------------------------------------------


class TestListConnectableDevices:
    def test_without_connection(self):
        record = _make_record(id=1, status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_ids.return_value = [record]
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [
            {"bot_id": "b1", "binding_id": 1, "owner_id": "u001"}
        ]
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=False,
        )
        assert total == 1
        assert items[0].record is record
        assert items[0].connection is None

    def test_with_connection_success(self):
        record = _make_record(
            id=1,
            entity_id="u001",
            status=DeviceBindingStatus.ACTIVE.value,
        )
        repo = MagicMock()
        repo.get_by_ids.return_value = [record]
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [
            {"bot_id": "b1", "binding_id": 1, "owner_id": "u001"}
        ]
        svc = _make_service(repo=repo, bot_query=bot_query)
        # Return a valid conn from _compose_device_conn_info
        fake_conn = DeviceConnectionInfo(type="local", target="localhost:20003", token="", engine_type="openclaw")
        svc._compose_device_conn_info = MagicMock(return_value=fake_conn)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        assert items[0].connection is not None

    def test_with_connection_error_yields_none(self):
        record = _make_record(
            id=1,
            entity_id="u001",
            status=DeviceBindingStatus.ACTIVE.value,
        )
        repo = MagicMock()
        repo.get_by_ids.return_value = [record]
        repo.get_by_id.return_value = None  # causes DeviceNotFoundError inside get_device_connection
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [
            {"bot_id": "b1", "binding_id": 1, "owner_id": "u001"}
        ]
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        assert items[0].connection is None

    def test_empty_entity_id_returns_empty(self):
        """当 entity_id 为空时返回空结果."""
        repo = MagicMock()
        bot_query = MagicMock()
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id=None,
            entity_type="staff",
            env="dev",
            with_connection=False,
        )
        assert total == 0
        assert items == []
        # 不应该调用 bot_query 或 repo
        bot_query.list_active_bots_by_entity.assert_not_called()
        repo.get_by_ids.assert_not_called()

    def test_no_personal_bots_returns_empty(self):
        """当没有个人 bot 时返回空结果."""
        repo = MagicMock()
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = []  # 没有 bot
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=False,
        )
        assert total == 0
        assert items == []
        # 不应该调用 repo.get_by_ids
        repo.get_by_ids.assert_not_called()

    def test_filters_by_env(self):
        """测试 env 过滤."""
        from dataclasses import replace as dc_replace
        record1 = _make_record(id=1, status=DeviceBindingStatus.ACTIVE.value)
        record2 = dc_replace(_make_record(id=2, status=DeviceBindingStatus.ACTIVE.value), env="prod")
        repo = MagicMock()
        repo.get_by_ids.return_value = [record1, record2]
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [
            {"bot_id": "b1", "binding_id": 1, "owner_id": "u001"},
            {"bot_id": "b2", "binding_id": 2, "owner_id": "u001"},
        ]
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",  # 只获取 dev 环境
            with_connection=False,
        )
        assert total == 1
        assert items[0].record.id == 1

    def test_filters_non_active_bindings(self):
        """只返回 ACTIVE 状态的绑定."""
        record_active = _make_record(id=1, status=DeviceBindingStatus.ACTIVE.value)
        record_pending = _make_record(id=2, status=DeviceBindingStatus.PENDING.value)
        record_released = _make_record(id=3, status=DeviceBindingStatus.RELEASED.value)
        repo = MagicMock()
        repo.get_by_ids.return_value = [record_active, record_pending, record_released]
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [
            {"bot_id": "b1", "binding_id": 1, "owner_id": "u001"},
            {"bot_id": "b2", "binding_id": 2, "owner_id": "u001"},
            {"bot_id": "b3", "binding_id": 3, "owner_id": "u001"},
        ]
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env=None,
            with_connection=False,
        )
        assert total == 1
        assert items[0].record.id == 1

    def test_pagination(self):
        """测试分页."""
        records = [_make_record(id=i, status=DeviceBindingStatus.ACTIVE.value) for i in range(1, 6)]
        repo = MagicMock()
        repo.get_by_ids.return_value = records
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [
            {"bot_id": f"b{i}", "binding_id": i, "owner_id": "u001"} for i in range(1, 6)
        ]
        svc = _make_service(repo=repo, bot_query=bot_query)

        # 第一页，每页 2 条
        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env=None,
            page=1,
            page_size=2,
            with_connection=False,
        )
        assert total == 5
        assert len(items) == 2
        assert items[0].record.id == 1
        assert items[1].record.id == 2

        # 第二页
        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env=None,
            page=2,
            page_size=2,
            with_connection=False,
        )
        assert total == 5
        assert len(items) == 2
        assert items[0].record.id == 3
        assert items[1].record.id == 4

    def test_calls_bot_query_with_personal_type(self):
        """验证 bot_query 使用 bot_type=personal 参数."""
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = []
        svc = _make_service(bot_query=bot_query)

        svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
        )
        bot_query.list_active_bots_by_entity.assert_called_once_with(
            entity_id="u001",
            entity_type="staff",
            bot_type="personal",
        )


# ---------------------------------------------------------------------------
# apply_device
# ---------------------------------------------------------------------------


class TestApplyDevice:
    class HookClaimingDeviceService(DeviceService):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.after_binding_args = None
            self.start_called = False

        def _do_allocate(self, **kwargs):
            return AllocatedDevice(
                device_id="hook-device-1",
                device_provider="hook-provider",
                device_props={"provider_key": "provider_value"},
            )

        def _after_binding_persisted(self, **kwargs):
            self.after_binding_args = kwargs
            return True

        def _start_service(self, *args, **kwargs):
            self.start_called = True
            return True, "started"

    class DefaultHookDeviceService(HookClaimingDeviceService):
        def _after_binding_persisted(self, **kwargs):
            self.after_binding_args = kwargs
            return False

    def test_apply_device_skips_generic_start_when_provider_hook_claims_lifecycle(self):
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = 42
        record = _make_record(id=42, status=DeviceBindingStatus.PENDING.value)
        repo.get_by_id.return_value = record
        svc = self.HookClaimingDeviceService(
            repository=repo,
            bot_query=MagicMock(),
            bot_sync=MagicMock(),
            oss_record_repo=MagicMock(),
            mcp_sync=MagicMock(),
        )
        svc._setup_directory = MagicMock(return_value=[])

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            with patch("agentclaw.community.core.devices.services.device_service.threading.Thread") as thread_cls:
                result = svc.apply_device(
                    apply_reason="create bot",
                    entity_id="u001",
                    entity_type="staff",
                    operator=_make_operator(),
                    bot_id="bot1",
                    owner_id="owner-001",
                )

        assert result is record
        assert svc.after_binding_args["binding_id"] == record.id
        assert svc.after_binding_args["bot_id"] == "bot1"
        assert svc.after_binding_args["owner_id"] == "owner-001"
        assert svc.after_binding_args["device_props"]["provider_key"] == "provider_value"
        assert svc.start_called is False
        thread_cls.assert_not_called()

    def test_apply_device_runs_generic_start_when_provider_hook_does_not_claim_lifecycle(self):
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = 42
        record = _make_record(id=42, status=DeviceBindingStatus.PENDING.value)
        repo.get_by_id.return_value = record
        svc = self.DefaultHookDeviceService(
            repository=repo,
            bot_query=MagicMock(),
            bot_sync=MagicMock(),
            oss_record_repo=MagicMock(),
            mcp_sync=MagicMock(),
        )
        svc._setup_directory = MagicMock(return_value=[])

        thread = MagicMock()

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            with patch(
                "agentclaw.community.core.devices.services.device_service.threading.Thread",
                return_value=thread,
            ) as thread_cls:
                result = svc.apply_device(
                    apply_reason="test",
                    entity_id="u001",
                    entity_type="staff",
                    operator=_make_operator(),
                    bot_id="bot1",
                    owner_id="u001",
                )

        assert result is record
        assert svc.after_binding_args["binding_id"] == record.id
        thread_cls.assert_called_once()
        thread.start.assert_called_once()
        start_target = thread_cls.call_args.kwargs["target"]
        assert callable(start_target)
        assert svc.start_called is False

        start_target()

        assert svc.start_called is True

    def test_apply_new_device(self):
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = 42
        record = _make_record(id=42)
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            result = svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot1",
            )

        assert result is record
        repo.insert_binding.assert_called_once()

    def test_apply_reuses_released_binding(self):
        released = _make_record(id=5, status=DeviceBindingStatus.RELEASED.value)
        record = _make_record(id=5, status=DeviceBindingStatus.PENDING.value)
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = released
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            result = svc.apply_device(
                apply_reason="reuse test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
            )

        repo.reuse_binding.assert_called_once()
        repo.insert_binding.assert_not_called()
        assert result is record

    def test_apply_returns_none_when_record_not_found(self):
        repo = MagicMock()
        repo.exists_device_id.return_value = False
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = 99
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            result = svc.apply_device(
                apply_reason=None,
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
            )

        assert result is None


# ---------------------------------------------------------------------------
# get_device_connection_v2
# ---------------------------------------------------------------------------


class TestGetDeviceConnectionV2:
    def test_local_connection_uses_baas_invoke_http(self):
        record = _make_record(
            entity_id="u001",
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"callback_token": "tok"},
        )
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)
        # Local type now routes through BaaS invoke-http (same as desktop)
        fake_conn = DeviceConnectionInfo(
            type="local", target="10.0.0.1:20003", token="mytoken",
            engine_type="openclaw",
            baas_base_url="http://baas.test",
            bot_uuid="bot-001",
            tenant="team_claw",
            engine_port=20003,
        )
        svc._compose_device_conn_info = MagicMock(return_value=fake_conn)

        result = svc.get_device_connection_v2(
            user_id="u001", nick_name="User", binding_id=1
        )
        assert result["use_proxy"] is True
        assert result["url"] == (
            "http://baas.test/api/v1/bots/team_claw/bot-001/invoke-http/20003"
        )
        assert result["headers"] == {"x-proxypass-token": "mytoken"}
        assert result["bot_uuid"] == "bot-001"

    def test_proxy_connection_with_sandbox_id(self):
        record = _make_record(
            entity_id="u001",
            device_provider=ARCA_DEVICE_PROVIDER,
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"callback_token": "tok", "sandbox_id": "sb-abc123"},
        )
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)
        fake_conn = DeviceConnectionInfo(
            type="arca", target="sb-abc123.proxy.net", token="mytoken", engine_type="openclaw"
        )
        svc._compose_device_conn_info = MagicMock(return_value=fake_conn)

        # The ARCA proxy target is served by the mocked SandboxRuntimeClient seam
        # (``_make_sandbox_client``), not corp ``arca_io`` (B6 devendoring), so this
        # is corp-free — only the community proxy-base config is patched.
        with patch("agentclaw.community.core.config.sofa.sofa_config.user_config", {"agentclawproxy": {"base_url": "https://proxy.example.com"}}):
            result = svc.get_device_connection_v2(
                user_id="u001", nick_name="User", binding_id=1
            )

        assert result["use_proxy"] is True
        assert "proxy" in result["url"].lower() or "proxypass" in result["url"]
        assert result["sandbox_id"] == "sb-abc123"

    def test_raises_device_service_error_on_failure(self):
        repo = MagicMock()
        repo.get_by_id.side_effect = RuntimeError("db failure")
        svc = _make_service(repo=repo)

        from agentclaw.community.core.devices.errors import DeviceServiceError
        with pytest.raises(DeviceServiceError):
            svc.get_device_connection_v2(user_id="u001", nick_name="User", binding_id=1)

    def test_proxy_connection_with_baas_type(self):
        """BaaS 设备使用代理，target 已是 ARCA 格式，直接使用。"""
        record = _make_record(
            entity_id="u001",
            device_provider=ARCA_DEVICE_PROVIDER,
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"callback_token": "tok"},
        )
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)
        # BaaS 设备返回的 target 已经是 ARCA 格式
        fake_conn = DeviceConnectionInfo(
            type="baas",
            target="ARCA_abc123@0:20003",
            token="baas_token",
            engine_type="openclaw",
        )
        svc._compose_device_conn_info = MagicMock(return_value=fake_conn)

        result = svc.get_device_connection_v2(
            user_id="u001", nick_name="User", binding_id=1
        )

        assert result["use_proxy"] is True
        assert "proxypass" in result["url"]
        assert "ARCA_abc123" in result["url"]
        assert result["target"] == "ARCA_abc123@0:20003"
        assert result["type"] == "baas"

    def test_proxy_connection_with_arca_target_prefix(self):
        """target 以 ARCA_ 开头时，即使没有 sandbox_id 也使用代理。"""
        record = _make_record(
            entity_id="u001",
            device_provider=ARCA_DEVICE_PROVIDER,
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"callback_token": "tok"},
        )
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)
        # 设备 target 以 ARCA_ 开头
        fake_conn = DeviceConnectionInfo(
            type="remote",
            target="ARCA_xyz789@1:20003",
            token="arca_token",
            engine_type="openclaw",
        )
        svc._compose_device_conn_info = MagicMock(return_value=fake_conn)

        result = svc.get_device_connection_v2(
            user_id="u001", nick_name="User", binding_id=1
        )

        assert result["use_proxy"] is True
        assert "proxypass" in result["url"]
        assert "ARCA_xyz789" in result["url"]
        assert result["target"] == "ARCA_xyz789@1:20003"


# ---------------------------------------------------------------------------
# _sync_bot_config_when_device_active
# ---------------------------------------------------------------------------


class TestSyncBotConfigWhenDeviceActive:
    def test_syncs_when_bot_found(self):
        record = _make_record()
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "bot1",
            "public": "0",
            "ext": {"permission_owner": "owner1"},
            "owner_id": "u001",
            "owner_name": "User",
        }
        bot_sync = MagicMock()
        bot_sync.sync_bot_config_to_device.return_value = {"success": True}
        svc = _make_service(repo=repo, bot_query=bot_query, bot_sync=bot_sync)

        svc._sync_bot_config_when_device_active(device_id="staff_u001_default")
        bot_sync.sync_bot_config_to_device.assert_called_once()

    def test_no_op_when_device_not_found(self):
        repo = MagicMock()
        repo.get_by_device_id.return_value = None
        bot_sync = MagicMock()
        svc = _make_service(repo=repo, bot_sync=bot_sync)

        svc._sync_bot_config_when_device_active(device_id="nonexistent")
        bot_sync.sync_bot_config_to_device.assert_not_called()

    def test_no_op_when_bot_not_found(self):
        record = _make_record()
        repo = MagicMock()
        repo.get_by_device_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None
        bot_sync = MagicMock()
        svc = _make_service(repo=repo, bot_query=bot_query, bot_sync=bot_sync)

        svc._sync_bot_config_when_device_active(device_id="staff_u001_default")
        bot_sync.sync_bot_config_to_device.assert_not_called()

    def test_exception_does_not_propagate(self):
        repo = MagicMock()
        repo.get_by_device_id.side_effect = RuntimeError("kaboom")
        svc = _make_service(repo=repo)

        # Should not raise
        svc._sync_bot_config_when_device_active(device_id="x")


class TestEffectiveBindingStatusUsesStoredColumn:
    """Status gating reads the STORED binding column for every engine — the
    teclaw read-through is gone (the TeclawPublishTaskHandler keeps the column
    fresh post-provision), so the connect FAILED gate and the connectable-list
    ACTIVE filter both decide off the stored status."""

    # ── _effective_binding_status (the shared helper) ─────────────────────

    def test_returns_stored_status(self):
        svc = _make_service()
        assert (
            svc._effective_binding_status(
                _make_record(status=DeviceBindingStatus.PENDING.value)
            )
            == DeviceBindingStatus.PENDING.value
        )
        assert (
            svc._effective_binding_status(
                _make_record(status=DeviceBindingStatus.ACTIVE.value)
            )
            == DeviceBindingStatus.ACTIVE.value
        )

    # ── Surface B: get_device_connection FAILED gate ──────────────────────

    def test_connect_preempts_when_stored_status_failed(self):
        record = _make_record(status=DeviceBindingStatus.FAILED.value)
        repo = MagicMock()
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo)

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(binding_id=1, operator=_make_operator())

    # ── Surface A: list_connectable_devices ACTIVE filter ─────────────────

    def test_list_includes_when_stored_status_active(self):
        binding = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo = MagicMock()
        repo.get_by_ids.return_value = [binding]
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [{"binding_id": 1}]
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001", entity_type="staff", env=None,
        )
        assert total == 1 and items[0].record is binding

    def test_list_excludes_when_stored_status_not_active(self):
        binding = _make_record(status=DeviceBindingStatus.PENDING.value)
        repo = MagicMock()
        repo.get_by_ids.return_value = [binding]
        bot_query = MagicMock()
        bot_query.list_active_bots_by_entity.return_value = [{"binding_id": 1}]
        svc = _make_service(repo=repo, bot_query=bot_query)

        total, items = svc.list_connectable_devices(
            entity_id="u001", entity_type="staff", env=None,
        )
        assert total == 0 and items == []

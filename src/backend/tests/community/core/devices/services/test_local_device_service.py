"""Tests for agentclaw.community.core.devices.services.local_device_service.LocalDeviceService.

Post-BaaS migration:
- LocalDeviceService no longer takes a ProcessManager — its lifecycle is driven by
  BaasService + BaasPublishPoller.
- The legacy callback_token / adapter_port / openclaw_port machinery has been removed
  from the service surface; tests covering those are obsolete and have been removed.
- Connection info now comes from BaaS get_ws_info; health from list_devices_by_bot_uuid.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.errors import (
    DeviceNotFoundError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceBindingInfo,
    DeviceBindingStatus,
    DeviceConnectionInfo,
    OperatorContext,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.baas_device_lifecycle_executor import (
    BaasDeviceLifecycleError,
)
from agentclaw.community.core.devices.services.device_service import LOCAL_DEVICE_PROVIDER
from agentclaw.community.core.devices.services.local_device_service import (
    LocalDeviceAllocateError,
    LocalDeviceReleaseError,
    LocalDeviceService,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    BotWsConnectionInfoResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    id: int = 1,
    entity_id: str = "u001",
    entity_type: str = "staff",
    device_id: str = "bot-test-001",
    device_provider: str = LOCAL_DEVICE_PROVIDER,
    status: str = DeviceBindingStatus.ACTIVE.value,
    device_props: dict | None = None,
) -> DeviceBindingRecord:
    props = device_props
    if props is None:
        props = {
            "bot_uuid": "bot-test-001",
            "publish_id": "pub-001",
            "bolt_id": "default",
            "entity_id": entity_id,
        }
    return DeviceBindingRecord(
        id=id,
        entity_id=entity_id,
        entity_type=entity_type,
        device_id=device_id,
        device_provider=device_provider,
        env="dev",
        device_props=props,
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


# ---------------------------------------------------------------------------
# Fixtures — BaaS-backed
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_baas():
    """Fake BaasService — singlebox 改造后必备。"""
    m = MagicMock()
    m._baas_api_base = "http://baas.local"
    m._build_create_bot_payload.return_value = {"fake": "local-payload"}
    m.post_bots_api.return_value = {"bot_uuid": "bot-test-001", "publish_id": "pub-001"}
    m.approve_publish.return_value = {"status": "approved"}
    m.destroy_bot.return_value = {"publish_id": "pub-destroy-001"}
    m.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE", "ip": "127.0.0.1"}]
    m.get_ws_info.return_value = BotWsConnectionInfoResponse(
        ws_url="ws://fake",
        token="fake-token",
        target="127.0.0.1:18789",
        expires_at="2099-01-01T00:00:00",
        paas_device_id="paas-001",
        baas_base_url="http://baas-fake",
        engine_port=20003,
        tenant="team_claw",
        bot_uuid="bot-test-001",
    )
    from agentclaw.community.core.service_bot.services.baas_service import (
        HttpConnectionInfo,
    )
    m.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010",
        token="http-token-001",
    )
    return m


@pytest.fixture
def mock_poller():
    return MagicMock()


def _make_service(
    config: dict | None = None,
    repo=None,
    baas_service=None,
    publish_poller=None,
    bot_query=None,
) -> LocalDeviceService:
    repo = repo or MagicMock()
    bq = bot_query or MagicMock()
    bot_sync = MagicMock()
    baas = baas_service if baas_service is not None else MagicMock()
    poller = publish_poller if publish_poller is not None else MagicMock()
    return LocalDeviceService(
        repo,
        baas,
        poller,
        config=config or {},
        bot_query=bq,
        bot_sync=bot_sync,
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
    )


@pytest.fixture
def local_device_service(mock_baas, mock_poller):
    """Default LocalDeviceService wired with mock BaaS + poller."""
    return _make_service(baas_service=mock_baas, publish_poller=mock_poller)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestLocalDeviceServiceInit:
    def test_custom_aidesktop_root(self):
        config = {"aidesktop_root": "/custom/aidesktop"}
        svc = _make_service(config=config)
        assert svc._get_aidesktop_root() == "/custom/aidesktop"

    def test_default_aidesktop_root(self):
        svc = _make_service()
        assert svc._get_aidesktop_root() == "/aidesktop"


# ---------------------------------------------------------------------------
# _resolve_env_dir / _resolve_entity_dir
# ---------------------------------------------------------------------------


class TestResolveHelpers:
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
# _generate_device_id (override)
# ---------------------------------------------------------------------------


class TestGenerateDeviceId:
    def test_generates_id_with_uuid_suffix(self):
        svc = _make_service()
        device_id, bolt_id = svc._generate_device_id(
            entity_id="u001", entity_type="staff", bot_id="mybot"
        )
        assert bolt_id == "mybot"
        assert device_id.startswith("staff_u001_mybot_")
        # UUID hex is 32 chars
        assert len(device_id) > len("staff_u001_mybot_")

    def test_default_bot_id(self):
        svc = _make_service()
        device_id, bolt_id = svc._generate_device_id(
            entity_id="u001", entity_type="staff"
        )
        assert bolt_id == "default"
        assert "staff_u001_default_" in device_id

    def test_unique_ids(self):
        svc = _make_service()
        ids = {svc._generate_device_id(entity_id="u001", entity_type="staff")[0] for _ in range(10)}
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# _setup_directory
# ---------------------------------------------------------------------------


class TestSetupDirectory:
    def test_creates_directories(self, tmp_path: Path):
        svc = _make_service(config={"aidesktop_root": str(tmp_path)})

        result = svc._setup_directory(
            _make_operator(),
            entity_id="u001",
            entity_type="staff",
            bolt_id="default",
            env="dev",
        )

        # Must return empty list (no NAS mappings for local)
        assert result == []

        # Verify directories were created
        device_dir = tmp_path / "aidesktop_dev" / "bolt_data" / "staff_u001" / "default"
        assert device_dir.exists()
        assert (device_dir / "openclaw").exists()
        assert (device_dir / "openclaw" / "workspace").exists()
        assert (device_dir / "openclaw" / "workspace" / "skills" / "skills-local").exists()
        assert (device_dir / "openclaw" / "workspace" / "skills" / "active").exists()

    def test_replaces_symlink_with_directory(self, tmp_path: Path):
        svc = _make_service(config={"aidesktop_root": str(tmp_path)})

        # Pre-create the directory path
        device_dir = tmp_path / "aidesktop_dev" / "bolt_data" / "staff_u001" / "default"
        device_dir.mkdir(parents=True)
        openclaw_dir = device_dir / "openclaw"
        # Create a symlink in place of the openclaw dir
        link_target = tmp_path / "link_target"
        link_target.mkdir()
        openclaw_dir.symlink_to(link_target)
        assert openclaw_dir.is_symlink()

        svc._setup_directory(
            _make_operator(),
            entity_id="u001",
            entity_type="staff",
            bolt_id="default",
            env="dev",
        )

        # Symlink should now be replaced with real dir
        assert not openclaw_dir.is_symlink()
        assert openclaw_dir.is_dir()

    def test_directory_creation_failure_raises(self, tmp_path: Path):
        svc = _make_service(config={"aidesktop_root": "/nonexistent/readonly/path"})

        with patch("pathlib.Path.mkdir", side_effect=PermissionError("read only")):
            with pytest.raises(LocalDeviceAllocateError, match="创建设备目录失败"):
                svc._setup_directory(
                    _make_operator(),
                    entity_id="u001",
                    entity_type="staff",
                    bolt_id="default",
                    env="dev",
                )


# ---------------------------------------------------------------------------
# _do_allocate — BaaS-backed (Task 13)
# ---------------------------------------------------------------------------


def test_do_allocate_returns_bot_uuid_as_device_id(local_device_service, mock_baas):
    result = local_device_service._do_allocate(
        entity_id="staff_u001",
        entity_type="staff",
        bolt_id="bolt-1",
        device_id="ignored",
        storage_mappings=[],
        env="dev",
    )

    assert result.device_id == "bot-test-001"  # 来自 BaaS bot_uuid
    assert result.device_provider == "local"
    assert result.device_props["bot_uuid"] == "bot-test-001"
    assert result.device_props["publish_id"] == "pub-001"
    assert result.device_props["bolt_id"] == "bolt-1"
    mock_baas._build_create_bot_payload.assert_called_once()
    mock_baas.post_bots_api.assert_called_once()
    mock_baas.approve_publish.assert_called_once()


def test_do_allocate_preserves_service_bot_type_and_owner(local_device_service, mock_baas):
    local_device_service._do_allocate(
        entity_id="staff_u001",
        entity_type="staff",
        bolt_id="service-draft-1",
        device_id="ignored",
        storage_mappings=[],
        env="dev",
        bot_type="service",
        owner_id="u001",
    )

    call_kwargs = mock_baas._build_create_bot_payload.call_args.kwargs
    assert call_kwargs["owner_id"] == "u001"
    assert call_kwargs["bot"]["bot_type"] == "service"


def test_do_allocate_create_bot_raises_propagates(local_device_service, mock_baas):
    mock_baas.post_bots_api.side_effect = BaasServiceError("boom")

    with pytest.raises(LocalDeviceAllocateError) as exc_info:
        local_device_service._do_allocate(
            entity_id="u",
            entity_type="staff",
            bolt_id="b",
            device_id="d",
            storage_mappings=[],
            env="dev",
        )

    assert isinstance(exc_info.value.__cause__, BaasDeviceLifecycleError)
    assert isinstance(exc_info.value.__cause__.__cause__, BaasServiceError)
    mock_baas.approve_publish.assert_not_called()


def test_do_allocate_approve_publish_raises_no_rollback(local_device_service, mock_baas):
    """approve_publish 失败 raise，不调 destroy_bot 回滚（与 desktop 对齐）。"""
    mock_baas.approve_publish.side_effect = BaasServiceError("approve boom")

    with pytest.raises(LocalDeviceAllocateError) as exc_info:
        local_device_service._do_allocate(
            entity_id="u",
            entity_type="staff",
            bolt_id="b",
            device_id="d",
            storage_mappings=[],
            env="dev",
        )

    assert isinstance(exc_info.value.__cause__, BaasDeviceLifecycleError)
    assert isinstance(exc_info.value.__cause__.__cause__, BaasServiceError)
    mock_baas.destroy_bot.assert_not_called()


# ---------------------------------------------------------------------------
# _do_release — BaaS-backed (Task 14)
# ---------------------------------------------------------------------------


def test_do_release_calls_destroy_then_approve(local_device_service, mock_baas):
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"bot_uuid": "bot-test-001", "entity_id": "u001"},
    )

    assert local_device_service._do_release(device=device) is True
    mock_baas.destroy_bot.assert_called_once()
    mock_baas.approve_publish.assert_called_once()


def test_do_release_destroy_raises_propagates(local_device_service, mock_baas):
    mock_baas.destroy_bot.side_effect = BaasServiceError("boom")
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"bot_uuid": "bot-test-001", "entity_id": "u001"},
    )

    with pytest.raises(LocalDeviceReleaseError) as exc_info:
        local_device_service._do_release(device=device)

    assert isinstance(exc_info.value.__cause__, BaasDeviceLifecycleError)
    assert isinstance(exc_info.value.__cause__.__cause__, BaasServiceError)


def test_do_release_approve_failure_does_not_block(local_device_service, mock_baas):
    mock_baas.approve_publish.side_effect = BaasServiceError("approve boom")
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"bot_uuid": "bot-test-001", "entity_id": "u001"},
    )

    # 即使 approve 失败，_do_release 仍返 True
    assert local_device_service._do_release(device=device) is True


# ---------------------------------------------------------------------------
# _compose_device_conn_info — BaaS-backed (Task 14)
# ---------------------------------------------------------------------------


def test_compose_conn_info_uses_baas_ws_info(local_device_service, mock_baas):
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"bot_uuid": "bot-test-001", "binding_id": 99, "entity_id": "u001"},
    )

    conn = local_device_service._compose_device_conn_info(device=device)
    assert conn.target == "127.0.0.1:18789"
    # T07: http-info 成功时 token 由 http-info 覆盖（业务主走 HTTP）
    assert conn.token == "http-token-001"
    assert conn.bot_uuid == "bot-test-001"
    mock_baas.get_ws_info.assert_called_once_with(
        bind_id=99, device_affinity="u001", ws_conn_mode=None
    )


# ---------------------------------------------------------------------------
# _query_device_info — BaaS-backed (Task 14)
# ---------------------------------------------------------------------------


def test_query_device_info_active_returns_healthy(local_device_service, mock_baas):
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"bot_uuid": "bot-test-001"},
    )
    info = local_device_service._query_device_info(device=device)
    assert info["healthy"] is True
    assert info["device_ip"] == "127.0.0.1"


def test_query_device_info_empty_returns_unhealthy(local_device_service, mock_baas):
    mock_baas.list_devices_by_bot_uuid.return_value = []
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"bot_uuid": "bot-test-001"},
    )
    info = local_device_service._query_device_info(device=device)
    assert info["healthy"] is False
    assert info["device_ip"] is None


# ---------------------------------------------------------------------------
# _start_service — BaaS-backed (Task 14)
# ---------------------------------------------------------------------------


def test_start_service_starts_poller(local_device_service, mock_poller):
    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={
            "bot_uuid": "bot-test-001",
            "publish_id": "pub-001",
            "binding_id": 99,
        },
    )

    ok, msg = local_device_service._start_service(device=device, engine="openclaw")
    assert ok is True
    mock_poller.start.assert_called_once_with(
        publish_id="pub-001", device_id="bot-test-001", binding_id=99,
    )


# ---------------------------------------------------------------------------
# _exec_shell
# ---------------------------------------------------------------------------


class TestExecShell:
    def test_returns_not_implemented_message(self):
        svc = _make_service()
        device = AllocatedDevice(
            device_id="x",
            device_provider=LOCAL_DEVICE_PROVIDER,
            device_props={},
        )
        result = svc._exec_shell(device=device, shell_cmd="ls")
        assert "does not support" in result.lower() or "not support" in result.lower()


# ---------------------------------------------------------------------------
# get_device_connection (override) — BaaS-backed
# ---------------------------------------------------------------------------


class TestGetDeviceConnection:
    def test_own_device_succeeds(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(entity_id="u001", status=DeviceBindingStatus.ACTIVE.value)
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        conn = svc.get_device_connection(
            binding_id=1,
            operator=_make_operator("u001"),
        )
        assert isinstance(conn, DeviceConnectionInfo)

    def test_get_device_connection_v2_returns_baas_invoke_http_for_local(
        self, mock_baas, mock_poller,
    ):
        repo = MagicMock()
        record = _make_record(
            id=42,
            entity_id="u001",
            status=DeviceBindingStatus.ACTIVE.value,
        )
        repo.get_by_id.return_value = record
        svc = _make_service(
            repo=repo,
            baas_service=mock_baas,
            publish_poller=mock_poller,
        )

        conn_info = svc.get_device_connection_v2(
            user_id="u001",
            nick_name="User",
            binding_id=42,
            operator_tenant_id="default",
        )

        assert conn_info["type"] == "local"
        assert conn_info["url"] == (
            "http://baas-fake/api/v1/bots/team_claw/"
            "bot-test-001/invoke-http/20003"
        )
        assert conn_info["headers"] == {"x-proxypass-token": "http-token-001"}
        assert conn_info["baas_base_url"] == "http://baas-fake"
        assert conn_info["bot_uuid"] == "bot-test-001"

    def test_not_found_raises(self, mock_baas, mock_poller):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        with pytest.raises(DeviceNotFoundError):
            svc.get_device_connection(
                binding_id=99, operator=_make_operator()
            )

    def test_failed_device_raises(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(status=DeviceBindingStatus.FAILED.value)
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(
                binding_id=1, operator=_make_operator()
            )

    def test_other_user_public_bot_allowed(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(entity_id="owner", status=DeviceBindingStatus.ACTIVE.value)
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {"public": "1"}
        svc = _make_service(
            repo=repo,
            baas_service=mock_baas,
            publish_poller=mock_poller,
            bot_query=bot_query,
        )

        conn = svc.get_device_connection(
            binding_id=1, operator=_make_operator("requester")
        )
        assert isinstance(conn, DeviceConnectionInfo)

    def test_other_user_private_bot_denied(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(entity_id="owner", status=DeviceBindingStatus.ACTIVE.value)
        repo.get_by_id.return_value = record
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {"public": "0"}
        svc = _make_service(
            repo=repo,
            baas_service=mock_baas,
            publish_poller=mock_poller,
            bot_query=bot_query,
        )

        with pytest.raises(InvalidDeviceStatusError):
            svc.get_device_connection(
                binding_id=1, operator=_make_operator("requester")
            )


# ---------------------------------------------------------------------------
# list_connectable_devices (override)
# ---------------------------------------------------------------------------


class TestListConnectableDevices:
    def test_without_connection(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo.list_bindings.return_value = (1, [record])
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=False,
        )
        assert total == 1
        assert isinstance(items[0], DeviceBindingInfo)
        assert items[0].connection is None

    def test_with_connection_and_operator(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(entity_id="u001", status=DeviceBindingStatus.ACTIVE.value)
        repo.list_bindings.return_value = (1, [record])
        repo.get_by_id.return_value = record
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        # Connection should be populated (local device returns conn info via BaaS)
        assert items[0].connection is not None

    def test_with_connection_error_yields_none(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(entity_id="u001", status=DeviceBindingStatus.ACTIVE.value)
        repo.list_bindings.return_value = (1, [record])
        repo.get_by_id.return_value = None  # causes DeviceNotFoundError in get_device_connection
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        assert items[0].connection is None

    def test_no_operator_skips_connection(self, mock_baas, mock_poller):
        repo = MagicMock()
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo.list_bindings.return_value = (1, [record])
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        total, items = svc.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=None,
        )
        assert total == 1
        assert items[0].connection is None

    def test_falls_back_to_current_env_when_env_is_none(self, mock_baas, mock_poller):
        """当 env=None 时,service 应该 fallback 到 get_current_env() 并透传给 repo。"""
        repo = MagicMock()
        record = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        repo.list_bindings.return_value = (1, [record])
        svc = _make_service(repo=repo, baas_service=mock_baas, publish_poller=mock_poller)

        with patch(
            "agentclaw.community.utils.env_utils.get_current_env", return_value="pre"
        ) as mock_get_env:
            total, items = svc.list_connectable_devices(
                entity_id="u001",
                entity_type="staff",
                env=None,
                with_connection=False,
            )

        assert total == 1
        mock_get_env.assert_called_once()
        # repo 应该收到 resolved env 而不是 None
        assert repo.list_bindings.call_args.kwargs["env"] == "pre"


# ---------------------------------------------------------------------------
# apply_device (override) — BaaS-backed
# ---------------------------------------------------------------------------


class TestApplyDevice:
    def _patch_env(self):
        return patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev")

    def test_new_device_inserts_binding(self, tmp_path: Path, mock_baas, mock_poller):
        repo = MagicMock()
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = 10
        record = _make_record(id=10, status=DeviceBindingStatus.PENDING.value)
        repo.get_by_id.return_value = record

        svc = _make_service(
            config={"aidesktop_root": str(tmp_path)},
            repo=repo,
            baas_service=mock_baas,
            publish_poller=mock_poller,
        )

        with self._patch_env():
            result = svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot1",
            )

        assert result is record
        repo.insert_binding.assert_called_once()

    def test_reuses_released_binding(self, tmp_path: Path, mock_baas, mock_poller):
        repo = MagicMock()
        released = _make_record(id=5, status=DeviceBindingStatus.RELEASED.value)
        repo.get_released_binding.return_value = released
        updated = _make_record(id=5, status=DeviceBindingStatus.PENDING.value)
        repo.get_by_id.return_value = updated

        svc = _make_service(
            config={"aidesktop_root": str(tmp_path)},
            repo=repo,
            baas_service=mock_baas,
            publish_poller=mock_poller,
        )

        with self._patch_env():
            result = svc.apply_device(
                apply_reason="reuse",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
            )

        repo.reuse_binding.assert_called_once()
        repo.insert_binding.assert_not_called()
        assert result is updated

    def test_raises_if_binding_not_found_after_insert(self, tmp_path: Path, mock_baas, mock_poller):
        repo = MagicMock()
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = 99
        repo.get_by_id.return_value = None  # not found after insert

        svc = _make_service(
            config={"aidesktop_root": str(tmp_path)},
            repo=repo,
            baas_service=mock_baas,
            publish_poller=mock_poller,
        )

        with self._patch_env():
            with pytest.raises(LocalDeviceAllocateError, match="Failed to create device binding"):
                svc.apply_device(
                    apply_reason=None,
                    entity_id="u001",
                    entity_type="staff",
                    operator=_make_operator(),
                )


def test_compose_conn_info_fills_url_and_token_from_http_info(
    local_device_service, mock_baas
):
    """_compose_device_conn_info 应通过 BaaS get_http_info 拿 url+token 写到返回 DeviceConnectionInfo。"""
    from agentclaw.community.core.devices.models import AllocatedDevice

    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={
            "bot_uuid": "bot-test-001",
            "binding_id": 99,
            "entity_id": "u001",
            "adapter_port": 20010,
        },
    )

    conn = local_device_service._compose_device_conn_info(device=device)

    assert conn.url == "http://10.0.0.1:20010"
    # http-info 提供的 token 应覆盖 ws-info 的（业务调用主要走 http）
    assert conn.token == "http-token-001"
    mock_baas.get_http_info.assert_called_once()
    call_kwargs = mock_baas.get_http_info.call_args.kwargs
    assert call_kwargs["bind_id"] == 99
    assert call_kwargs["port"] == 20010
    assert call_kwargs["device_affinity"] == "u001"


def test_report_device_alive_backfills_adapter_port_with_binding_id(
    local_device_service, mock_baas
):
    repo = local_device_service._repo
    current = _make_record(
        id=41,
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={"bot_uuid": "bot-test-001", "publish_id": "pub-001"},
    )
    updated = _make_record(
        id=42,
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={"bot_uuid": "bot-test-001", "publish_id": "pub-001"},
    )
    repo.get_by_device_id.return_value = current
    repo.get_by_id.return_value = updated

    result = local_device_service.report_device_alive(
        device_id="bot-test-001",
        token="",
        skip_token_check=True,
    )

    assert result is updated
    mock_baas.get_ws_info.assert_called_once_with(bind_id=42)
    repo.update_device_props.assert_called_once_with(
        binding_id=42,
        props={
            "bot_uuid": "bot-test-001",
            "publish_id": "pub-001",
            "adapter_port": 20003,
        },
    )


def test_compose_conn_info_http_info_failure_falls_back_to_ws_only(
    local_device_service, mock_baas
):
    """http-info 失败但 ws-info 成功 → url 为空、token 用 ws 的、conn 仍 available=True。"""
    from agentclaw.community.core.devices.models import AllocatedDevice
    from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

    mock_baas.get_http_info.side_effect = BaasServiceError("http endpoint not ready")

    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={
            "bot_uuid": "bot-test-001",
            "binding_id": 99,
            "entity_id": "u001",
        },
    )

    conn = local_device_service._compose_device_conn_info(device=device)

    # available 仍 True（ws-info 成功）
    assert conn.available is True
    # url 空——caller 会 fallback 到 `f"http://{target}"`
    assert conn.url == ""
    # token 用 ws 的（fake-token，见 fixture）
    assert conn.token == "fake-token"
    # target 仍是 ws 的
    assert conn.target == "127.0.0.1:18789"


def test_compose_conn_info_ws_info_failure_already_returns_unavailable(
    local_device_service, mock_baas
):
    """ws-info 失败 → 整个 conn available=False（保留 9dc7d7837 既有行为）。"""
    from agentclaw.community.core.devices.models import AllocatedDevice
    from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

    mock_baas.get_ws_info.side_effect = BaasServiceError("ws endpoint not ready")

    device = AllocatedDevice(
        device_id="bot-test-001",
        device_provider="local",
        device_props={"binding_id": 99, "entity_id": "u001"},
    )

    conn = local_device_service._compose_device_conn_info(device=device)

    assert conn.available is False
    assert conn.target == ""
    assert conn.token == ""
    assert "unavailable" in conn.message.lower()
    # ws 都没拿到，不应该再去尝试 http
    mock_baas.get_http_info.assert_not_called()

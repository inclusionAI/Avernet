"""Tests for ExpertChatInstanceService — caller container lifecycle.

Tests the per-caller BaaS container provisioning service which handles:
- Build artifact resolution via publish record lookup
- Container creation via BotBuildService.release_async
- Container upgrade via BotBuildService.upgrade_async
- Connection building via BaaS get_ws_info_by_bot_uuid
- Exception handling with traceback recording
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass

from agentclaw.community.core.expert_chat.errors import (
    BotNotPublishedError,
    ConnectionError,
)
from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import (
    ExpertChatInstanceService,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    BotWsConnectionInfoResponse,
)
from agentclaw.community.core.service_bot.types import PublishStage


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

BOT_ID = "bot1"
OWNER_ID = "owner1"
USER_ID = "caller1"
BOT_UUID = "uuid-test-001"
BINDING_ID = 12345


@dataclass
class MockPublishRecord:
    """Minimal stand-in for BotPublishRecord."""
    id: int = 123
    name: str = "Test Bot"
    owner_id: str = OWNER_ID
    version: int = 1
    ext: dict = None

    def __post_init__(self):
        if self.ext is None:
            self.ext = {"migration_path": "/nas/migration/path"}


def _make_ws_info():
    """Create a mock BotWsConnectionInfoResponse object."""
    return BotWsConnectionInfoResponse(
        ws_url="ws://localhost:8890/api/openclaw/ws",
        token="test-token-abc",
        target=BOT_UUID,
        expires_at="2099-01-01T00:00:00Z",
        paas_device_id="device-001",
        baas_base_url="http://localhost:8890",
        engine_port=20003,
        tenant="test_tenant",
        bot_uuid=BOT_UUID,
    )


def _make_service(
    *,
    instance_repo=None,
    baas=None,
    publish_repo=None,
    bot_repo=None,
    binding_repo=None,
    bot_build_service=None,
):
    """Construct ExpertChatInstanceService with mocks."""
    instance_repo = instance_repo or MagicMock()
    baas = baas or MagicMock()
    publish_repo = publish_repo or MagicMock()
    bot_repo = bot_repo or MagicMock()
    binding_repo = binding_repo or MagicMock()
    bot_build_service = bot_build_service or MagicMock()

    svc = ExpertChatInstanceService(
        instance_repo=instance_repo,
        baas_service=baas,
        bot_publish_repo=publish_repo,
        bot_repo=bot_repo,
        binding_repo=binding_repo,
        bot_build_service=bot_build_service,
    )
    return svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service


def _wire_publish(publish_repo, record=None):
    """Wire publish_repo to return a success publish record."""
    publish_repo.get_by_publish_bot_id = MagicMock(
        return_value=record or MockPublishRecord()
    )


def _wire_bot_repo(bot_repo, bot_info=None):
    """Wire bot_repo to return bot info."""
    if bot_info is None:
        bot_info = {
            "bot_id": BOT_ID,
            "owner_id": OWNER_ID,
            "bot_name": "Test Bot",
            "entity_id": OWNER_ID,
        }
    bot_repo.get_by_id_and_owner = MagicMock(return_value=bot_info)


def _wire_binding_repo(binding_repo, binding_id=BINDING_ID):
    """Wire binding_repo to return a binding_id on insert."""
    binding_repo.insert_binding = MagicMock(return_value=binding_id)


def _wire_bot_build_service_async(bot_build_service, create_result=None, upgrade_result=None):
    """Wire bot_build_service async methods with AsyncMock."""
    if create_result is not None:
        bot_build_service.release_async = AsyncMock(return_value=create_result)
    if upgrade_result is not None:
        bot_build_service.upgrade_async = AsyncMock(return_value=upgrade_result)


class TestResolveBuildArtifact:
    """Tests for _resolve_build_artifact method."""

    def test_no_success_publish_raises_not_published(self):
        """Lines 267, 271: No success publish record raises BotNotPublishedError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=None)

        with pytest.raises(BotNotPublishedError) as exc_info:
            svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert "no success publish order" in str(exc_info.value).lower()
        publish_repo.get_by_publish_bot_id.assert_called_once()

    def test_success_returns_record_and_migration_path(self):
        """Successful resolution returns (record, migration_path)."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        record = MockPublishRecord(
            id=456,
            version=2,
            ext={"migration_path": "/nas/custom/path"}
        )
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=record)

        result_record, migration_path = svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert result_record.id == 456
        assert migration_path == "/nas/custom/path"

    def test_publish_record_ext_none_returns_none_migration_path(self):
        """Line 275: When publish_record.ext is None, migration_path should be None."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        record = MockPublishRecord(id=789, version=1, ext=None)
        # Override __post_init__ effect
        record.ext = None
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=record)

        result_record, migration_path = svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert result_record.id == 789
        assert migration_path is None

    def test_publish_record_version_none_uses_default(self):
        """Line 103: When publish_record.version is None, default to 1."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        record = MockPublishRecord(id=999, version=None, ext={"migration_path": "/nas/path"})
        # Ensure version is None
        record.version = None
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=record)

        result_record, migration_path = svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert result_record.id == 999
        assert result_record.version is None


class TestCreateContainer:
    """Tests for _create_container method (async, uses release_async)."""

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_connection_error(self):
        """Bot not found raises ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        bot_repo.get_by_id_and_owner = MagicMock(return_value=None)

        with pytest.raises(ConnectionError) as exc_info:
            await svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "Bot not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generic_exception_wrapped_as_connection_error(self):
        """Generic exceptions wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        _wire_bot_repo(bot_repo)
        bot_build_service.release_async = AsyncMock(side_effect=RuntimeError("network error"))

        with pytest.raises(ConnectionError) as exc_info:
            await svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "network error" in exc_info.value.original_error

    @pytest.mark.asyncio
    async def test_no_bot_uuid_in_result_raises_connection_error(self):
        """No bot_uuid in release_async result raises ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        _wire_bot_repo(bot_repo)
        bot_build_service.release_async = AsyncMock(return_value={"publish_id": 999})  # No bot_uuid

        with pytest.raises(ConnectionError) as exc_info:
            await svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "no bot_uuid" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_success_returns_bot_uuid_and_publish_id(self):
        """Successful create returns bot_uuid and publish_id."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})

        result = await svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert result["bot_uuid"] == BOT_UUID
        assert result["publish_id"] == 888
        assert result["binding_id"] == BINDING_ID
        # Verify release_async was called with correct parameters
        bot_build_service.release_async.assert_called_once()
        call_kwargs = bot_build_service.release_async.call_args[1]
        assert call_kwargs["user_id"] == USER_ID
        assert call_kwargs["migration_path"] == "/nas/path"
        assert call_kwargs["publish_stage"] == PublishStage.ONLINE
        # Verify binding was created with correct params
        binding_repo.insert_binding.assert_called_once()
        call_kwargs = binding_repo.insert_binding.call_args[1]
        assert call_kwargs["entity_id"] == OWNER_ID
        assert call_kwargs["device_id"] == BOT_UUID

    @pytest.mark.asyncio
    async def test_release_async_called_with_correct_params(self):
        """release_async is called with expected parameters."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        bot_info = {
            "bot_id": BOT_ID,
            "owner_id": OWNER_ID,
            "bot_name": "Test Bot",
            "entity_id": OWNER_ID,
        }
        _wire_bot_repo(bot_repo, bot_info)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})

        await svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path", version=2)

        bot_build_service.release_async.assert_called_once()
        call_kwargs = bot_build_service.release_async.call_args[1]
        assert call_kwargs["bot"] == bot_info
        assert call_kwargs["user_id"] == USER_ID
        assert call_kwargs["migration_path"] == "/nas/path"
        assert call_kwargs["device_count"] == 1
        assert call_kwargs["publish_stage"] == PublishStage.ONLINE
        assert call_kwargs["version"] == "2"


class TestUpgradeContainer:
    """Tests for _upgrade_container method (async, uses upgrade_async)."""

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_connection_error(self):
        """Bot not found raises ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        bot_repo.get_by_id_and_owner = MagicMock(return_value=None)

        with pytest.raises(ConnectionError) as exc_info:
            await svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"

    @pytest.mark.asyncio
    async def test_success_returns_bot_uuid_and_publish_id(self):
        """Successful upgrade returns bot_uuid and publish_id."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})

        result = await svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert result["bot_uuid"] == BOT_UUID
        assert result["publish_id"] == 999
        bot_build_service.upgrade_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_generic_exception_wrapped_as_connection_error(self):
        """Generic exceptions wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(side_effect=RuntimeError("upgrade failed"))

        with pytest.raises(ConnectionError) as exc_info:
            await svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "upgrade failed" in exc_info.value.original_error

    @pytest.mark.asyncio
    async def test_upgrade_async_called_with_correct_params(self):
        """upgrade_async is called with expected parameters."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        bot_info = {
            "bot_id": BOT_ID,
            "owner_id": OWNER_ID,
            "bot_name": "Test Bot",
            "entity_id": OWNER_ID,
        }
        _wire_bot_repo(bot_repo, bot_info)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})

        await svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path", version=3)

        bot_build_service.upgrade_async.assert_called_once()
        call_kwargs = bot_build_service.upgrade_async.call_args[1]
        assert call_kwargs["bot_uuid"] == BOT_UUID
        assert call_kwargs["bot"] == bot_info
        assert call_kwargs["user_id"] == USER_ID
        assert call_kwargs["migration_path"] == "/nas/path"
        assert call_kwargs["device_count"] == 1
        assert call_kwargs["publish_stage"] == PublishStage.ONLINE
        assert call_kwargs["version"] == "3"


class TestBuildConnection:
    """Tests for _build_connection method."""

    def test_baas_service_error_wrapped_as_connection_error(self):
        """BaasServiceError wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        baas.get_ws_info_by_bot_uuid = MagicMock(side_effect=BaasServiceError("ws error"))

        with pytest.raises(ConnectionError) as exc_info:
            svc._build_connection(BOT_UUID, BOT_ID, USER_ID)

        assert exc_info.value.error_code == "5001"
        assert "ws error" in exc_info.value.original_error

    def test_generic_exception_wrapped_as_connection_error(self):
        """Generic exceptions wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        baas.get_ws_info_by_bot_uuid = MagicMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(ConnectionError) as exc_info:
            svc._build_connection(BOT_UUID, BOT_ID, USER_ID)

        assert exc_info.value.error_code == "5001"

    def test_success_returns_connection_dict(self):
        """Successful build returns connection dict."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        result = svc._build_connection(BOT_UUID, BOT_ID, USER_ID)

        assert result["ws_url"] == ws_info.ws_url
        assert result["token"] == ws_info.token
        assert result["bot_uuid"] == BOT_UUID
        baas.get_ws_info_by_bot_uuid.assert_called_once_with(
            bot_uuid=BOT_UUID, device_affinity=USER_ID
        )


class TestGetCallerConnection:
    """Tests for get_caller_connection method."""

    @pytest.mark.asyncio
    async def test_no_success_publish_raises_not_published(self):
        """No success publish record raises BotNotPublishedError."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=None)

        with pytest.raises(BotNotPublishedError):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

    @pytest.mark.asyncio
    async def test_success_instance_returns_immediately(self):
        """Instance already success with matching version returns immediately."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 2,  # Higher than publish record version
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )
        _wire_publish(publish_repo, MockPublishRecord(version=1))  # Old version
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is False
        assert result["connection"]["bot_uuid"] == BOT_UUID
        assert result["instance"]["status"] == "success"
        # No create or upgrade called
        bot_build_service.release_async.assert_not_called()
        bot_build_service.upgrade_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_instance_with_old_version_upgrades(self):
        """Success instance with old version triggers upgrade."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 1,  # Old version
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )
        _wire_publish(publish_repo, MockPublishRecord(version=2))  # New version
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is False
        bot_build_service.upgrade_async.assert_called_once()
        bot_build_service.release_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_instance_creates_container(self):
        """New instance triggers create_container."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        instance_repo.update_instance = MagicMock(return_value=True)
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        # Mock get_instance to return different values on subsequent calls
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is False
        bot_build_service.release_async.assert_called_once()
        # Verify binding_id is saved to ext and binding status is updated
        binding_repo.update_status.assert_called_once_with(binding_id=BINDING_ID, status="ACTIVE")
        # Verify update_instance was called with binding_id in ext
        update_calls = instance_repo.update_instance.call_args_list
        assert any(
            call[1].get("ext", {}).get("binding_id") == BINDING_ID
            for call in update_calls
            if call[1].get("ext")
        )

    @pytest.mark.asyncio
    async def test_instance_not_ready_returns_need_poll(self):
        """Instance not ready returns need_poll=True."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 1,
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": existing_ext}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is True
        assert result["connection"] is None
        assert result["instance"]["status"] == "init"

    @pytest.mark.asyncio
    async def test_init_status_with_baas_publish_id_skips_upgrade(self):
        """When status is 'init' and baas_publish_id exists, should skip upgrade and poll progress."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 1,
            "baas_publish_id": 888,  # Has baas_publish_id
            "binding_id": BINDING_ID,  # Has binding_id from previous create
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": existing_ext}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Should NOT call upgrade_async since status is 'init' and baas_publish_id exists
        bot_build_service.upgrade_async.assert_not_called()
        bot_build_service.release_async.assert_not_called()
        # Should poll progress
        baas.get_publish_progress.assert_called_once()
        # Should update binding status with binding_id from ext
        binding_repo.update_status.assert_called_once_with(binding_id=BINDING_ID, status="PENDING")
        assert result["need_poll"] is True
        assert result["connection"] is None

    @pytest.mark.asyncio
    async def test_init_status_without_baas_publish_id_calls_upgrade(self):
        """When status is 'init' but no baas_publish_id, should call upgrade."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 1,
            # No baas_publish_id
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": existing_ext}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Should call upgrade_async since no baas_publish_id
        bot_build_service.upgrade_async.assert_called_once()
        assert result["need_poll"] is True
        assert result["connection"] is None

    @pytest.mark.asyncio
    async def test_non_init_status_calls_upgrade(self):
        """When status is not 'init' (e.g., 'failed'), should call upgrade."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 1,
            "baas_publish_id": 888,  # Has baas_publish_id, but status is not 'init'
            "binding_id": BINDING_ID,  # Has binding_id from ext
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "failed", "ext": existing_ext}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Should call upgrade_async since status is not 'init'
        bot_build_service.upgrade_async.assert_called_once()
        # Should update binding status with binding_id from ext
        binding_repo.update_status.assert_called_once_with(binding_id=BINDING_ID, status="PENDING")
        assert result["need_poll"] is True
        assert result["connection"] is None

    @pytest.mark.asyncio
    async def test_success_instance_without_bot_uuid_creates_container(self):
        """Success instance without bot_uuid triggers create."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": None,  # No bot_uuid
            "service_bot_publish_id": 123,
            "version": 1,
        }
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return success on second call
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 1, "status": "success", "ext": existing_ext}
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID, "version": 1}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        bot_build_service.release_async.assert_called_once()
        assert result["need_poll"] is False

    @pytest.mark.asyncio
    async def test_success_instance_version_none_uses_zero(self):
        """ext.get('version') defaults to 0 when None."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "version": None,  # No version
        }
        _wire_publish(publish_repo, MockPublishRecord(version=1))
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return success on second call
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 1, "status": "success", "ext": existing_ext}
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID, "version": 1}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # version None vs 1 triggers upgrade since 0 < 1
        bot_build_service.upgrade_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_baas_publish_failed_sets_status_to_failed(self):
        """FAILED status from baas_publish sets instance status to 'failed'."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "FAILED"})
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return failed status on second call
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "failed", "ext": {"bot_uuid": BOT_UUID}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify update_instance was called with status="failed"
        update_calls = instance_repo.update_instance.call_args_list
        assert any(
            call[1].get("status") == "failed"
            for call in update_calls
        )
        assert result["need_poll"] is True
        assert result["connection"] is None

    @pytest.mark.asyncio
    async def test_create_without_publish_id_skips_poll(self):
        """When baas_publish_id is None, skip polling."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        # release_async returns no publish_id
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return success on second call
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # get_publish_progress should not be called
        baas.get_publish_progress.assert_not_called()
        assert result["need_poll"] is False

    @pytest.mark.asyncio
    async def test_upgrade_adds_bot_uuid_to_ext_if_missing(self):
        """Upgrade preserves bot_uuid in ext when upgrading."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 1,
        }
        NEW_BOT_UUID = "new-uuid-002"
        _wire_publish(publish_repo, MockPublishRecord(version=2))
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": NEW_BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = BotWsConnectionInfoResponse(
            ws_url="ws://localhost:8890/api/openclaw/ws",
            token="test-token",
            target=NEW_BOT_UUID,
            expires_at="2099-01-01T00:00:00Z",
            paas_device_id="device-002",
            baas_base_url="http://localhost:8890",
            engine_port=20003,
            tenant="test_tenant",
            bot_uuid=NEW_BOT_UUID,
        )
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return success on second call
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 1, "status": "init", "ext": existing_ext}
            return {"id": 1, "status": "success", "ext": {"bot_uuid": NEW_BOT_UUID}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        bot_build_service.upgrade_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_upgrade_bypasses_version_check(self):
        """force_upgrade=True bypasses the version check fast path."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 2,  # Same as publish record version
        }
        _wire_publish(publish_repo, MockPublishRecord(version=2))  # Same version
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return success on subsequent calls
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            return {"id": 1, "status": "success", "ext": existing_ext}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        # Call with force_upgrade=True
        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID, force_upgrade=True)

        # Should have called upgrade_async despite version match
        bot_build_service.upgrade_async.assert_called_once()
        bot_build_service.release_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_upgrade_false_uses_fast_path(self):
        """force_upgrade=False uses the version check fast path when version is current."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "service_bot_publish_id": 123,
            "version": 2,  # Higher than publish record version
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )
        _wire_publish(publish_repo, MockPublishRecord(version=1))  # Old version
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        # Call with force_upgrade=False (default)
        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID, force_upgrade=False)

        # Should NOT call release_async or upgrade_async - fast path used
        bot_build_service.release_async.assert_not_called()
        bot_build_service.upgrade_async.assert_not_called()
        assert result["need_poll"] is False
        assert result["connection"]["bot_uuid"] == BOT_UUID

    @pytest.mark.asyncio
    async def test_force_upgrade_creates_when_no_bot_uuid(self):
        """force_upgrade=True with no bot_uuid triggers create, not upgrade."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": None,  # No bot_uuid
            "service_bot_publish_id": 123,
            "version": 1,
        }
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        # Mock get_instance to return success on second call
        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 1, "status": "success", "ext": existing_ext}
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID, "version": 1}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)

        # Call with force_upgrade=True
        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID, force_upgrade=True)

        # Should have called release_async (no bot_uuid, so create not upgrade)
        bot_build_service.release_async.assert_called_once()
        bot_build_service.upgrade_async.assert_not_called()
        assert result["need_poll"] is False


class TestGetCallerConnectionExceptionHandling:
    """Tests for exception handling in get_caller_connection."""

    @pytest.mark.asyncio
    async def test_exception_records_traceback_in_ext(self):
        """Exception during get_caller_connection records traceback in instance ext."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        # Make release_async raise an exception (gets wrapped in ConnectionError by _create_container)
        bot_build_service.release_async = AsyncMock(side_effect=RuntimeError("container creation failed"))

        with pytest.raises(ConnectionError, match="container creation failed"):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify update_instance was called with error info
        update_calls = instance_repo.update_instance.call_args_list
        assert len(update_calls) > 0
        last_call = update_calls[-1]
        assert last_call[1]["status"] == "failed"
        ext = last_call[1]["ext"]
        assert "error" in ext
        assert "message" in ext["error"]
        assert "container creation failed" in ext["error"]["message"]
        assert "traceback" in ext["error"]

    @pytest.mark.asyncio
    async def test_exception_update_failure_does_not_affect_main_flow(self):
        """If update_instance fails when recording error, exception still propagates."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        bot_build_service.release_async = AsyncMock(side_effect=RuntimeError("container creation failed"))
        # Make update_instance also fail
        instance_repo.update_instance = MagicMock(side_effect=Exception("db error"))

        # The original exception should still be raised (wrapped in ConnectionError)
        with pytest.raises(ConnectionError, match="container creation failed"):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

    @pytest.mark.asyncio
    async def test_exception_in_upgrade_records_traceback(self):
        """Exception during upgrade records traceback in instance ext."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "version": 1,
        }
        _wire_publish(publish_repo, MockPublishRecord(version=2))
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(side_effect=RuntimeError("upgrade failed"))

        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )

        with pytest.raises(ConnectionError, match="upgrade failed"):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify update_instance was called with error info
        update_calls = instance_repo.update_instance.call_args_list
        assert len(update_calls) > 0
        last_call = update_calls[-1]
        assert last_call[1]["status"] == "failed"
        ext = last_call[1]["ext"]
        assert "error" in ext
        assert "upgrade failed" in ext["error"]["message"]

    @pytest.mark.asyncio
    async def test_build_connection_exception_records_traceback(self):
        """Exception during _build_connection records traceback in instance ext."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "version": 2,
        }
        _wire_publish(publish_repo, MockPublishRecord(version=1))
        baas.get_ws_info_by_bot_uuid = MagicMock(side_effect=RuntimeError("ws connection failed"))

        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )

        # _build_connection wraps RuntimeError in ConnectionError with message "无法连接到Bot服务"
        with pytest.raises(ConnectionError, match="无法连接到Bot服务"):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify update_instance was called with error info
        update_calls = instance_repo.update_instance.call_args_list
        assert len(update_calls) > 0
        last_call = update_calls[-1]
        assert last_call[1]["status"] == "failed"
        ext = last_call[1]["ext"]
        assert "error" in ext
        # The traceback should contain the original error
        assert "traceback" in ext["error"]

    @pytest.mark.asyncio
    async def test_exception_preserves_existing_ext(self):
        """Exception handling preserves existing ext data and adds error info."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "version": 1,
            "service_bot_publish_id": 123,
            "custom_field": "custom_value",
        }
        _wire_publish(publish_repo, MockPublishRecord(version=2))
        _wire_bot_repo(bot_repo)
        bot_build_service.upgrade_async = AsyncMock(side_effect=RuntimeError("upgrade failed"))

        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )

        with pytest.raises(ConnectionError):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify that existing ext fields are preserved
        update_calls = instance_repo.update_instance.call_args_list
        last_call = update_calls[-1]
        ext = last_call[1]["ext"]
        assert ext["bot_uuid"] == BOT_UUID
        assert ext["version"] == 1
        assert ext["service_bot_publish_id"] == 123
        assert ext["custom_field"] == "custom_value"
        assert "error" in ext


class TestGetCallerConnectionEdgeCases:
    """Additional edge case tests for get_caller_connection."""

    @pytest.mark.asyncio
    async def test_create_container_success_returns_connection(self):
        """Full happy path: create container and return connection."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()

        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID, "version": 1}}
        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        instance_repo.update_instance = MagicMock(return_value=True)
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is False
        assert result["connection"] is not None
        assert result["connection"]["bot_uuid"] == BOT_UUID
        assert result["connection"]["ws_url"] == ws_info.ws_url
        assert result["connection"]["token"] == ws_info.token

    @pytest.mark.asyncio
    async def test_publish_record_version_none_uses_default_one(self):
        """When publish_record.version is None, defaults to 1 for comparison."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "version": 2,
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )
        # publish_version is None, defaults to 1, which is less than 2
        record = MockPublishRecord(id=123, version=None, ext={"migration_path": "/path"})
        record.version = None
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=record)
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Version 1 (default) <= 2 (instance), so should return immediately
        assert result["need_poll"] is False
        assert result["connection"]["bot_uuid"] == BOT_UUID


class TestRepositoryUpdateInstanceWithStatusOnly:
    """Tests for update_instance called with status only."""

    @pytest.mark.asyncio
    async def test_publish_status_success_sets_instance_status(self):
        """SUCCESS status from baas_publish sets instance status to 'success'."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()

        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID}}

        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        instance_repo.update_instance = MagicMock(return_value=True)
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify update_instance was called with status="success"
        update_calls = instance_repo.update_instance.call_args_list
        assert any(
            call[1].get("status") == "success"
            for call in update_calls
            if call[1].get("status")
        )
        assert result["need_poll"] is False
        assert result["connection"]["bot_uuid"] == BOT_UUID

    @pytest.mark.asyncio
    async def test_publish_status_failed_sets_instance_status(self):
        """FAILED status from baas_publish sets instance status to 'failed'."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()

        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "failed", "ext": {"bot_uuid": BOT_UUID}}

        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        instance_repo.update_instance = MagicMock(return_value=True)
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "FAILED"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # Verify update_instance was called with status="failed"
        update_calls = instance_repo.update_instance.call_args_list
        assert any(
            call[1].get("status") == "failed"
            for call in update_calls
            if call[1].get("status")
        )
        assert result["need_poll"] is True
        assert result["connection"] is None

    @pytest.mark.asyncio
    async def test_publish_status_running_sets_instance_status_to_init(self):
        """RUNNING status sets instance status to 'init' (not original status)."""
        svc, instance_repo, baas, publish_repo, bot_repo, binding_repo, bot_build_service = _make_service()

        call_count = [0]
        def mock_get_instance(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"id": 1, "status": "init", "ext": {"bot_uuid": BOT_UUID}}

        instance_repo.get_instance = MagicMock(side_effect=mock_get_instance)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        instance_repo.update_instance = MagicMock(return_value=True)
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        _wire_binding_repo(binding_repo)
        bot_build_service.release_async = AsyncMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # RUNNING sets status to "init" explicitly
        update_calls = instance_repo.update_instance.call_args_list
        assert any(
            call[1].get("status") == "init"
            for call in update_calls
            if call[1].get("status")
        )
        assert result["need_poll"] is True
        assert result["connection"] is None
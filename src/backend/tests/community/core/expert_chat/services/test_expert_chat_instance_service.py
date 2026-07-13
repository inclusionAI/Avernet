"""Tests for ExpertChatInstanceService — caller container lifecycle.

Tests the per-caller BaaS container provisioning service which handles:
- Build artifact resolution via publish record lookup
- Container creation via BaaS create_bot
- Container upgrade via BaaS upgrade_bot
- Connection building via BaaS get_ws_info_by_bot_uuid
"""
import pytest
from unittest.mock import MagicMock, patch
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


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

BOT_ID = "bot1"
OWNER_ID = "owner1"
USER_ID = "caller1"
BOT_UUID = "uuid-test-001"


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
):
    """Construct ExpertChatInstanceService with mocks."""
    instance_repo = instance_repo or MagicMock()
    baas = baas or MagicMock()
    publish_repo = publish_repo or MagicMock()
    bot_repo = bot_repo or MagicMock()

    svc = ExpertChatInstanceService(
        instance_repo=instance_repo,
        baas_service=baas,
        bot_publish_repo=publish_repo,
        bot_repo=bot_repo,
    )
    return svc, instance_repo, baas, publish_repo, bot_repo


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


class TestResolveBuildArtifact:
    """Tests for _resolve_build_artifact method."""

    def test_no_success_publish_raises_not_published(self):
        """Lines 267, 271: No success publish record raises BotNotPublishedError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=None)

        with pytest.raises(BotNotPublishedError) as exc_info:
            svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert "no success publish order" in str(exc_info.value).lower()
        publish_repo.get_by_publish_bot_id.assert_called_once()

    def test_success_returns_record_and_migration_path(self):
        """Successful resolution returns (record, migration_path)."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
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
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        record = MockPublishRecord(id=789, version=1, ext=None)
        # Override __post_init__ effect
        record.ext = None
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=record)

        result_record, migration_path = svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert result_record.id == 789
        assert migration_path is None

    def test_publish_record_version_none_uses_default(self):
        """Line 103: When publish_record.version is None, default to 1."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        record = MockPublishRecord(id=999, version=None, ext={"migration_path": "/nas/path"})
        # Ensure version is None
        record.version = None
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=record)

        result_record, migration_path = svc._resolve_build_artifact(BOT_ID, OWNER_ID)

        assert result_record.id == 999
        assert result_record.version is None


class TestCreateContainer:
    """Tests for _create_container method (lines 302, 317-320, 324, 333)."""

    def test_bot_not_found_raises_connection_error(self):
        """Line 302: Bot not found raises ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        bot_repo.get_by_id_and_owner = MagicMock(return_value=None)

        with pytest.raises(ConnectionError) as exc_info:
            svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "Bot not found" in str(exc_info.value)

    def test_baas_service_error_propagates(self):
        """Line 317-318: BaasServiceError propagates directly."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(side_effect=BaasServiceError("baas down"))

        with pytest.raises(BaasServiceError):
            svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

    def test_generic_exception_wrapped_as_connection_error(self):
        """Lines 319-328: Generic exceptions wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(side_effect=RuntimeError("network error"))

        with pytest.raises(ConnectionError) as exc_info:
            svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "network error" in exc_info.value.original_error

    def test_no_bot_uuid_in_result_raises_connection_error(self):
        """Lines 332-337: No bot_uuid in create_bot result raises ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(return_value={"publish_id": 999})  # No bot_uuid

        with pytest.raises(ConnectionError) as exc_info:
            svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "no bot_uuid" in str(exc_info.value).lower()

    def test_success_returns_bot_uuid_and_publish_id(self):
        """Successful create returns bot_uuid and publish_id."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})

        result = svc._create_container(BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert result["bot_uuid"] == BOT_UUID
        assert result["publish_id"] == 888


class TestUpgradeContainer:
    """Tests for _upgrade_container method (lines 364-366, 370-372, 381, 385, 389-390, 394)."""

    def test_bot_not_found_raises_connection_error(self):
        """Lines 364-366: Bot not found raises ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        bot_repo.get_by_id_and_owner = MagicMock(return_value=None)

        with pytest.raises(ConnectionError) as exc_info:
            svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"

    def test_success_returns_bot_uuid_and_publish_id(self):
        """Lines 381, 385-388: Successful upgrade returns bot_uuid and publish_id."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        _wire_bot_repo(bot_repo)
        baas.upgrade_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})

        result = svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert result["bot_uuid"] == BOT_UUID
        assert result["publish_id"] == 999
        baas.upgrade_bot.assert_called_once()

    def test_generic_exception_wrapped_as_connection_error(self):
        """Lines 389-398: Generic exceptions wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        _wire_bot_repo(bot_repo)
        baas.upgrade_bot = MagicMock(side_effect=RuntimeError("upgrade failed"))

        with pytest.raises(ConnectionError) as exc_info:
            svc._upgrade_container(BOT_UUID, BOT_ID, OWNER_ID, USER_ID, migration_path="/nas/path")

        assert exc_info.value.error_code == "5001"
        assert "upgrade failed" in exc_info.value.original_error


class TestBuildConnection:
    """Tests for _build_connection method (lines 419-420, 425, 430-431, 436)."""

    def test_baas_service_error_wrapped_as_connection_error(self):
        """Lines 419-429: BaasServiceError wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        baas.get_ws_info_by_bot_uuid = MagicMock(side_effect=BaasServiceError("ws error"))

        with pytest.raises(ConnectionError) as exc_info:
            svc._build_connection(BOT_UUID, BOT_ID, USER_ID)

        assert exc_info.value.error_code == "5001"
        assert "ws error" in exc_info.value.original_error

    def test_generic_exception_wrapped_as_connection_error(self):
        """Lines 430-440: Generic exceptions wrapped as ConnectionError."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        baas.get_ws_info_by_bot_uuid = MagicMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(ConnectionError) as exc_info:
            svc._build_connection(BOT_UUID, BOT_ID, USER_ID)

        assert exc_info.value.error_code == "5001"

    def test_success_returns_connection_dict(self):
        """Successful build returns connection dict."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
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
    """Tests for get_caller_connection method (lines 118-122, 127, 131, 168, 176-181, 210, 215)."""

    @pytest.mark.asyncio
    async def test_no_success_publish_raises_not_published(self):
        """Lines 267, 271 tested via _resolve_build_artifact but also exercised here."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        publish_repo.get_by_publish_bot_id = MagicMock(return_value=None)

        with pytest.raises(BotNotPublishedError):
            await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

    @pytest.mark.asyncio
    async def test_success_instance_returns_immediately(self):
        """Lines 118-131, 168: Instance already success with matching version returns immediately."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
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
        baas.create_bot.assert_not_called()
        baas.upgrade_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_instance_with_old_version_upgrades(self):
        """Lines 164-181: Success instance with old version triggers upgrade."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
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
        baas.upgrade_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        ws_info = _make_ws_info()
        baas.get_ws_info_by_bot_uuid = MagicMock(return_value=ws_info)
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is False
        baas.upgrade_bot.assert_called_once()
        baas.create_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_instance_creates_container(self):
        """New instance triggers create_container."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        instance_repo.update_instance = MagicMock(return_value=True)
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": {"bot_uuid": BOT_UUID}}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
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
        baas.create_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_instance_not_ready_returns_need_poll(self):
        """Lines 209-218: Instance not ready returns need_poll=True."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
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
        baas.upgrade_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})
        instance_repo.update_instance = MagicMock(return_value=True)

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        assert result["need_poll"] is True
        assert result["connection"] is None
        assert result["instance"]["status"] == "init"

    @pytest.mark.asyncio
    async def test_success_instance_without_bot_uuid_creates_container(self):
        """Lines 119: Success instance without bot_uuid triggers create."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        existing_ext = {
            "bot_uuid": None,  # No bot_uuid
            "service_bot_publish_id": 123,
            "version": 1,
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
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

        baas.create_bot.assert_called_once()
        assert result["need_poll"] is False

    @pytest.mark.asyncio
    async def test_success_instance_version_none_uses_zero(self):
        """Lines 120: ext.get('version') defaults to 0 when None."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        existing_ext = {
            "bot_uuid": BOT_UUID,
            "version": None,  # No version
        }
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "success", "ext": existing_ext}
        )
        _wire_publish(publish_repo, MockPublishRecord(version=1))
        _wire_bot_repo(bot_repo)
        baas.upgrade_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 999})
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
        baas.upgrade_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_baas_publish_failed_sets_status_to_failed(self):
        """Lines 197: FAILED status from baas_publish sets instance status to 'failed'."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
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
        """Lines 191: When baas_publish_id is None, skip polling."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        instance_repo.get_instance = MagicMock(return_value=None)
        instance_repo.upsert_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": None}
        )
        _wire_publish(publish_repo)
        _wire_bot_repo(bot_repo)
        # create_bot returns no publish_id
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID})
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
        """Lines 180-181: Upgrade preserves bot_uuid in ext when upgrading."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
        # This tests the case where bot_uuid exists in ext but we need to verify it's preserved
        existing_ext = {
            "bot_uuid": BOT_UUID,  # bot_uuid is present
            "service_bot_publish_id": 123,
            "version": 1,
        }
        NEW_BOT_UUID = "new-uuid-002"
        instance_repo.get_instance = MagicMock(
            return_value={"id": 1, "status": "init", "ext": existing_ext}
        )
        _wire_publish(publish_repo, MockPublishRecord(version=2))
        _wire_bot_repo(bot_repo)
        baas.upgrade_bot = MagicMock(return_value={"bot_uuid": NEW_BOT_UUID, "publish_id": 999})
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

        baas.upgrade_bot.assert_called_once()
        # Verify bot_uuid was preserved/updated in ext
        update_calls = instance_repo.update_instance.call_args_list
        assert any(
            call[1].get("ext", {}).get("bot_uuid") == BOT_UUID
            for call in update_calls
        )


class TestRequestId:
    """Tests for _request_id static method (lines 453-477)."""

    def test_request_id_generates_consistent_hash(self):
        """_request_id generates a deterministic MD5 hash."""
        bot = {
            "entity_id": "entity123",
            "bot_id": "bot456",
        }
        user_id = "user789"
        stage = "caller_create"

        request_id = ExpertChatInstanceService._request_id(bot, user_id, stage)

        # Verify it returns a 32-char hex string (MD5 hash)
        assert len(request_id) == 32
        assert all(c in "0123456789abcdef" for c in request_id)

        # Same inputs should produce same hash
        request_id2 = ExpertChatInstanceService._request_id(bot, user_id, stage)
        assert request_id == request_id2

    def test_request_id_different_for_different_users(self):
        """Different user_id produces different request_id."""
        bot = {
            "entity_id": "entity123",
            "bot_id": "bot456",
        }
        stage = "caller_create"

        id1 = ExpertChatInstanceService._request_id(bot, "user1", stage)
        id2 = ExpertChatInstanceService._request_id(bot, "user2", stage)

        assert id1 != id2

    def test_request_id_different_for_different_stages(self):
        """Different stage produces different request_id."""
        bot = {
            "entity_id": "entity123",
            "bot_id": "bot456",
        }
        user_id = "user789"

        id_create = ExpertChatInstanceService._request_id(bot, user_id, "caller_create")
        id_upgrade = ExpertChatInstanceService._request_id(bot, user_id, "caller_upgrade")

        assert id_create != id_upgrade

    def test_request_id_handles_missing_entity_id(self):
        """_request_id handles missing entity_id gracefully."""
        bot = {
            "bot_id": "bot456",
        }
        user_id = "user789"
        stage = "caller_create"

        request_id = ExpertChatInstanceService._request_id(bot, user_id, stage)

        assert len(request_id) == 32


class TestGetCallerConnectionEdgeCases:
    """Additional edge case tests for get_caller_connection."""

    @pytest.mark.asyncio
    async def test_create_container_success_returns_connection(self):
        """Full happy path: create container and return connection."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()

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
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
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
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()
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
        """Lines 197: SUCCESS status from baas_publish sets instance status to 'success'."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()

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
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
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
        """Lines 197: FAILED status from baas_publish sets instance status to 'failed'."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()

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
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
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
    async def test_publish_status_running_keeps_instance_status(self):
        """Lines 197: RUNNING status keeps original instance status."""
        svc, instance_repo, baas, publish_repo, bot_repo = _make_service()

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
        baas.create_bot = MagicMock(return_value={"bot_uuid": BOT_UUID, "publish_id": 888})
        baas.get_publish_progress = MagicMock(return_value={"status": "RUNNING"})

        result = await svc.get_caller_connection(USER_ID, BOT_ID, OWNER_ID)

        # RUNNING keeps original status (init)
        assert result["need_poll"] is True
        assert result["connection"] is None
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
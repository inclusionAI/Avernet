"""Tests for bot_service.update_bot BCN sync integration.

Tests that bot name/summary updates are synced to BCN.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from agentclaw.community.core.bot_management.services.bot_service import BotService


def _make_bot_service(repository=None, device_service=None, bcn_service=None) -> BotService:
    """Build a BotService with MagicMock fallbacks for the injected deps
    not under test (DI ctor requires all of them)."""
    return BotService(
        drm_reader=MagicMock(),
        repository=repository or Mock(),
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=bcn_service or MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=(lambda: device_service if device_service is not None else MagicMock()),
        path_factory=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


class TestUpdateBotBcnSync:
    """Tests for BCN sync during bot update."""

    @pytest.fixture
    def mock_repository(self):
        """Create a mock bot repository."""
        repo = Mock()
        repo.get_by_id_and_owner.return_value = {
            "id": 1,
            "bot_id": "test-bot-id",
            "bot_name": "Old Name",
            "bot_desc": "Old Description",
            "owner_id": "85020",
            "owner_name": "Test User",
            "status": "ACTIVE",
            "binding_id": None,
            "ext": {},
        }
        repo.update_by_owner.return_value = {
            "id": 1,
            "bot_id": "test-bot-id",
            "bot_name": "New Name",
            "bot_desc": "New Description",
            "owner_id": "85020",
            "owner_name": "Test User",
            "status": "ACTIVE",
            "binding_id": None,
            "ext": {},
        }
        # get_by_bot_name returns None by default (no existing bot with that name)
        repo.get_by_bot_name.return_value = None
        return repo

    @pytest.fixture
    def mock_device_service(self):
        """Create a mock device service."""
        return Mock()

    def test_update_bot_syncs_to_bcn_on_name_change(
        self, mock_repository, mock_device_service
    ):
        """Test that updating bot_name triggers BCN sync."""
        service = _make_bot_service(repository=mock_repository, device_service=mock_device_service)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                bot_name="New Name",
            )

            # Verify BCN sync was called
            mock_sync.assert_called_once_with(
                bot_id="test-bot-id",
                owner_id="85020",
                bot_name="New Name",
                bot_desc=None,
            )

    def test_update_bot_syncs_to_bcn_on_desc_change(
        self, mock_repository, mock_device_service
    ):
        """Test that updating bot_desc triggers BCN sync."""
        service = _make_bot_service(repository=mock_repository, device_service=mock_device_service)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                bot_desc="New Description",
            )

            # Verify BCN sync was called
            mock_sync.assert_called_once_with(
                bot_id="test-bot-id",
                owner_id="85020",
                bot_name=None,
                bot_desc="New Description",
            )

    def test_update_bot_syncs_to_bcn_on_both_changes(
        self, mock_repository, mock_device_service
    ):
        """Test that updating both name and desc triggers BCN sync."""
        service = _make_bot_service(repository=mock_repository, device_service=mock_device_service)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                bot_name="New Name",
                bot_desc="New Description",
            )

            # Verify BCN sync was called
            mock_sync.assert_called_once_with(
                bot_id="test-bot-id",
                owner_id="85020",
                bot_name="New Name",
                bot_desc="New Description",
            )

    def test_update_bot_forwards_request_headers_to_sync(
        self, mock_repository, mock_device_service
    ):
        """Test that update_bot passes request headers into BCN sync."""
        service = _make_bot_service(repository=mock_repository, device_service=mock_device_service)
        request_headers = {
            "cookie": "IAM_TOKEN=test-token",
            "authorization": "Bearer opaque-or-jwt-token",
        }

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                bot_name="New Name",
                request_headers=request_headers,
            )

            # Verify BCN sync was called
            mock_sync.assert_called_once_with(
                bot_id="test-bot-id",
                owner_id="85020",
                bot_name="New Name",
                bot_desc=None,
                request_headers=request_headers,
            )

    def test_update_bot_no_sync_when_no_name_or_desc_change(
        self, mock_repository, mock_device_service
    ):
        """Test that BCN sync is not triggered when only other fields change."""
        service = _make_bot_service(repository=mock_repository, device_service=mock_device_service)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                ext={"avatar_url": "https://example.com/avatar.png"},
            )

            # Verify BCN sync was NOT called
            mock_sync.assert_not_called()

    def test_update_bot_sync_to_bcn_can_be_disabled(
        self, mock_repository, mock_device_service
    ):
        """Test that BCN sync can be disabled via sync_to_bcn=False."""
        service = _make_bot_service(repository=mock_repository, device_service=mock_device_service)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                bot_name="New Name",
                sync_to_bcn=False,  # Disable BCN sync
            )

            # Verify BCN sync was NOT called
            mock_sync.assert_not_called()


class TestSyncBotToBcn:
    """Tests for _sync_bot_to_bcn method."""

    @pytest.fixture
    def bot_service(self):
        """Create a BotService instance with mocked repository."""
        return _make_bot_service()

    def test_sync_bot_to_bcn_formats_bot_id_correctly(
        self, bot_service
    ):
        """Test that BCN bot_id is formatted as {bot_id}:{owner_id}."""
        mock_bcn_service = Mock()
        bot_service._bcn_service = mock_bcn_service

        mock_repo = Mock()
        mock_repo.get_by_id_and_owner.return_value = {
            "bot_id": "20260421_gfdsz5vi",
            "bot_name": "Test Bot",
            "bot_desc": "Test Description",
            "owner_id": "85020",
        }
        bot_service._repository = mock_repo

        bot_service._sync_bot_to_bcn(
            bot_id="20260421_gfdsz5vi",
            owner_id="85020",
            bot_name="New Bot Name",
            bot_desc="New Description",
        )

        # Verify BCN onboard was called with correct bot_id format
        mock_bcn_service.onboard_bot.assert_called_once_with(
            bot_id="20260421_gfdsz5vi:85020",
            name="New Bot Name",
            summary="New Description",
        )

    def test_sync_bot_to_bcn_uses_existing_values_when_none(
        self, bot_service
    ):
        """Test that existing values are used when new values are None."""
        mock_bcn_service = Mock()
        bot_service._bcn_service = mock_bcn_service

        mock_repo = Mock()
        mock_repo.get_by_id_and_owner.return_value = {
            "bot_id": "test-bot",
            "bot_name": "Existing Name",
            "bot_desc": "Existing Description",
            "owner_id": "85020",
        }
        bot_service._repository = mock_repo

        # Only name is updated, desc is None
        bot_service._sync_bot_to_bcn(
            bot_id="test-bot",
            owner_id="85020",
            bot_name="New Name",
            bot_desc=None,
        )

        mock_bcn_service.onboard_bot.assert_called_once_with(
            bot_id="test-bot:85020",
            name="New Name",
            summary="Existing Description",  # Uses existing description
        )

    def test_sync_bot_to_bcn_forwards_request_headers(
        self, bot_service
    ):
        """Test that request headers are forwarded to BCN onboard."""
        mock_bcn_service = Mock()
        bot_service._bcn_service = mock_bcn_service

        mock_repo = Mock()
        mock_repo.get_by_id_and_owner.return_value = {
            "bot_id": "test-bot",
            "bot_name": "Existing Name",
            "bot_desc": "Existing Description",
            "owner_id": "85020",
        }
        bot_service._repository = mock_repo

        request_headers = {
            "cookie": "IAM_TOKEN=test-token",
            "authorization": "Bearer opaque-or-jwt-token",
        }

        bot_service._sync_bot_to_bcn(
            bot_id="test-bot",
            owner_id="85020",
            request_headers=request_headers,
        )

        mock_bcn_service.onboard_bot.assert_called_once_with(
            bot_id="test-bot:85020",
            name="Existing Name",
            summary="Existing Description",
            request_headers=request_headers,
        )

    def test_sync_bot_to_bcn_handles_bcn_error(
        self, bot_service
    ):
        """Test that BCN errors are caught and logged, not raised."""
        from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

        mock_bcn_service = Mock()
        mock_bcn_service.onboard_bot.side_effect = BcnServiceError("BCN connection failed")
        bot_service._bcn_service = mock_bcn_service

        mock_repo = Mock()
        mock_repo.get_by_id_and_owner.return_value = {
            "bot_id": "test-bot",
            "bot_name": "Test Bot",
            "bot_desc": "Test Description",
            "owner_id": "85020",
        }
        bot_service._repository = mock_repo

        # Should not raise, just log warning
        bot_service._sync_bot_to_bcn(
            bot_id="test-bot",
            owner_id="85020",
            bot_name="New Name",
            bot_desc="New Description",
        )

        # Verify onboard was called even though it failed
        mock_bcn_service.onboard_bot.assert_called_once()

    def test_sync_bot_to_bcn_handles_bot_not_found(
        self, bot_service
    ):
        """Test that missing bot is handled gracefully."""
        mock_bcn_service = Mock()
        bot_service._bcn_service = mock_bcn_service

        mock_repo = Mock()
        mock_repo.get_by_id_and_owner.return_value = None  # Bot not found
        bot_service._repository = mock_repo

        # Should not raise, just log warning and return
        bot_service._sync_bot_to_bcn(
            bot_id="nonexistent-bot",
            owner_id="85020",
            bot_name="New Name",
            bot_desc="New Description",
        )

        # BCN service should not be called
        mock_bcn_service.onboard_bot.assert_not_called()

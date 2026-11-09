"""Tests for DefaultPublishAdminService.restore_bot.

Covers success mapping, not-found, conflict propagation, the audit log, and
cross-env behavior (no get_current_env). Mock builders shared with the other
admin-service tests live in ``_admin_test_fixtures``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.publish_manage import (
    PublishConflictError,
    PublishNotFoundError,
    RestoreBotResult,
)
from secbaas.community.core.repository.bot import (
    BotRecordConflictError,
    RestoredBotRecord,
)
from tests.unit.core.service.publish_manage._admin_test_fixtures import (
    _make_mock_bot,
    _make_restored_snapshot,
    _make_service,
)


class TestRestoreBot:
    """DefaultPublishAdminService.restore_bot."""

    async def test_restore_bot_happy_path_maps_result(self):
        snapshot = _make_restored_snapshot(
            bot_id=7,
            bot_uuid="bot-abc-123",
            previous_status="PENDING",
            previous_is_deleted=7,
            retired_sibling_count=2,
        )
        bot_repo = MagicMock()
        bot_repo.restore_bot_record_by_id.return_value = snapshot

        service = _make_service(bot_repo=bot_repo)

        result = await service.restore_bot(bot_id=7, operator="ops.alice")

        assert isinstance(result, RestoreBotResult)
        assert result.bot_id == 7
        assert result.bot_uuid == "bot-abc-123"
        assert result.previous_status == "PENDING"
        assert result.previous_is_deleted == 7
        assert result.new_status == "ACTIVE"
        assert result.retired_sibling_count == 2

    async def test_restore_bot_forwards_operator_as_modifier(self):
        snapshot = _make_restored_snapshot()
        bot_repo = MagicMock()
        bot_repo.restore_bot_record_by_id.return_value = snapshot

        service = _make_service(bot_repo=bot_repo)

        await service.restore_bot(bot_id=7, operator="ops.bob")

        bot_repo.restore_bot_record_by_id.assert_called_once_with(7, modifier="ops.bob")

    async def test_restore_bot_not_found_raises_publish_not_found(self):
        bot_repo = MagicMock()
        bot_repo.restore_bot_record_by_id.return_value = None

        service = _make_service(bot_repo=bot_repo)

        with pytest.raises(PublishNotFoundError) as exc_info:
            await service.restore_bot(bot_id=999, operator="ops.alice")

        assert "999" in str(exc_info.value)
        bot_repo.restore_bot_record_by_id.assert_called_once_with(
            999, modifier="ops.alice"
        )

    async def test_restore_bot_conflict_translated_to_publish_conflict(self):
        """Repo BotRecordConflictError must surface, not be swallowed.

        Translated into the api-layer conflict error at the service
        boundary (adapters are banned from importing core exceptions).
        """
        bot_repo = MagicMock()
        bot_repo.restore_bot_record_by_id.side_effect = BotRecordConflictError(
            "live unique-key slot conflict"
        )

        service = _make_service(bot_repo=bot_repo)

        with pytest.raises(PublishConflictError) as exc_info:
            await service.restore_bot(bot_id=7, operator="ops.alice")

        assert "7" in exc_info.value.message
        assert "conflicts" in exc_info.value.message
        assert isinstance(exc_info.value.__cause__, BotRecordConflictError)

    async def test_restore_bot_emits_audit_log(self):
        snapshot = _make_restored_snapshot(
            bot_id=7,
            bot_uuid="bot-abc-123",
            previous_status="PENDING",
            retired_sibling_count=2,
        )
        bot_repo = MagicMock()
        bot_repo.restore_bot_record_by_id.return_value = snapshot

        service = _make_service(bot_repo=bot_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.logger"
        ) as mock_logger:
            await service.restore_bot(bot_id=7, operator="ops.alice")

        mock_logger.warning.assert_called_once_with(
            "ADMIN_RESTORE_BOT: operator=%s bot_id=%s bot_uuid=%s %s->ACTIVE, retired=%s",
            "ops.alice",
            7,
            "bot-abc-123",
            "PENDING",
            2,
        )

    async def test_restore_bot_cross_env_does_not_call_get_current_env(self):
        snapshot = _make_restored_snapshot()
        bot_repo = MagicMock()
        bot_repo.restore_bot_record_by_id.return_value = snapshot

        service = _make_service(bot_repo=bot_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.get_current_env"
        ) as mock_get_env:
            await service.restore_bot(bot_id=7, operator="ops.alice")

        mock_get_env.assert_not_called()


class TestRestoreBotByUuid:
    """restore_bot(bot_uuid=...): resolution then the shared by-id restore."""

    def _make_generation(self, id: int) -> MagicMock:
        record = MagicMock()
        record.id = id
        return record

    async def test_resolves_via_rel_max_and_restores_resolved_id(self):
        records = [self._make_generation(30), self._make_generation(10)]
        bot_repo = MagicMock()
        bot_repo.list_by_bot_uuid_only_including_deleted.return_value = records
        snapshot = _make_restored_snapshot(bot_id=30)
        bot_repo.restore_bot_record_by_id.return_value = snapshot
        rel_repo = MagicMock()
        rel_repo.max_live_bot_id_for_bot_ids.return_value = 30

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        result = await service.restore_bot(bot_uuid="bot-abc-123", operator="ops.alice")

        bot_repo.list_by_bot_uuid_only_including_deleted.assert_called_once_with(
            "bot-abc-123"
        )
        rel_repo.max_live_bot_id_for_bot_ids.assert_called_once_with([30, 10])
        bot_repo.restore_bot_record_by_id.assert_called_once_with(
            30, modifier="ops.alice"
        )

        assert isinstance(result, RestoreBotResult)
        assert result.bot_id == 30
        assert result.bot_uuid == "bot-abc-123"
        assert result.new_status == "ACTIVE"

    async def test_unknown_uuid_raises_publish_not_found(self):
        bot_repo = MagicMock()
        bot_repo.list_by_bot_uuid_only_including_deleted.return_value = []
        rel_repo = MagicMock()

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        with pytest.raises(PublishNotFoundError) as exc_info:
            await service.restore_bot(bot_uuid="bot-none", operator="ops.alice")

        assert "No bot records found for bot_uuid=bot-none" in str(exc_info.value)
        rel_repo.max_live_bot_id_for_bot_ids.assert_not_called()
        bot_repo.restore_bot_record_by_id.assert_not_called()

    async def test_uuid_without_live_rel_raises_publish_not_found(self):
        records = [self._make_generation(7)]
        bot_repo = MagicMock()
        bot_repo.list_by_bot_uuid_only_including_deleted.return_value = records
        rel_repo = MagicMock()
        rel_repo.max_live_bot_id_for_bot_ids.return_value = None

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        with pytest.raises(PublishNotFoundError) as exc_info:
            await service.restore_bot(bot_uuid="bot-no-rel", operator="ops.alice")

        assert "No non-deleted device relation found for bot_uuid=bot-no-rel" in str(
            exc_info.value
        )
        bot_repo.restore_bot_record_by_id.assert_not_called()

    async def test_both_selectors_raises_value_error(self):
        service = _make_service()

        with pytest.raises(ValueError, match="exactly one of bot_id or bot_uuid"):
            await service.restore_bot(
                bot_id=7, bot_uuid="bot-abc-123", operator="ops.alice"
            )

    async def test_neither_selector_raises_value_error(self):
        service = _make_service()

        with pytest.raises(ValueError, match="exactly one of bot_id or bot_uuid"):
            await service.restore_bot(operator="ops.alice")

    async def test_uuid_path_emits_audit_log_with_resolved_id(self):
        records = [self._make_generation(30), self._make_generation(10)]
        bot_repo = MagicMock()
        bot_repo.list_by_bot_uuid_only_including_deleted.return_value = records
        snapshot = _make_restored_snapshot(
            bot_id=30,
            bot_uuid="bot-abc-123",
            previous_status="PENDING",
            retired_sibling_count=1,
        )
        bot_repo.restore_bot_record_by_id.return_value = snapshot
        rel_repo = MagicMock()
        rel_repo.max_live_bot_id_for_bot_ids.return_value = 30

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.logger"
        ) as mock_logger:
            await service.restore_bot(bot_uuid="bot-abc-123", operator="ops.alice")

        mock_logger.warning.assert_called_once_with(
            "ADMIN_RESTORE_BOT: operator=%s bot_id=%s bot_uuid=%s %s->ACTIVE, retired=%s",
            "ops.alice",
            30,
            "bot-abc-123",
            "PENDING",
            1,
        )

    async def test_uuid_path_does_not_call_get_current_env(self):
        records = [self._make_generation(30)]
        bot_repo = MagicMock()
        bot_repo.list_by_bot_uuid_only_including_deleted.return_value = records
        bot_repo.restore_bot_record_by_id.return_value = _make_restored_snapshot(
            bot_id=30
        )
        rel_repo = MagicMock()
        rel_repo.max_live_bot_id_for_bot_ids.return_value = 30

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.get_current_env"
        ) as mock_get_env:
            await service.restore_bot(bot_uuid="bot-abc-123", operator="ops.alice")

        mock_get_env.assert_not_called()


class TestRestoreBotResult:
    """RestoreBotResult dataclass contract."""

    def test_construction(self):
        result = RestoreBotResult(
            bot_id=7,
            bot_uuid="bot-abc-123",
            previous_status="PENDING",
            previous_is_deleted=7,
            new_status="ACTIVE",
            retired_sibling_count=2,
        )
        assert result.bot_id == 7
        assert result.bot_uuid == "bot-abc-123"
        assert result.previous_status == "PENDING"
        assert result.previous_is_deleted == 7
        assert result.new_status == "ACTIVE"
        assert result.retired_sibling_count == 2

    def test_construction_with_converged_state(self):
        result = RestoreBotResult(
            bot_id=42,
            bot_uuid="abc-def-456",
            previous_status="ACTIVE",
            previous_is_deleted=0,
            new_status="ACTIVE",
            retired_sibling_count=0,
        )
        assert result.previous_is_deleted == 0
        assert result.retired_sibling_count == 0

"""Unit tests for device_lifecycle: reallocate_orphaned_bots desktop-bot filtering."""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from agentclaw.community.plugins.local import device_lifecycle
from agentclaw.community.plugins.local.device_lifecycle import (
    reallocate_orphaned_bots,
    release_all_stale_bindings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBotModel:
    """Minimal stand-in for BotModel ORM object used by reallocate_orphaned_bots."""

    def __init__(
        self,
        bot_id: str = "bot001",
        owner_id: str = "user001",
        entity_id: str = "staff_user001",
        entity_type: str = "staff",
        bot_name: str = "TestBot",
        active_engine: str = "openclaw",
        bot_type: str = "personal",
    ):
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.bot_name = bot_name
        self.active_engine = active_engine
        self.bot_type = bot_type


def _database_with_rows(rows):
    database = MagicMock()
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    database.session.return_value.__enter__.return_value = db
    database.orm_session.return_value.__enter__.return_value = db
    return database, db


def test_production_module_has_no_unmanaged_session_helper_bypass():
    source = inspect.getsource(device_lifecycle)
    assert "SessionLocal" not in source
    assert "get_session" not in source


def test_release_stale_bindings_uses_database_plugin_context():
    database, _db = _database_with_rows([])

    release_all_stale_bindings(database)

    database.orm_session.assert_called_once_with()


# ===========================================================================
# reallocate_orphaned_bots — desktop bot filtering
# ===========================================================================


class TestReallocateOrphanedBotsDesktopFilter:
    """reallocate_orphaned_bots must skip desktop bots."""

    def test_skips_desktop_bots(self):
        """Desktop bots should not be passed to _reallocate_bots."""
        personal_bot = _FakeBotModel(bot_id="personal_1", bot_type="personal")
        desktop_bot = _FakeBotModel(bot_id="desktop_1", bot_type="desktop")
        service_bot = _FakeBotModel(bot_id="service_1", bot_type="service")

        database, _db = _database_with_rows([
            personal_bot,
            desktop_bot,
            service_bot,
        ])

        mock_bot_service = MagicMock()
        with patch(
            "agentclaw.community.plugins.local.device_lifecycle._reallocate_bots"
        ) as mock_reallocate:
            reallocate_orphaned_bots(database, mock_bot_service)

        # _reallocate_bots should only receive personal and service bots
        mock_reallocate.assert_called_once()
        snapshots = mock_reallocate.call_args[0][0]
        bot_ids = [s["bot_id"] for s in snapshots]
        assert "personal_1" in bot_ids
        assert "service_1" in bot_ids
        assert "desktop_1" not in bot_ids

    def test_only_desktop_bots_results_in_empty_snapshots(self):
        """If all orphaned bots are desktop type, no allocation happens."""
        desktop_bot_1 = _FakeBotModel(bot_id="desktop_a", bot_type="desktop")
        desktop_bot_2 = _FakeBotModel(bot_id="desktop_b", bot_type="desktop")

        database, _db = _database_with_rows([
            desktop_bot_1,
            desktop_bot_2,
        ])

        mock_bot_service = MagicMock()
        with patch(
            "agentclaw.community.plugins.local.device_lifecycle._reallocate_bots"
        ) as mock_reallocate:
            reallocate_orphaned_bots(database, mock_bot_service)

        mock_reallocate.assert_called_once_with([], mock_bot_service)

    def test_no_orphaned_bots(self):
        """No orphaned bots means no allocation."""
        database, _db = _database_with_rows([])

        mock_bot_service = MagicMock()
        with patch(
            "agentclaw.community.plugins.local.device_lifecycle._reallocate_bots"
        ) as mock_reallocate:
            reallocate_orphaned_bots(database, mock_bot_service)

        # _reallocate_bots should not be called when there are no orphans
        mock_reallocate.assert_not_called()

    def test_snapshot_fields_preserved(self):
        """Snapshot dict should preserve all fields for non-desktop bots."""
        bot = _FakeBotModel(
            bot_id="my_bot",
            owner_id="owner_123",
            entity_id="staff_456",
            entity_type="staff",
            bot_name="MyBot",
            active_engine="moltis",
            bot_type="personal",
        )

        database, _db = _database_with_rows([bot])

        mock_bot_service = MagicMock()
        with patch(
            "agentclaw.community.plugins.local.device_lifecycle._reallocate_bots"
        ) as mock_reallocate:
            reallocate_orphaned_bots(database, mock_bot_service)

        mock_reallocate.assert_called_once()
        snapshots = mock_reallocate.call_args[0][0]
        assert len(snapshots) == 1
        s = snapshots[0]
        assert s["bot_id"] == "my_bot"
        assert s["owner_id"] == "owner_123"
        assert s["entity_id"] == "staff_456"
        assert s["entity_type"] == "staff"
        assert s["bot_name"] == "MyBot"
        assert s["active_engine"] == "moltis"

    def test_read_context_closes_before_reallocation(self):
        bot = _FakeBotModel()
        database, _db = _database_with_rows([bot])
        context_active = False

        def enter_context():
            nonlocal context_active
            context_active = True
            return _db

        def exit_context(*_args):
            nonlocal context_active
            context_active = False

        database.session.return_value.__enter__.side_effect = enter_context
        database.session.return_value.__exit__.side_effect = exit_context

        def assert_context_closed(_snapshots, _bot_service):
            assert context_active is False

        with patch(
            "agentclaw.community.plugins.local.device_lifecycle._reallocate_bots",
            side_effect=assert_context_closed,
        ):
            reallocate_orphaned_bots(database, MagicMock())

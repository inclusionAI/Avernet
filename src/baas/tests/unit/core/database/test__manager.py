"""DatabaseManager unit tests — plugin-delegated model.

Tests the simplified DatabaseManager that delegates all operations
to a registered DataSourcePlugin. Uses MagicMock, no real DB.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.database import DatabaseManager, db_manager


@pytest.fixture
def fresh_manager():
    """Return a clean singleton — reset class-level state."""
    DatabaseManager._instance = None
    DatabaseManager._plugin = None
    DatabaseManager._scoped_sessions = []
    return DatabaseManager()


class TestNewSingleton:
    def test_new_returns_same_instance(self, fresh_manager):
        mgr2 = DatabaseManager()
        assert mgr2 is fresh_manager

    def test_new_does_not_create_duplicate(self, fresh_manager):
        first = DatabaseManager.__new__(DatabaseManager)
        second = DatabaseManager()
        assert first is second


class TestInitPlugin:
    def test_sets_plugin(self, fresh_manager):
        plugin = MagicMock()
        fresh_manager.init_plugin(plugin)
        assert fresh_manager._plugin is plugin

    def test_is_initialized_true_after_init_plugin(self, fresh_manager):
        assert fresh_manager.is_initialized is False
        fresh_manager.init_plugin(MagicMock())
        assert fresh_manager.is_initialized is True


class TestSession:
    def test_delegates_to_plugin(self, fresh_manager):
        fake_conn = MagicMock()
        plugin = MagicMock()
        plugin.sync_connection.return_value.__enter__ = MagicMock(
            return_value=fake_conn
        )
        plugin.sync_connection.return_value.__exit__ = MagicMock(return_value=False)
        fresh_manager.init_plugin(plugin)

        with fresh_manager.session("ds") as conn:
            assert conn is fake_conn
        plugin.sync_connection.assert_called_once_with("ds")

    def test_raises_when_no_plugin(self, fresh_manager):
        with pytest.raises(RuntimeError, match="not initialized"):
            with fresh_manager.session("ds"):
                pass


class TestGetSession:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, fresh_manager):
        fake_session = MagicMock()
        plugin = MagicMock()

        async def _fake_session():
            yield fake_session

        plugin.session = _fake_session
        fresh_manager.init_plugin(plugin)

        async with fresh_manager.get_session() as session:
            assert session is fake_session

    @pytest.mark.asyncio
    async def test_raises_when_no_plugin(self, fresh_manager):
        with pytest.raises(RuntimeError, match="not initialized"):
            async with fresh_manager.get_session():
                pass


class TestOrmSession:
    def test_delegates_to_plugin(self, fresh_manager):
        fake_session = MagicMock()
        plugin = MagicMock()
        plugin.orm_session.return_value.__enter__ = MagicMock(return_value=fake_session)
        plugin.orm_session.return_value.__exit__ = MagicMock(return_value=False)
        fresh_manager.init_plugin(plugin)

        with fresh_manager.orm_session() as session:
            assert session is fake_session

    def test_raises_when_no_plugin(self, fresh_manager):
        with pytest.raises(RuntimeError, match="not initialized"):
            with fresh_manager.orm_session():
                pass


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_plugin(self, fresh_manager):
        plugin = MagicMock()
        plugin.close = AsyncMock()
        fresh_manager.init_plugin(plugin)

        await fresh_manager.close()
        plugin.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_plugin_no_error(self, fresh_manager):
        await fresh_manager.close()


class TestGlobalDbManager:
    def test_global_singleton(self):
        mgr_a = db_manager
        mgr_b = db_manager
        assert mgr_a is mgr_b

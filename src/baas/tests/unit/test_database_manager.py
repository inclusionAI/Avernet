"""Unit tests for DatabaseManager — connection lifecycle, transactions, pool exhaustion."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.database._manager import DatabaseManager

pytestmark = [pytest.mark.unit]


class TestDatabaseManagerSingleton:
    def test_singleton_returns_same_instance(self) -> None:
        dm1 = DatabaseManager()
        dm2 = DatabaseManager()
        assert dm1 is dm2


class TestDatabaseManagerPluginInit:
    def test_init_plugin_sets_plugin_reference(self) -> None:
        dm = DatabaseManager()
        plugin = MagicMock()
        plugin.session = MagicMock()
        plugin.sync_connection = MagicMock()
        plugin.orm_session = MagicMock()

        dm.init_plugin(plugin)

        assert dm._plugin is plugin

    def test_init_plugin_without_session_interface(self) -> None:
        dm = DatabaseManager()
        plugin = MagicMock(spec=[])

        dm.init_plugin(plugin)

        assert dm._plugin is plugin

    def test_sync_session_delegates_to_plugin(self) -> None:
        dm = DatabaseManager()
        plugin = MagicMock()
        mock_conn = MagicMock()
        plugin.sync_connection.return_value.__enter__.return_value = mock_conn

        dm.init_plugin(plugin)

        with dm.session("default") as conn:
            assert conn is mock_conn

        plugin.sync_connection.assert_called_once_with("default")

    @pytest.mark.asyncio
    async def test_async_session_delegates_to_plugin(self) -> None:
        dm = DatabaseManager()
        plugin = MagicMock()
        mock_session = MagicMock()

        async def _fake_session():
            yield mock_session

        plugin.session = _fake_session

        dm.init_plugin(plugin)

        async with dm.get_session() as s:
            assert s is mock_session

    @pytest.mark.asyncio
    async def test_close_delegates_to_plugin(self) -> None:
        dm = DatabaseManager()
        plugin = MagicMock()
        plugin.close = AsyncMock()

        dm.init_plugin(plugin)
        await dm.close()

        plugin.close.assert_called_once()


class TestDatabaseManagerUrlInit:
    def test_init_from_config_disabled(self) -> None:
        dm = DatabaseManager()
        dm._plugin = None

        dm.init_from_config({"enabled": False})

        assert dm._engine is None

    def test_init_from_config_no_datasources(self) -> None:
        dm = DatabaseManager()
        dm._plugin = None

        dm.init_from_config({"enabled": True, "datasources": []})

        assert dm._engine is None

    def test_session_raises_when_not_initialized(self) -> None:
        dm = DatabaseManager()
        dm._plugin = None
        dm._connection_factory = None

        with pytest.raises(RuntimeError):
            with dm.session("default"):
                pass

    @pytest.mark.asyncio
    async def test_get_session_raises_when_not_initialized(self) -> None:
        dm = DatabaseManager()
        dm._plugin = None
        dm._session_factory = None

        with pytest.raises(RuntimeError):
            async with dm.get_session():
                pass


class TestScopedOrmSession:
    def test_scoped_orm_session_isolates_sessions(self) -> None:
        dm = DatabaseManager()
        plugin = MagicMock()
        mock_session = MagicMock()
        plugin.orm_session.return_value.__enter__.return_value = mock_session

        dm.init_plugin(plugin)
        dm._scoped_sessions = []

        with dm.scoped_orm_session() as s:
            assert s is mock_session
            assert len(dm._scoped_sessions) == 1

        assert len(dm._scoped_sessions) == 0

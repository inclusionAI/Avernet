import pytest

from gateway.community.plugins.database.bare._plugin import BareDatabasePlugin
from gateway.community.spi.database import DataSourcePlugin


class DataSourcePluginContract:
    plugin: DataSourcePlugin

    def test_create_all_runs_without_error(self) -> None:
        self.plugin.create_all()

    def test_orm_session_context_manager(self) -> None:
        with self.plugin.orm_session() as session:
            from sqlalchemy.orm import Session

            assert isinstance(session, Session)

    def test_seed_runs_without_error(self) -> None:
        from unittest.mock import MagicMock

        self.plugin.seed(MagicMock())

    @pytest.mark.asyncio
    async def test_close_disposes_engine(self) -> None:
        self.plugin.create_all()
        await self.plugin.close()

    def test_init_database_defaults_to_memory(self) -> None:
        from unittest.mock import MagicMock

        mock_config = MagicMock()
        mock_config.db_url = ""
        mock_config.plugin_type = "SQLITE_ORM"
        self.plugin.init_database(mock_config)


class TestBareDatabasePlugin(DataSourcePluginContract):
    def setup_method(self) -> None:
        self.plugin = BareDatabasePlugin()

    def test_sync_connection_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            self.plugin.sync_connection("default")

    def test_orm_session_commits(self) -> None:
        self.plugin.create_all()
        with self.plugin.orm_session() as session:
            from sqlalchemy import text

            session.execute(text("SELECT 1"))

    @pytest.mark.asyncio
    async def test_close_disposes_engine(self) -> None:
        try:
            await super().test_close_disposes_engine()
        except ValueError as e:
            if "greenlet" in str(e):
                pytest.skip("greenlet not installed — async engine unavailable")

    @pytest.mark.asyncio
    async def test_async_session_context_manager(self) -> None:
        try:
            self.plugin.create_all()
            async with self.plugin.session() as session:
                from sqlalchemy.ext.asyncio import AsyncSession

                assert isinstance(session, AsyncSession)
        except ValueError as e:
            if "greenlet" in str(e):
                pytest.skip("greenlet not installed — async session unavailable")

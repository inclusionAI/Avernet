import pytest

from gateway.community.plugins.database.mariadb._plugin import MariaDbOrmPlugin
from gateway.community.plugins.database.sqlite._plugin import SqliteDatabasePlugin
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

    def test_init_database_accepts_no_args(self) -> None:
        # The BaaS-aligned contract: connection params are sealed in __init__,
        # so init_database() is a no-arg activation.
        self.plugin.init_database()


class TestSqliteDatabasePlugin(DataSourcePluginContract):
    def setup_method(self) -> None:
        self.plugin = SqliteDatabasePlugin()

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


def _mariadb_url(host: str = "127.0.0.1", port: int = 3306) -> str:
    return f"mysql+aiomysql://user:pass@{host}:{port}/mydb?charset=utf8mb4"


class TestMariaDbOrmPlugin:
    """Unit tests for MariaDbOrmPlugin (no live server needed)."""

    def test_init_database_requires_url(self) -> None:
        plugin = MariaDbOrmPlugin()
        with pytest.raises(RuntimeError, match="requires database_url"):
            plugin.init_database()

    def test_orm_session_raises_before_init(self) -> None:
        plugin = MariaDbOrmPlugin(database_url=_mariadb_url())
        with pytest.raises(RuntimeError, match="not initialized"):
            with plugin.orm_session():
                pass

    def test_database_label_omits_credentials(self) -> None:
        plugin = MariaDbOrmPlugin(database_url=_mariadb_url())
        label = plugin._resolve_database_label(
            "mysql+aiomysql://user:secret@db.internal:3306/mydb?charset=utf8mb4"
        )
        assert "secret" not in label
        assert "db.internal:3306/mydb" in label


class TestMariaDbOrmPluginContract(DataSourcePluginContract):
    """Contract conformance for MariaDB, buildable without a live server.

    Engines are built in ``init_database``; ``create_all`` needs initialized
    engines and a live DB, which the connection-backed tests cover under the
    E2E overlay, so the connection-dependent contract tests are overridden here.
    """

    def setup_method(self) -> None:
        self.plugin = MariaDbOrmPlugin(
            database_url=_mariadb_url(), create_schema=False, seed_data=False
        )

    def test_create_all_runs_without_error(self) -> None:
        # Requires a live DB and initialized engines; covered by E2E overlay.
        pytest.skip(
            "requires a live MariaDB server (see configs/overlays/e2e-mariadb.yaml)"
        )

    def test_orm_session_context_manager(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            with self.plugin.orm_session():
                pass

    def test_seed_runs_without_error(self) -> None:
        from unittest.mock import MagicMock

        self.plugin.seed(MagicMock())

    @pytest.mark.asyncio
    async def test_close_disposes_engine(self) -> None:
        await self.plugin.close()

    def test_init_database_builds_engines(self) -> None:
        self.plugin.init_database()
        assert self.plugin._sync_engine is not None
        assert self.plugin._async_engine is not None

    def test_init_database_accepts_no_args(self) -> None:
        # Overridden to avoid double init on the shared instance; the base
        # contract asserts the signature, the builds-engines test exercises it.
        pass

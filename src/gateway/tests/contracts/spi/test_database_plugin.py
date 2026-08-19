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

    def test_init_database_defaults_to_memory(self) -> None:
        from unittest.mock import MagicMock

        mock_config = MagicMock()
        mock_config.db_url = ""
        mock_config.plugin_type = "SQLITE_ORM"
        self.plugin.init_database(mock_config)


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


def _make_mariadb_config(host: str = "127.0.0.1", port: int = 3306):
    from gateway.community.bootstrap import DatabaseConfig
    from gateway.community.spi.database import PluginDatabaseType

    return DatabaseConfig(
        plugin_type=PluginDatabaseType.MARIADB_ORM,
        mariadb_host=host,
        mariadb_port=port,
        mariadb_database="mydb",
        mariadb_user="user",
        mariadb_password="pass",
    )


def _mariadb_config_no_db(db_url: str = "sqlite:////tmp/x.db"):
    from gateway.community.bootstrap import DatabaseConfig
    from gateway.community.spi.database import PluginDatabaseType

    return DatabaseConfig(
        plugin_type=PluginDatabaseType.MARIADB_ORM,
        db_url=db_url,
        create_schema=False,
        mariadb_host="127.0.0.1",
        mariadb_port=3306,
        mariadb_database="",
        mariadb_user="",
        mariadb_password="",
    )


class TestMariaDbOrmPluginUrlResolution:
    """Unit tests for MariaDbOrmPlugin URL resolution (no live server needed)."""

    def setup_method(self) -> None:
        self.plugin = MariaDbOrmPlugin()

    def test_resolve_url_from_structured_config(self) -> None:
        cfg = _make_mariadb_config(host="db.internal", port=3307)
        url = self.plugin._resolve_url(cfg)
        assert url.startswith("mysql+aiomysql://")
        assert "db.internal:3307/mydb" in url

    def test_resolve_url_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MARIADB_HOST", "env-host")
        monkeypatch.setenv("MARIADB_PORT", "4406")
        monkeypatch.setenv("MARIADB_DATABASE", "env_db")
        monkeypatch.setenv("MARIADB_USER", "env_user")
        monkeypatch.setenv("MARIADB_PASSWORD", "env_pass")
        url = self.plugin._resolve_url(_make_mariadb_config(host="cfg-host", port=3306))
        assert "env-host:4406/env_db" in url
        assert "env_user:env_pass" in url

    def test_resolve_url_database_url_env_precedence(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://u:p@h:3306/db")
        url = self.plugin._resolve_url(_make_mariadb_config(host="cfg-host"))
        assert url == "mysql+aiomysql://u:p@h:3306/db"

    def test_resolve_url_raises_on_missing_database(self, monkeypatch) -> None:
        monkeypatch.delenv("MARIADB_DATABASE", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="requires a database"):
            self.plugin._resolve_url(_mariadb_config_no_db())

    def test_orm_session_raises_before_init(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            with self.plugin.orm_session():
                pass

    def test_database_label_omits_credentials(self) -> None:
        label = self.plugin._resolve_database_label(
            "mysql+aiomysql://user:secret@db.internal:3306/mydb?charset=utf8mb4"
        )
        assert "secret" not in label
        assert "db.internal:3306/mydb" in label


class TestMariaDbOrmPluginContract(DataSourcePluginContract):
    """Contract conformance for MariaDB, buildable without a live server.

    The plugin's engines are built lazily in ``init_database``; a live DB is not
    assumed. ``create_all`` needs initialized engines and a live DB, which the
    connection-backed tests cover under the E2E overlay, so the inherited
    connection-dependent contract tests are overridden here.
    """

    def setup_method(self) -> None:
        self.plugin = MariaDbOrmPlugin()

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
        self.plugin.init_database(_make_mariadb_config())
        assert self.plugin._sync_engine is not None
        assert self.plugin._async_engine is not None

    def test_init_database_defaults_to_memory(self) -> None:
        # No SQLite-style in-memory default for MariaDB; a real connection
        # target is required. Covered by test_init_database_builds_engines.
        pytest.skip("MariaDB has no in-memory default target")

    def test_init_database_raises_without_database(self) -> None:
        with pytest.raises(RuntimeError, match="requires a database"):
            self.plugin.init_database(_mariadb_config_no_db())

import pytest

from secbaas.community.plugins.database.mariadb.mariadb_orm import MariaDbOrmPlugin
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin
from secbaas.community.spi.database import ConnectionProvider, DataSourcePlugin


class DataSourcePluginContract:
    """Abstract conformance test contract for DataSourcePlugin implementations.

    Every DataSourcePlugin (SqliteOrm, ZDAS) must pass these tests.
    """

    plugin: DataSourcePlugin

    def test_sync_connection_context_manager(self) -> None:
        self.plugin.sync_connection("default")

    def test_create_all_runs_without_error(self) -> None:
        self.plugin.create_all()

    def test_orm_session_available(self) -> None:
        generator = self.plugin.orm_session()
        assert generator is not None

    def test_seed_runs_without_error(self) -> None:
        """Default: seed is a no-op that accepts any session argument."""
        from unittest.mock import MagicMock

        self.plugin.seed(MagicMock())


class TestSqliteOrmPlugin(DataSourcePluginContract):
    def setup_method(self) -> None:
        self.plugin = SqliteOrmPlugin()

    def test_sync_connection_context_manager(self) -> None:
        with pytest.raises(NotImplementedError):
            self.plugin.sync_connection("default")

    async def test_close_disposes_engines(self) -> None:
        try:
            await self.plugin.close()
        except ValueError as e:
            if "greenlet" in str(e):
                pytest.skip("greenlet not installed — cannot test async close")

    def test_orm_session_yields_session(self) -> None:
        with self.plugin.orm_session() as session:
            from sqlalchemy.orm import Session as SASession

            assert isinstance(session, SASession)

    def test_create_all_populates_metadata_and_creates_tables(self) -> None:
        self.plugin.create_all()
        from sqlalchemy import inspect

        inspector = inspect(self.plugin._sync_engine)
        table_names = inspector.get_table_names()
        assert len(table_names) > 0, "create_all should create at least one table"

    def test_seed_inserts_tenant_and_template(self) -> None:
        self.plugin.create_all()
        with self.plugin.orm_session() as session:
            self.plugin.seed(session)

        from secbaas.community.core.repository.tenant import TenantModel

        with self.plugin.orm_session() as session:
            tenant = session.query(TenantModel).filter_by(name="team_claw").first()
            assert tenant is not None, "seed should insert the team_claw tenant"


class ConnectionProviderContract:
    """Abstract conformance test contract for ConnectionProvider implementations."""

    plugin: ConnectionProvider

    def test_get_connection_returns_something(self) -> None:
        result = self.plugin.get_connection("default")
        assert result is not None


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

    def test_ormsession_raises_before_init(self) -> None:
        with pytest.raises(RuntimeError):
            with self.plugin.orm_session():
                pass

    def test_database_label_omits_credentials(self) -> None:
        label = self.plugin._resolve_database_label(
            "mysql+aiomysql://user:secret@db.internal:3306/mydb?charset=utf8mb4"
        )
        assert "secret" not in label
        assert "db.internal:3306/mydb" in label


def _make_mariadb_config(host="127.0.0.1", port=3306):
    from secbaas.community.bootstrap import DatabaseConfig
    from secbaas.community.spi.database import PluginDatabaseType

    return DatabaseConfig(
        plugin_type=PluginDatabaseType.MARIADB_ORM,
        mariadb_host=host,
        mariadb_port=port,
        mariadb_database="mydb",
        mariadb_user="user",
        mariadb_password="pass",
    )

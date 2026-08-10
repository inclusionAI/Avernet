import pytest

from secbaas.community.plugins.database.stub.sqlite_orm import SqliteOrmPlugin
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

        from secbaas.community.core.repository.api_gateway._orm_model import (
            APIKeyModel,
        )
        from secbaas.community.core.repository.tenant import TenantModel

        with self.plugin.orm_session() as session:
            tenant = session.query(TenantModel).filter_by(name="team_claw").first()
            assert tenant is not None, "seed should insert the team_claw tenant"
            bcn_key = (
                session.query(APIKeyModel)
                .filter_by(api_key_prefix="9acXMLaU")
                .first()
            )
            assert bcn_key is not None, "seed should insert the local BCN identity"


class ConnectionProviderContract:
    """Abstract conformance test contract for ConnectionProvider implementations."""

    plugin: ConnectionProvider

    def test_get_connection_returns_something(self) -> None:
        result = self.plugin.get_connection("default")
        assert result is not None

"""DB-backed tests for ``AppRepository`` (queries the seeded ``avernet_application`` table)."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.app import AppRepository
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.app import RegisteredApp


@pytest.fixture(scope="module")
def registry() -> AppRepository:
    db = SqliteDatabasePlugin()
    initialize_database(db, DatabaseConfig(plugin_type="SQLITE_ORM", db_url=""))
    return AppRepository(db)


async def test_known_token_resolves_seeded_app(registry: AppRepository) -> None:
    app = await registry.find_app_by_token("app-key")
    assert app == RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t",
    )


async def test_unknown_token_returns_none(registry: AppRepository) -> None:
    assert await registry.find_app_by_token("nope") is None

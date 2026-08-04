"""DB-backed tests for ``AccessKeyRepository`` (seeded ``avernet_access_key_token`` table)."""

from __future__ import annotations

from datetime import datetime

import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.access_key import RegisteredAccessKey


def _make_db():
    db = SqliteDatabasePlugin()
    return initialize_database(db, DatabaseConfig(plugin_type="SQLITE_ORM", db_url=""))


@pytest.fixture(scope="module")
def registry() -> AccessKeyRepository:
    return AccessKeyRepository(_make_db())


async def test_known_token_resolves_seeded_access_key(
    registry: AccessKeyRepository,
) -> None:
    ak = await registry.find_access_key_by_token("ak-token")
    assert ak == RegisteredAccessKey(
        access_key="ak-1",
        tenant="t",
        expire_at=datetime(2027, 1, 1, 0, 0, 0),
    )


async def test_unknown_token_returns_none(
    registry: AccessKeyRepository,
) -> None:
    assert await registry.find_access_key_by_token("nope") is None

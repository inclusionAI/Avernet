"""DB-backed tests for ``AppRepository``'s API-key path.

Seeds ``avernet_application`` the way the registrar and the secbaas migration
both do — a PBKDF2 hash plus the key's 8-character prefix, never the plaintext —
and presents the plaintext key the way a caller would.
"""

from __future__ import annotations

import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.app import APIKeyGenerator, AppRepository, AppRow
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

_ACTIVE_KEY = APIKeyGenerator.generate()
_INACTIVE_KEY = APIKeyGenerator.generate()
_REVOKED_KEY = APIKeyGenerator.generate()


def _seed(session, key: str, *, app_name: str, status: str = "ACTIVE") -> None:
    session.add(
        AppRow(
            app_name=app_name,
            app_type="assistant",
            api_key_hash=APIKeyGenerator.hash_key(key),
            api_key_prefix=key[:8],
            owners="org-1",
            tenant="t",
            status=status,
        )
    )


@pytest.fixture(scope="module")
def db() -> DataSourcePlugin:
    plugin = initialize_database(
        SqliteDatabasePlugin(), DatabaseConfig(plugin_type="SQLITE_ORM", db_url="")
    )
    with plugin.orm_session() as session:
        _seed(session, _ACTIVE_KEY, app_name="Demo App")
        _seed(session, _INACTIVE_KEY, app_name="Dormant App", status="INACTIVE")
        _seed(session, _REVOKED_KEY, app_name="Revoked App", status="REVOKED")
    return plugin


@pytest.fixture(scope="module")
def registry(db: DataSourcePlugin) -> AppRepository:
    return AppRepository(db)


async def test_correct_key_resolves_active_seeded_app(registry: AppRepository) -> None:
    assert await registry.find_app_by_credential(_ACTIVE_KEY) == RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t",
    )


async def test_wrong_key_with_known_prefix_returns_none(
    registry: AppRepository,
) -> None:
    """The prefix locates the row; only the hash decides. This is the whole point."""
    impostor = _ACTIVE_KEY[:8] + APIKeyGenerator.generate()[8:]
    assert impostor != _ACTIVE_KEY
    assert await registry.find_app_by_credential(impostor) is None


async def test_unknown_key_returns_none(registry: AppRepository) -> None:
    assert await registry.find_app_by_credential(APIKeyGenerator.generate()) is None


@pytest.mark.parametrize("status", ["INACTIVE", "REVOKED"])
async def test_non_active_rows_return_none(
    registry: AppRepository, status: str
) -> None:
    """A correct key is still refused when its app is not ACTIVE."""
    key = _INACTIVE_KEY if status == "INACTIVE" else _REVOKED_KEY
    assert await registry.find_app_by_credential(key) is None


@pytest.mark.parametrize("credential", ["", "short", "1234567"])
async def test_credentials_too_short_return_none(
    registry: AppRepository, credential: str
) -> None:
    """Rejected before any query — there is no prefix to look up."""
    assert await registry.find_app_by_credential(credential) is None


async def test_row_with_prefix_but_no_hash_returns_none(
    db: DataSourcePlugin, registry: AppRepository
) -> None:
    """A malformed row fails closed rather than raising."""
    key = APIKeyGenerator.generate()
    with db.orm_session() as session:
        session.add(
            AppRow(
                app_name="Broken App",
                app_type="assistant",
                api_key_hash=None,
                api_key_prefix=key[:8],
                owners="org-1",
                tenant="t",
            )
        )
    assert await registry.find_app_by_credential(key) is None


async def test_exists_prefix(registry: AppRepository) -> None:
    assert await registry.exists_prefix(_ACTIVE_KEY[:8]) is True
    assert await registry.exists_prefix(APIKeyGenerator.generate()[:8]) is False


async def test_exists_prefix_sees_non_active_rows(registry: AppRepository) -> None:
    """Collision checking must ignore status, or a new key could shadow an old row."""
    assert await registry.exists_prefix(_REVOKED_KEY[:8]) is True


async def test_store_persists_only_the_hash(db: DataSourcePlugin) -> None:
    key = APIKeyGenerator.generate()
    repository = AppRepository(db)
    app_id = await repository.store(
        api_key_hash=APIKeyGenerator.hash_key(key),
        api_key_prefix=key[:8],
        app_name="Stored App",
        owners="org-2",
        app_type="assistant",
        tenant="t2",
        creator="alice",
        modifier="alice",
    )

    with db.orm_session() as session:
        row = session.get(AppRow, app_id)
        assert row is not None
        assert row.api_key_prefix == key[:8]
        assert row.token is None
        assert key not in (row.api_key_hash or "")  # no plaintext anywhere

    assert await repository.find_app_by_credential(key) is not None

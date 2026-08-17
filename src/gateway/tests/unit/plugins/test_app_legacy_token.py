"""DB-backed tests for the deprecated legacy-JWT path — the continuity guarantee.

Seeds ``avernet_application`` the way it looked before the API-key scheme
(``token`` holding a plaintext JWT, ``api_key_*`` NULL) and asserts such an app
still authenticates. If these tests fail, shipping this change cuts off every
credential holder who has not yet rotated.

Delete this module together with ``AppRepository._by_legacy_token`` and the
``token`` column once the deprecation warning has gone quiet in production.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import event

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.app import APIKeyGenerator, AppRepository, AppRow
from gateway.community.core.app import _repository as app_repository
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

# A real gateway-issued app token: HS256 JWT with claims
# {"iss":"gateway","typ":"app","sub":"Legacy App","tenant":"t","iat":…,"jti":…}.
# Its leading characters encode the header, which is why every such token shares
# the prefix `eyJhbGci` and why these cannot be migrated onto prefix lookup.
_LEGACY_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6ImJhcmUifQ"
    ".eyJpc3MiOiJnYXRld2F5IiwidHlwIjoiYXBwIiwic3ViIjoiTGVnYWN5IEFwcCIsInRlbmFudCI6"
    "InQiLCJpYXQiOjE3MDAwMDAwMDAsImp0aSI6ImRlYWRiZWVmIn0"
    ".Z3dGVzdHNpZ25hdHVyZV9ub3RfdmVyaWZpZWRfYnlfdGhlX2dhdGV3YXk"
)
_INACTIVE_JWT = _LEGACY_JWT[:-4] + "aaaa"
_API_KEY = APIKeyGenerator.generate()


@pytest.fixture(scope="module")
def db() -> DataSourcePlugin:
    plugin = initialize_database(
        SqliteDatabasePlugin(), DatabaseConfig(plugin_type="SQLITE_ORM", db_url="")
    )
    with plugin.orm_session() as session:
        # Seeded the old way: a plaintext token, no api_key_* columns.
        session.add(
            AppRow(
                app_name="Legacy App",
                app_type="assistant",
                token=_LEGACY_JWT,
                owners="org-legacy",
                tenant="t",
            )
        )
        session.add(
            AppRow(
                app_name="Dormant Legacy App",
                app_type="assistant",
                token=_INACTIVE_JWT,
                owners="org-legacy",
                tenant="t",
                status="INACTIVE",
            )
        )
        # ...alongside a row on the new scheme, in the same table.
        session.add(
            AppRow(
                app_name="Modern App",
                app_type="assistant",
                api_key_hash=APIKeyGenerator.hash_key(_API_KEY),
                api_key_prefix=_API_KEY[:8],
                owners="org-modern",
                tenant="t2",
            )
        )
    return plugin


@pytest.fixture(scope="module")
def registry(db: DataSourcePlugin) -> AppRepository:
    return AppRepository(db)


async def test_legacy_jwt_still_resolves_its_app(registry: AppRepository) -> None:
    """The whole point of the transition window."""
    assert await registry.find_app_by_credential(_LEGACY_JWT) == RegisteredApp(
        id=1,
        app_name="Legacy App",
        owners="org-legacy",
        app_type="assistant",
        tenant="t",
    )


async def test_unknown_jwt_returns_none(registry: AppRepository) -> None:
    """Soft miss, so another Bearer-based chain may still claim it (US27)."""
    assert await registry.find_app_by_credential(_LEGACY_JWT[:-4] + "zzzz") is None


async def test_inactive_legacy_row_returns_none(registry: AppRepository) -> None:
    """Behavior change: the old lookup ignored status. Confirmed safe — the
    non-ACTIVE population in the real table is zero."""
    assert await registry.find_app_by_credential(_INACTIVE_JWT) is None


@pytest.fixture(autouse=True)
def _reset_warned_apps() -> Iterator[None]:
    """The report ledger is module state keyed on surrogate ids, which restart at
    1 in every fresh in-memory DB. Clear on both sides so entries cannot leak
    between tests or into another module."""
    app_repository._last_reported.clear()
    yield
    app_repository._last_reported.clear()


async def test_legacy_resolution_warns_with_the_app_identity(
    registry: AppRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning is what tells us when the path is safe to delete."""
    with caplog.at_level(logging.WARNING):
        assert await registry.find_app_by_credential(_LEGACY_JWT) is not None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "deprecated" in message
    assert "Legacy App" in message  # identifies who still needs rotating


async def test_legacy_warning_is_emitted_once_per_app_not_once_per_request(
    registry: AppRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """A busy un-rotated app would otherwise emit millions of lines a day and
    bury the quiet app that also needs rotating."""
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            assert await registry.find_app_by_credential(_LEGACY_JWT) is not None

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_api_key_resolution_does_not_warn(
    registry: AppRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the legacy path is noisy, or the signal is worthless."""
    with caplog.at_level(logging.WARNING):
        assert await registry.find_app_by_credential(_API_KEY) is not None

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


async def test_both_credential_forms_coexist(registry: AppRepository) -> None:
    """One table, two schemes, each resolving to its own app."""
    legacy = await registry.find_app_by_credential(_LEGACY_JWT)
    modern = await registry.find_app_by_credential(_API_KEY)

    assert legacy is not None and modern is not None
    assert legacy.app_name == "Legacy App"
    assert modern.app_name == "Modern App"


async def test_an_api_key_never_takes_the_legacy_path(
    db: DataSourcePlugin, registry: AppRepository
) -> None:
    """Format dispatch is total: a 32-char key stored as a legacy token is
    looked up as an API key and therefore does not resolve."""
    stray = APIKeyGenerator.generate()
    with db.orm_session() as session:
        session.add(
            AppRow(
                app_name="Misfiled App",
                app_type="assistant",
                token=stray,  # a key sitting in the legacy column
                owners="org-1",
                tenant="t",
            )
        )
    assert await registry.find_app_by_credential(stray) is None


async def test_trailing_newline_authenticates_on_neither_path(
    registry: AppRepository,
) -> None:
    """``validate_format`` accepts a trailing newline (an upstream quirk), so a
    newline-suffixed key routes to the API-key branch. It must still be refused
    — and a newline-suffixed JWT must not match the legacy row either."""
    assert await registry.find_app_by_credential(_API_KEY + "\n") is None
    assert await registry.find_app_by_credential(_LEGACY_JWT + "\n") is None


@pytest.mark.parametrize("credential", [_API_KEY, _LEGACY_JWT])
async def test_resolution_issues_exactly_one_query(
    db: DataSourcePlugin, registry: AppRepository, credential: str
) -> None:
    """No try-then-fallback: dispatch picks a path up front.

    Guards the hot path — a fallback would mean two queries per request for
    every API key, plus a wasted PBKDF2 on misses.
    """
    selects: list[str] = []

    with db.orm_session() as session:
        engine = session.bind

    def _count(conn, cursor, statement, params, context, executemany) -> None:  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        assert await registry.find_app_by_credential(credential) is not None
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert len(selects) == 1, f"expected one lookup, got {len(selects)}"

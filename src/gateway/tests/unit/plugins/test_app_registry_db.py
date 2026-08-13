"""DB-backed tests for ``AppRepository``'s API-key path.

Seeds ``avernet_application`` the way the registrar and the secbaas migration
both do — a PBKDF2 hash plus the key's 8-character prefix, never the plaintext —
and presents the plaintext key the way a caller would.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.app import APIKeyGenerator, AppRepository, AppRow
from gateway.community.core.app import _repository as app_repository
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

_ACTIVE_KEY = APIKeyGenerator.generate()
_ACTIVE_HASH = APIKeyGenerator.hash_key(_ACTIVE_KEY)
_INACTIVE_KEY = APIKeyGenerator.generate()
_REVOKED_KEY = APIKeyGenerator.generate()


def _seed(session, key: str, *, app_name: str, status: str = "ACTIVE") -> None:
    session.add(
        AppRow(
            app_name=app_name,
            app_type="assistant",
            api_key_hash=_ACTIVE_HASH
            if key == _ACTIVE_KEY
            else APIKeyGenerator.hash_key(key),
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


@pytest.fixture(autouse=True)
def _reset_report_ledgers() -> Iterator[None]:
    """The report-once ledgers are module state keyed on surrogate ids, which
    restart at 1 in every fresh in-memory DB. Clear on both sides so entries
    cannot leak between tests or into another module."""
    app_repository._reported_corrupt_apps.clear()
    yield
    app_repository._reported_corrupt_apps.clear()


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


async def test_duplicate_prefix_raises_prefix_taken_error(
    db: DataSourcePlugin,
) -> None:
    """The real IntegrityError path, not a fake raising the domain error.

    Guards the constraint-name discrimination in ``store``: without it the
    registrar retries any write failure and reports prefix exhaustion.
    """
    from gateway.community.core.app import PrefixTakenError

    repository = AppRepository(db)
    key = APIKeyGenerator.generate()
    common = dict(
        api_key_hash=APIKeyGenerator.hash_key(key),
        api_key_prefix=key[:8],
        owners="o",
        app_type="assistant",
        tenant="t",
    )
    await repository.store(app_name="First", **common)

    with pytest.raises(PrefixTakenError):
        await repository.store(app_name="Second", **common)


async def test_non_collision_integrity_errors_are_not_disguised(
    db: DataSourcePlugin,
) -> None:
    """A NOT NULL violation must surface as itself, not as a prefix collision."""
    from sqlalchemy.exc import IntegrityError

    from gateway.community.core.app import PrefixTakenError

    key = APIKeyGenerator.generate()
    with pytest.raises(IntegrityError) as caught:
        await AppRepository(db).store(
            api_key_hash=APIKeyGenerator.hash_key(key),
            api_key_prefix=key[:8],
            app_name=None,  # type: ignore[arg-type]
            owners="o",
            app_type="assistant",
            tenant="t",
        )
    assert not isinstance(caught.value, PrefixTakenError)


async def test_malformed_hash_is_reported_not_silently_rejected(
    db: DataSourcePlugin, registry: AppRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """A hash missing its separator reads as a wrong key without this check."""
    import logging

    key = APIKeyGenerator.generate()
    with db.orm_session() as session:
        session.add(
            AppRow(
                app_name="Malformed App",
                app_type="assistant",
                api_key_hash="no-separator-here",
                api_key_prefix=key[:8],
                owners="org-1",
                tenant="t",
            )
        )

    with caplog.at_level(logging.ERROR):
        assert await registry.find_app_by_credential(key) is None

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Malformed App" in errors[0].getMessage()


async def test_corrupt_row_error_is_logged_once_per_app(
    db: DataSourcePlugin, registry: AppRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt row can never authenticate, so its client retries forever."""
    import logging

    key = APIKeyGenerator.generate()
    with db.orm_session() as session:
        session.add(
            AppRow(
                app_name="Repeatedly Corrupt App",
                app_type="assistant",
                api_key_hash=None,
                api_key_prefix=key[:8],
                owners="org-1",
                tenant="t",
            )
        )

    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            assert await registry.find_app_by_credential(key) is None

    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


async def test_corrupt_row_is_logged_as_an_error(
    db: DataSourcePlugin, registry: AppRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """A prefix with no hash breaks the table's one-credential-form invariant.

    It fails closed either way, but silently would leave an operator staring at
    a healthy-looking row and an app that cannot authenticate.
    """
    import logging

    key = APIKeyGenerator.generate()
    with db.orm_session() as session:
        session.add(
            AppRow(
                app_name="Corrupt App",
                app_type="assistant",
                api_key_hash=None,
                api_key_prefix=key[:8],
                owners="org-1",
                tenant="t",
            )
        )

    with caplog.at_level(logging.ERROR):
        assert await registry.find_app_by_credential(key) is None

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Corrupt App" in errors[0].getMessage()


async def test_verification_does_not_stall_the_event_loop(
    registry: AppRepository,
) -> None:
    """PBKDF2 must not run on the event loop.

        Measures the property directly — the longest the loop goes without servicing
        a 5ms heartbeat — rather than inferring it from wall-clock speedup. Speedup
        needs a second core to appear, and ``os.cpu_count()`` does not see cgroup or
        affinity limits, so a CPU-count guard silently reports the wrong answer on a
        constrained runner. A stall is visible on any number of cores.

    Calibrated against a *direct* ``verify_key`` call, not against a repository
        lookup: timing the lookup would include the very derivation under test, so a
        blocking implementation would raise its own bound and pass.

        One verification at a time, deliberately. Enough concurrent derivations to
        saturate every core starve the loop thread even when the work is correctly
        off it, so a concurrent version measures CPU saturation rather than
        blocking. With a single derivation on a worker thread there is always a core
        for the loop, so the two cases separate cleanly: correct code idles at the
        ~5ms heartbeat, blocking code stalls for the whole derivation.

        Skipped on a single core, where the property is unobservable: the loop
        thread competes with the worker threads for the one CPU, so starvation is
        indistinguishable from blocking. ``sched_getaffinity`` rather than
        ``cpu_count`` because only the former sees affinity and cgroup limits —
        ``cpu_count`` reports the machine's cores and would let the guard pass on a
        pinned runner, then fail the assertion against correct code.
    """
    import asyncio
    import os
    import time

    available = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else (os.cpu_count() or 1)
    )
    if available < 2:
        pytest.skip(
            f"needs >1 usable core to tell stalling from starvation (have {available})"
        )

    start = time.perf_counter()
    APIKeyGenerator.verify_key(_ACTIVE_KEY, _ACTIVE_HASH)
    derivation = time.perf_counter() - start
    budget = derivation * 0.5

    longest_gap = 0.0

    async def heartbeat() -> None:
        nonlocal longest_gap
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            longest_gap = max(longest_gap, now - previous)
            previous = now

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.02)
        longest_gap = 0.0
        assert await registry.find_app_by_credential(_ACTIVE_KEY) is not None
        # Yield before cancelling: the heartbeat records a gap only when it next
        # runs, so cancelling straight away discards the very stall under test.
        await asyncio.sleep(0.01)
    finally:
        beat.cancel()

    assert longest_gap < budget, (
        f"the loop went {longest_gap * 1000:.0f}ms without running a coroutine, "
        f"against a {budget * 1000:.0f}ms budget (half of a {derivation * 1000:.0f}ms "
        "derivation) — verification is running on the event loop"
    )

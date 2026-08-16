"""Unit tests for AppRegistrar (mints an API key, persists its hash, returns the record)."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.app import APIKeyGenerator, AppRegistrar, AppRepository
from gateway.community.core.app._orm import AppRow
from gateway.community.core.app._registrar import IssuedApp, PrefixAllocationError
from gateway.community.core.app._repository import PrefixTakenError
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.database import DataSourcePlugin


@pytest.fixture
def db() -> DataSourcePlugin:
    return initialize_database(
        SqliteDatabasePlugin(), DatabaseConfig(plugin_type="SQLITE_ORM", db_url="")
    )


@pytest.fixture
def registrar(db: DataSourcePlugin) -> AppRegistrar:
    return AppRegistrar(AppRepository(db))


async def test_register_persists_row_and_returns_record(
    registrar: AppRegistrar, db: DataSourcePlugin
) -> None:
    issued = await registrar.register(
        "X App", "org-1", "assistant", "t1", creator="alice"
    )
    assert isinstance(issued, IssuedApp)
    assert isinstance(issued.id, int) and issued.id >= 1
    assert issued.app_name == "X App"
    assert issued.owners == "org-1"
    assert issued.app_type == "assistant"
    assert issued.tenant == "t1"

    # The registering caller is recorded as both creator and modifier (non-empty),
    # never a fabricated default.
    with db.orm_session() as session:
        row = session.get(AppRow, issued.id)
        assert row is not None
        assert row.creator == "alice"
        assert row.modifier == "alice"


async def test_issued_key_is_32_char_base62(registrar: AppRegistrar) -> None:
    issued = await registrar.register("X App", "org-1", "assistant", "t1", creator="a")
    assert len(issued.api_key) == 32
    assert APIKeyGenerator.validate_format(issued.api_key) is True


async def test_only_the_hash_is_persisted(
    registrar: AppRegistrar, db: DataSourcePlugin
) -> None:
    """The plaintext key must not be recoverable from the registry."""
    issued = await registrar.register("X App", "org-1", "assistant", "t1", creator="a")

    with db.orm_session() as session:
        row = session.get(AppRow, issued.id)
        assert row is not None
        assert row.token is None  # no JWT is ever minted now
        assert row.api_key_prefix == issued.api_key[:8]
        assert row.api_key_hash is not None
        assert issued.api_key not in row.api_key_hash


async def test_issued_key_authenticates(
    registrar: AppRegistrar, db: DataSourcePlugin
) -> None:
    """Mint → verify closed loop, through the same repository a request uses."""
    issued = await registrar.register("X App", "org-1", "assistant", "t1", creator="a")

    record = await AppRepository(db).find_app_by_credential(issued.api_key)
    assert record is not None
    assert record.id == issued.id
    assert record.app_name == "X App"
    assert record.tenant == "t1"


async def test_two_registrations_get_different_keys(registrar: AppRegistrar) -> None:
    first = await registrar.register("A", "org", "assistant", "t", creator="a")
    second = await registrar.register("B", "org", "assistant", "t", creator="a")
    assert first.api_key != second.api_key
    assert first.api_key[:8] != second.api_key[:8]


class _CollidingRepository:
    """Reports every prefix as taken, and records what was attempted."""

    def __init__(self) -> None:
        self.checked: list[str] = []
        self.stored = 0

    async def exists_prefix(self, api_key_prefix: str) -> bool:
        self.checked.append(api_key_prefix)
        return True

    async def store(self, **_kwargs: object) -> int:
        self.stored += 1
        return 1


async def test_prefix_collision_retries_then_fails_without_writing() -> None:
    repository = _CollidingRepository()
    registrar = AppRegistrar(repository)  # type: ignore[arg-type]

    with pytest.raises(PrefixAllocationError):
        await registrar.register("X", "org", "assistant", "t", creator="a")

    assert len(repository.checked) == 3  # three attempts, then give up
    assert len(set(repository.checked)) == 3  # a fresh key each time
    assert repository.stored == 0  # nothing partially written


async def test_registration_succeeds_on_a_later_attempt() -> None:
    """A collision is retried, not fatal."""

    class _CollidesOnce(_CollidingRepository):
        async def exists_prefix(self, api_key_prefix: str) -> bool:
            self.checked.append(api_key_prefix)
            return len(self.checked) == 1

    repository = _CollidesOnce()
    issued = await AppRegistrar(repository).register(  # type: ignore[arg-type]
        "X", "org", "assistant", "t", creator="a"
    )

    assert len(repository.checked) == 2
    assert repository.stored == 1
    assert issued.api_key[:8] == repository.checked[1]


async def test_prefix_taken_at_insert_is_retried() -> None:
    """The check-then-insert race: two concurrent registrations can both pass
    ``exists_prefix``, so the loser must retry rather than 500."""

    class _LosesFirstRace(_CollidingRepository):
        async def exists_prefix(self, api_key_prefix: str) -> bool:
            self.checked.append(api_key_prefix)
            return False  # nothing seen yet — but someone else is inserting

        async def store(self, **kwargs: object) -> int:
            self.stored += 1
            if self.stored == 1:
                raise PrefixTakenError("raced")
            return 7

    repository = _LosesFirstRace()
    issued = await AppRegistrar(repository).register(  # type: ignore[arg-type]
        "X", "org", "assistant", "t", creator="a"
    )

    assert repository.stored == 2  # first insert lost the race, second won
    assert issued.id == 7


async def test_persistent_insert_races_fail_without_a_partial_write() -> None:
    class _AlwaysRaced(_CollidingRepository):
        async def exists_prefix(self, api_key_prefix: str) -> bool:
            self.checked.append(api_key_prefix)
            return False

        async def store(self, **kwargs: object) -> int:
            self.stored += 1
            raise PrefixTakenError("raced")

    repository = _AlwaysRaced()
    with pytest.raises(PrefixAllocationError):
        await AppRegistrar(repository).register(  # type: ignore[arg-type]
            "X", "org", "assistant", "t", creator="a"
        )
    assert repository.stored == 3  # three attempts, all lost

"""Authorization lookup for ``baas_resource_key_bot_mapping``.

Pins the wildcard grant: a row whose ``bot_id`` is ``*`` authorizes the key for
every bot, while a key with no rows at all still authorizes nothing. The
deny-by-default half is as important as the wildcard itself — inferring the
wildcard from an empty set would turn "never configured" into "allow
everything".

The repository is exercised against a real in-memory database rather than a
mocked session, so the OR filtering is what is actually under test.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from secbaas.community.core.repository.resource_key import OrmResourceKeyRepository
from secbaas.community.core.repository.resource_key._orm_model import (
    WILDCARD_BOT_ID,
    ResourceKeyBotMappingModel,
    ResourceKeyModel,
)
from secbaas.community.spi.database import Base

#: Two resource keys and one unrelated key id that has no rows at all.
KEY_WITH_EXACT_ONLY = 1
KEY_WITH_WILDCARD = 2
KEY_WITH_NO_ROWS = 3
#: A key with no mapping rows, used only to resolve the row itself.
KEY_PLAIN = 4


class _InMemoryDatabase:
    """Minimal ``orm_session`` provider over one SQLite connection."""

    def __init__(self) -> None:
        self._engine = create_engine("sqlite://")
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)

    @contextmanager
    def orm_session(self):
        session: Session = self._session_factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    def seed(self) -> None:
        session = self._session_factory()
        try:
            session.add_all(
                [
                    ResourceKeyModel(
                        id=KEY_WITH_EXACT_ONLY,
                        tenant="teamclaw",
                        resource_key="exact-only-key",
                        app="app",
                    ),
                    ResourceKeyModel(
                        id=KEY_WITH_WILDCARD,
                        tenant="teamclaw",
                        resource_key="wildcard-key",
                        app="app",
                    ),
                    # Exercised by the sibling lookup's own tests: the row an
                    # authorization is keyed on has to be resolvable by
                    # (resource_key, tenant).
                    ResourceKeyModel(
                        id=KEY_PLAIN,
                        tenant="teamclaw",
                        resource_key="whole-key",
                        app="app",
                    ),
                ]
            )
            # SQLite does not autoincrement a BigInteger primary key the way
            # MySQL does, so ids are assigned explicitly here.
            session.add_all(
                [
                    ResourceKeyBotMappingModel(
                        id=1, resource_key_id=KEY_WITH_EXACT_ONLY, bot_id="bot-a"
                    ),
                    ResourceKeyBotMappingModel(
                        id=2, resource_key_id=KEY_WITH_WILDCARD, bot_id=WILDCARD_BOT_ID
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()


@pytest.fixture
def repository() -> OrmResourceKeyRepository:
    database = _InMemoryDatabase()
    database.seed()
    return OrmResourceKeyRepository(database)


class TestExactAuthorization:
    """A row naming the bot authorizes exactly that bot."""

    def test_exact_hit_is_authorized(self, repository):
        assert repository.exists_bot_mapping(KEY_WITH_EXACT_ONLY, "bot-a") is True

    def test_other_bot_is_denied_without_a_wildcard(self, repository):
        assert repository.exists_bot_mapping(KEY_WITH_EXACT_ONLY, "bot-b") is False


class TestWildcardAuthorization:
    """A ``*`` row authorizes any bot for its own key."""

    def test_wildcard_covers_an_otherwise_unknown_bot(self, repository):
        assert repository.exists_bot_mapping(KEY_WITH_WILDCARD, "bot-never-seen") is True

    def test_wildcard_also_covers_an_exactly_named_bot(self, repository):
        assert repository.exists_bot_mapping(KEY_WITH_WILDCARD, "bot-a") is True

    def test_wildcard_on_another_key_does_not_leak(self, repository):
        # KEY_WITH_EXACT_ONLY has no wildcard, so KEY_WITH_WILDCARD's row must
        # not authorize it for an arbitrary bot.
        assert repository.exists_bot_mapping(KEY_WITH_EXACT_ONLY, "bot-never-seen") is False


class TestDenyByDefault:
    """No rows means no access — never an implicit wildcard."""

    def test_empty_mapping_set_denies(self, repository):
        assert repository.exists_bot_mapping(KEY_WITH_NO_ROWS, "bot-a") is False

    def test_neither_exact_nor_wildcard_denies(self, repository):
        assert repository.exists_bot_mapping(KEY_WITH_NO_ROWS, "bot-never-seen") is False


class TestResourceKeyLookup:
    """The sibling lookup in the same module; it resolves the row whose ``id``
    the authorization check is keyed on, so a change here would silently point
    every grant at the wrong key."""

    def test_returns_the_row_for_a_known_key_and_tenant(self, repository):
        record = repository.get_by_resource_key_and_tenant("whole-key", "teamclaw")

        assert record is not None
        assert record.resource_key == "whole-key"
        assert record.tenant == "teamclaw"
        assert record.app == "app"

    def test_returns_none_for_an_unknown_key(self, repository):
        assert repository.get_by_resource_key_and_tenant("nope", "teamclaw") is None

    def test_is_scoped_by_tenant(self, repository):
        assert repository.get_by_resource_key_and_tenant("whole-key", "other") is None
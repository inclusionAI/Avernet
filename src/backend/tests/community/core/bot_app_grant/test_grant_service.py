"""Repository and service tests for owner-granted bot authorizations.

Run against a real SQLite database rather than a mock repository, because the
behaviours worth pinning here are database behaviours: a unique key that has to
survive a second withdrawal, an append-only log that has to outlive the row it
describes, and an insert race that has to resolve to the winner's row instead of
a 500. A mock would assert that the code calls itself the way it was written.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_app_grant.errors import GrantNotFoundError
from agentclaw.community.core.bot_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    BotAppGrantLogModel,
    BotAppGrantModel,
    GrantAction,
)
from agentclaw.community.core.bot_app_grant.services import BotAppGrantService
from agentclaw.community.core.repository.implementations.bot.app_grant import (
    BotAppGrantRepository,
)


@pytest.fixture
def sessions():
    """A fresh in-memory database with only this feature's two tables."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[BotAppGrantModel.__table__, BotAppGrantLogModel.__table__]
    )
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def db(sessions):
    """A DatabasePlugin stand-in exposing both session entrypoints.

    Both yield the same real session: SQLite has no AUTOCOMMIT split to model,
    and the distinction these tests care about — that the mutations are one unit
    of work — is asserted through observable state, not through which context
    manager was called.
    """

    class _Db:
        @contextmanager
        def _session(self):
            session = sessions()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        def orm_session(self):
            return self._session()

        def transactional_orm_session(self):
            return self._session()

    return _Db()


@pytest.fixture
def repo(db):
    return BotAppGrantRepository(db)


class _LiveBots:
    """A bot repository double tracking which bots this owner still has.

    Mirrors ``list_live_bot_ids_by_owner``, which excludes soft-deleted rows —
    so ``delete`` here means what it means in production: the id stops coming
    back. ``queries`` counts calls, which is how the batching test proves the
    filter costs one query rather than one per grant.
    """

    def __init__(self):
        self.live: dict[str, set[str]] = {}
        self.queries = 0

    def add(self, owner_id: str, *bot_ids: str) -> None:
        self.live.setdefault(owner_id, set()).update(bot_ids)

    def delete(self, owner_id: str, bot_id: str) -> None:
        self.live.get(owner_id, set()).discard(bot_id)

    def list_live_bot_ids_by_owner(self, owner_id: str) -> list[str]:
        self.queries += 1
        return sorted(self.live.get(owner_id, set()))


@pytest.fixture
def bots():
    """Seeded with every bot the tests grant, so the default is "all live"."""
    doubles = _LiveBots()
    for owner in ("u-1", "u-2"):
        doubles.add(owner, "b-1", "b-2")
    return doubles


@pytest.fixture
def service(repo, bots):
    return BotAppGrantService(repo, bots)


GRANT = {"bot_id": "b-1", "owner_id": "u-1", "app_id": 42, "app_name": "partner"}


def _log(sessions):
    with sessions() as session:
        return [
            row.action
            for row in session.query(BotAppGrantLogModel).order_by(
                BotAppGrantLogModel.id
            )
        ]


def test_owner_and_tenant_are_resolved_at_write_time_not_read_from_request(
    service, sessions
):
    """The row must answer "whose bot, in which tenant" on its own.

    The later machine-caller path resolves ownership from these columns rather
    than from anything the caller sends, so they have to be on the row and they
    have to be the resolved values.
    """
    record = service.grant(**GRANT)

    assert record.owner_id == "u-1"
    assert record.avernet_tenant  # stamped by the guard, not passed in
    with sessions() as session:
        row = session.query(BotAppGrantModel).one()
        assert row.owner_id == "u-1"
        assert row.avernet_tenant == record.avernet_tenant


def test_grant_is_idempotent_and_does_not_move_granted_at(service, sessions):
    """A repeat is the same authorization, not a new period.

    Moving ``gmt_create`` would make the record answer "when someone last
    called this" instead of "since when", and appending a second event would
    invent a period that never began.
    """
    first = service.grant(**GRANT)
    second = service.grant(**GRANT)

    assert second.id == first.id
    assert second.gmt_create == first.gmt_create
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 1
    assert _log(sessions) == [GrantAction.GRANTED]


def test_duplicate_grant_appends_no_log_event(service, sessions):
    service.grant(**GRANT)
    service.grant(**GRANT)
    service.grant(**GRANT)

    assert _log(sessions) == [GrantAction.GRANTED]


def test_grant_withdraw_grant_withdraw_survives(service, sessions):
    """The cycle that broke the first schema.

    A soft-deleted single table collides on the *second* withdrawal, when two
    revoked rows share the remaining key columns. Two tables make it ordinary.
    """
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)

    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 0
    assert _log(sessions) == [
        GrantAction.GRANTED,
        GrantAction.REVOKED,
        GrantAction.GRANTED,
        GrantAction.REVOKED,
    ]


def test_regrant_after_revoke_creates_a_new_period(service, sessions):
    """The second grant is a new period, not a revival of the closed one.

    Asserted through the log rather than through row identity. The live row is
    genuinely new — the first was deleted — but its primary key proves nothing:
    SQLite reuses a rowid freed by a delete, so an id comparison would pass on
    MySQL and fail here for a reason that has no bearing on the contract. What
    the contract actually promises is that the two periods stay separable, and
    the log is where that lives.
    """
    first = service.grant(**GRANT)
    service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)
    second = service.grant(**GRANT)

    assert _log(sessions) == [
        GrantAction.GRANTED,
        GrantAction.REVOKED,
        GrantAction.GRANTED,
    ], "the closed period and the new one must both be readable"
    assert second.gmt_create >= first.gmt_create
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 1, "exactly one is live"


def test_log_outlives_the_live_row(service, sessions):
    """The audit is read exactly when the live row is gone.

    So the log carries its own copy of the app name and tenant rather than
    joining to a row that no longer exists.
    """
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)

    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 0
        revoked = (
            session.query(BotAppGrantLogModel)
            .filter(BotAppGrantLogModel.action == GrantAction.REVOKED)
            .one()
        )
        assert revoked.app_name == "partner"
        assert revoked.avernet_tenant
        assert revoked.bot_id == "b-1"


def test_revoke_absent_grant_raises_distinctly(service):
    """"Nothing to remove" must not read as "removed"."""
    with pytest.raises(GrantNotFoundError):
        service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)


def test_revoke_is_not_silently_repeatable(service):
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)

    with pytest.raises(GrantNotFoundError):
        service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)


def test_list_excludes_revoked_grants(service):
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", owner_id="u-1", app_id=42)

    assert service.list_for_bot(bot_id="b-1", owner_id="u-1") == []
    assert service.list_for_app(app_id=42, owner_id="u-1") == []


def test_list_for_app_is_scoped_to_the_calling_app(service):
    """Two apps on one owner's bots see disjoint lists.

    A listing that silently widens is worse than one that fails, so each
    scoping dimension is pinned on its own.
    """
    service.grant(bot_id="b-1", owner_id="u-1", app_id=42, app_name="partner")
    service.grant(bot_id="b-2", owner_id="u-1", app_id=99, app_name="other")

    assert [r.bot_id for r in service.list_for_app(app_id=42, owner_id="u-1")] == ["b-1"]
    assert [r.bot_id for r in service.list_for_app(app_id=99, owner_id="u-1")] == ["b-2"]


def test_grant_truncates_an_over_long_app_name_instead_of_failing(service, sessions):
    """An authorization must not fail because a display name is long.

    The gateway does not bound ``app_name``, so some valid name exceeds any
    width this table could pick. Truncating in code makes the outcome the same
    on every engine and SQL mode — rather than a rejected grant under strict
    settings and a silent truncation under permissive ones — and costs nothing
    that matters, because identity is ``app_id``, not the name.
    """
    long_name = "n" * (APP_NAME_MAX_LENGTH + 500)

    record = service.grant(
        bot_id="b-1", owner_id="u-1", app_id=42, app_name=long_name
    )

    assert len(record.app_name) == APP_NAME_MAX_LENGTH
    assert record.app_id == 42, "identity is unaffected by the truncation"
    with sessions() as session:
        logged = session.query(BotAppGrantLogModel).one()
        assert len(logged.app_name) == APP_NAME_MAX_LENGTH


def test_list_for_app_excludes_a_deleted_bot(service, bots):
    """A grant outliving its bot must not be reported as live access.

    ``delete_bot`` soft-deletes the bot and leaves grants alone, so without the
    filter the app's view would advertise a deleted bot as currently authorized
    indefinitely. The owner's view needs no equivalent test: its route resolves
    the bot first and 404s.
    """
    service.grant(bot_id="b-1", owner_id="u-1", app_id=42, app_name="partner")
    service.grant(bot_id="b-2", owner_id="u-1", app_id=42, app_name="partner")

    bots.delete("u-1", "b-1")

    assert [r.bot_id for r in service.list_for_app(app_id=42, owner_id="u-1")] == ["b-2"]


def test_list_for_app_filters_in_one_query_not_one_per_grant(service, bots):
    """The filter must not scale its round trips with the grant count.

    An earlier revision called ``get_by_id_and_owner`` per grant and justified
    it as bounded; the bound was not real, and this route is unpaginated and
    synchronous. Counting queries is the only way to keep the fix from
    regressing into the shape it replaced.
    """
    service.grant(bot_id="b-1", owner_id="u-1", app_id=42, app_name="partner")
    service.grant(bot_id="b-2", owner_id="u-1", app_id=42, app_name="partner")
    bots.queries = 0

    service.list_for_app(app_id=42, owner_id="u-1")

    assert bots.queries == 1


def test_list_for_app_makes_no_bot_query_when_there_are_no_grants(service, bots):
    """Nothing to filter, so nothing to ask the bot repository."""
    bots.queries = 0

    assert service.list_for_app(app_id=42, owner_id="u-1") == []
    assert bots.queries == 0


def test_list_for_app_is_scoped_to_the_calling_owner(service):
    """One app, granted by two owners, sees only the calling owner's bots."""
    service.grant(bot_id="b-1", owner_id="u-1", app_id=42, app_name="partner")
    service.grant(bot_id="b-2", owner_id="u-2", app_id=42, app_name="partner")

    assert [r.bot_id for r in service.list_for_app(app_id=42, owner_id="u-1")] == ["b-1"]
    assert [r.bot_id for r in service.list_for_app(app_id=42, owner_id="u-2")] == ["b-2"]


def test_list_for_bot_is_scoped_to_the_owning_user(service):
    service.grant(bot_id="b-1", owner_id="u-1", app_id=42, app_name="partner")

    assert service.list_for_bot(bot_id="b-1", owner_id="u-2") == []


def test_one_bot_authorization_conveys_nothing_about_another(service):
    service.grant(bot_id="b-1", owner_id="u-1", app_id=42, app_name="partner")

    assert service.list_for_bot(bot_id="b-2", owner_id="u-1") == []


def test_grant_ignores_a_caller_supplied_env(repo, sessions):
    """A caller-chosen ``env`` would write an unreachable row.

    It would be live, invisible to every read (which all use the process env),
    impossible to revoke, and would still occupy the unique key. The repository
    takes ``env`` from the process, so the extra key is inert.
    """
    written = repo.grant({**GRANT, "env": "pre"})

    assert repo.find("b-1", "u-1", 42) is not None, "must stay findable"
    assert repo.revoke("b-1", "u-1", 42) is True, "must stay revocable"
    assert written.env != "pre"


def test_losing_the_insert_race_returns_the_winners_row(repo, monkeypatch, sessions):
    """Concurrency, not just sequence: the loser must not get a 500.

    Two callers can both pass the existence check, because the row they are
    looking for does not exist and so cannot be locked. The loser hits the
    unique key, and the state it asked for now holds — which is what
    idempotency promises. Simulated by blinding the first check to a row that
    really is there.
    """
    winner = repo.grant(dict(GRANT))

    real_live_row = BotAppGrantRepository._live_row
    calls = {"n": 0}

    def blind_first_check(self, db, bot_id, owner_id, app_id, env, *, lock=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_live_row(self, db, bot_id, owner_id, app_id, env, lock=lock)

    monkeypatch.setattr(BotAppGrantRepository, "_live_row", blind_first_check)

    loser = repo.grant(dict(GRANT))

    assert loser.id == winner.id
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 1
    assert _log(sessions) == [GrantAction.GRANTED], "no phantom period"

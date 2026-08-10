"""Repository and service tests for user-granted bot authorizations.

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
from agentclaw.community.core.bot_app_grant.errors import (
    GrantIdentityTooLongError,
    GrantNotFoundError,
    GrantOwnerConflictError,
)
from agentclaw.community.core.bot_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    IDENTITY_MAX_LENGTH,
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
    """A bot repository double tracking which bots still exist.

    Mirrors ``filter_live_bots``, which is keyed on ``(bot_id, owner_id)``.
    Neither half alone identifies a bot: an owner-scoped filter would drop every
    *shared* bot (the case delegation exists for), and an id-only one would call
    a deleted bot live whenever another owner still holds one of the same id.

    ``queries`` counts calls, which is how the batching test proves the filter
    costs one query rather than one per grant.
    """

    def __init__(self):
        self.live: set[tuple[str, str]] = set()
        self.queries = 0

    def add(self, owner_id: str, *bot_ids: str) -> None:
        self.live.update((bot_id, owner_id) for bot_id in bot_ids)

    def delete(self, owner_id: str, bot_id: str) -> None:
        """Soft-deletion, as the caller sees it: the bot stops coming back."""
        self.live.discard((bot_id, owner_id))

    def filter_live_bots(self, pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
        if not pairs:
            return set()
        self.queries += 1
        return {pair for pair in pairs if pair in self.live}


@pytest.fixture
def bots():
    """Seeded with every bot the tests grant, so the default is "all live"."""
    doubles = _LiveBots()
    doubles.add("u-1", "b-1", "b-2")
    # ``u-2`` owns their own bots of the same ids — which is the situation the
    # pair-keyed filter exists for, not an artefact of the fixture.
    doubles.add("u-2", "b-1", "b-2")
    doubles.add("u-1", "b-9")
    return doubles


@pytest.fixture
def service(repo, bots):
    return BotAppGrantService(repo, bots)


GRANT = {
    "bot_id": "b-1",
    "user_id": "u-1",
    "owner_id": "u-1",
    "app_id": 42,
    "app_name": "partner",
}


def _log(sessions):
    with sessions() as session:
        return [
            row.action
            for row in session.query(BotAppGrantLogModel).order_by(
                BotAppGrantLogModel.id
            )
        ]


def _rows(sessions, model):
    with sessions() as session:
        return session.query(model).order_by(model.id).all()


def test_both_users_and_the_tenant_are_resolved_at_write_time(service, sessions):
    """The row must answer "as whom, on whose bot, in which tenant" on its own.

    The machine-caller path reads all three off the record rather than off the
    request, so they have to be on the row and they have to be resolved values.
    """
    record = service.grant(**GRANT)

    assert record.user_id == "u-1"
    assert record.owner_id == "u-1"
    assert record.avernet_tenant  # stamped by the guard, not passed in
    with sessions() as session:
        row = session.query(BotAppGrantModel).one()
        assert row.user_id == "u-1"
        assert row.owner_id == "u-1"
        assert row.avernet_tenant == record.avernet_tenant


def test_delegator_and_owner_are_recorded_separately_for_a_shared_bot(
    service, sessions
):
    """The two columns are two different people, and both must survive.

    ``user_id`` is who lent their access; ``owner_id`` is whose bot it is. A
    collaborator delegating on someone else's bot is the case the single-column
    record could not express at all.
    """
    record = service.grant(
        bot_id="b-9", user_id="u-2", owner_id="u-1", app_id=42, app_name="partner"
    )

    assert (record.user_id, record.owner_id) == ("u-2", "u-1")
    with sessions() as session:
        row = session.query(BotAppGrantModel).one()
        assert (row.user_id, row.owner_id) == ("u-2", "u-1")
    logged = _rows(sessions, BotAppGrantLogModel)[0]
    assert (logged.user_id, logged.owner_id) == ("u-2", "u-1")


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
    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)

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
    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)
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
    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)

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
        service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)


def test_revoke_is_not_silently_repeatable(service):
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)

    with pytest.raises(GrantNotFoundError):
        service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)


def test_list_excludes_revoked_grants(service):
    service.grant(**GRANT)
    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)

    assert service.list_for_bot(bot_id="b-1", owner_id="u-1") == []
    assert service.list_for_app(app_id=42, user_id="u-1") == []


def test_list_for_app_is_scoped_to_the_calling_app(service):
    """Two apps on one owner's bots see disjoint lists.

    A listing that silently widens is worse than one that fails, so each
    scoping dimension is pinned on its own.
    """
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-2", user_id="u-1", owner_id="u-1", app_id=99, app_name="other"
    )

    assert [r.bot_id for r in service.list_for_app(app_id=42, user_id="u-1")] == ["b-1"]
    assert [r.bot_id for r in service.list_for_app(app_id=99, user_id="u-1")] == ["b-2"]


def test_an_over_long_identity_is_refused_rather_than_truncated(service, sessions):
    """The opposite decision from ``app_name``, and for the stated reason.

    ``app_name`` is truncated because it is not identity. A user id **is** — it
    is what every app-only request resolves on — so truncating writes a row that
    looks live in every listing while no lookup can ever match it. The
    application would be unauthorized forever with nothing to say why. Refusing
    at consent time is the only outcome that tells anyone.

    The identity boundary really does admit ids this long: ``require_user_id``
    deliberately imposes no cap, because one there would lock a caller out of
    the whole surface for a value the gateway accepts.
    """
    too_long = "u" * (IDENTITY_MAX_LENGTH + 1)

    with pytest.raises(GrantIdentityTooLongError):
        service.grant(
            bot_id="b-1", user_id=too_long, owner_id="u-1", app_id=42, app_name="p"
        )
    with pytest.raises(GrantIdentityTooLongError):
        service.grant(
            bot_id="b-1", user_id="u-1", owner_id=too_long, app_id=42, app_name="p"
        )

    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 0, "and nothing written"


def test_an_identity_at_the_limit_is_accepted(service):
    """The boundary itself is usable — the refusal starts one character later."""
    at_limit = "u" * IDENTITY_MAX_LENGTH

    record = service.grant(
        bot_id="b-1", user_id=at_limit, owner_id="u-1", app_id=42, app_name="p"
    )

    assert record.user_id == at_limit


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
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name=long_name
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
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-2", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )

    bots.delete("u-1", "b-1")

    assert [r.bot_id for r in service.list_for_app(app_id=42, user_id="u-1")] == ["b-2"]


def test_a_deleted_bot_is_not_kept_alive_by_a_same_named_bot(service, bots):
    """Liveness on the bare id reports a deleted bot as live.

    ``u-1`` delegated an application on ``u-9``'s bot called ``default``, and
    that bot is then soft-deleted — by a path that bypasses the deletion sweep,
    which is a gap this codebase carries elsewhere. Another owner still holds a
    live bot of the same id, so an id-only liveness check finds one and calls
    the grant reachable: the application keeps being told it may reach a bot
    that no longer exists.

    The owner is on every grant record, so the pair costs nothing to check.
    """
    bots.add("u-9", "default")
    bots.add("u-3", "default")  # someone else's, still live
    service.grant(
        bot_id="default", user_id="u-1", owner_id="u-9", app_id=42, app_name="partner"
    )

    bots.delete("u-9", "default")

    assert service.list_for_app(app_id=42, user_id="u-1") == []


def test_list_for_app_filters_in_one_query_not_one_per_grant(service, bots):
    """The filter must not scale its round trips with the grant count.

    An earlier revision called ``get_by_id_and_owner`` per grant and justified
    it as bounded; the bound was not real, and this route is unpaginated and
    synchronous. Counting queries is the only way to keep the fix from
    regressing into the shape it replaced.
    """
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-2", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    bots.queries = 0

    service.list_for_app(app_id=42, user_id="u-1")

    assert bots.queries == 1


def test_list_for_app_makes_no_bot_query_when_there_are_no_grants(service, bots):
    """Nothing to filter, so nothing to ask the bot repository."""
    bots.queries = 0

    assert service.list_for_app(app_id=42, user_id="u-1") == []
    assert bots.queries == 0


def test_list_for_app_is_scoped_to_the_delegating_user(service):
    """One app, two delegators, sees only the named delegator's bots.

    The scope is the *delegation*, not the ownership: an application holding a
    grant from one user must not inherit another user's.
    """
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-2", user_id="u-2", owner_id="u-2", app_id=42, app_name="partner"
    )

    assert [r.bot_id for r in service.list_for_app(app_id=42, user_id="u-1")] == ["b-1"]
    assert [r.bot_id for r in service.list_for_app(app_id=42, user_id="u-2")] == ["b-2"]


def test_list_for_app_includes_a_bot_the_delegator_does_not_own(service, bots):
    """The case the owner-based filter silently dropped.

    ``b-3`` belongs to ``u-9`` and ``u-2`` collaborates on it. Filtering the
    result against ``u-2``'s *own* live bots — which is what this did while only
    owners could grant — removes exactly the bot the delegation exists to reach,
    and does it without an error.
    """
    bots.add("u-9", "b-3")
    service.grant(
        bot_id="b-3", user_id="u-2", owner_id="u-9", app_id=42, app_name="partner"
    )

    assert [r.bot_id for r in service.list_for_app(app_id=42, user_id="u-2")] == ["b-3"]


def test_list_for_bot_shows_every_delegation_whoever_made_it(service):
    """The bot's owner has to see a grant a collaborator made.

    Narrowing this to one delegating user would hide from an owner precisely the
    machine access they most need to know about — access to their own bot,
    arranged by someone else.
    """
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=99, app_name="other"
    )

    delegations = service.list_for_bot(bot_id="b-1", owner_id="u-1")

    assert {(r.user_id, r.app_id) for r in delegations} == {("u-1", 42), ("u-2", 99)}


def test_two_users_may_delegate_the_same_app_on_the_same_bot(service, sessions):
    """Two delegations, not one — and the second must not be swallowed.

    Keyed on the bot's owner these would collide, and the idempotent grant path
    would return the first user's row for the second user's request: an
    application bounded by the wrong person's access, with no error to notice.
    """
    first = service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    second = service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=42, app_name="partner"
    )

    assert first.id != second.id
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 2
    assert _log(sessions) == [GrantAction.GRANTED, GrantAction.GRANTED]


def test_one_users_withdrawal_leaves_the_others_delegation_working(service):
    """A collaborator withdraws their own loan, not a colleague's."""
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=42, app_name="partner"
    )

    service.revoke(bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42)

    assert service.list_for_app(app_id=42, user_id="u-1") == []
    assert [r.bot_id for r in service.list_for_app(app_id=42, user_id="u-2")] == ["b-1"]


def test_owner_override_withdraws_every_delegation_of_one_app(service, sessions):
    """"Revoke this app's access to my bot" means all of it.

    A withdrawal that left the application still reaching the bot through a
    colleague's grant would not be a withdrawal.
    """
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=99, app_name="other"
    )

    service.revoke_app(bot_id="b-1", owner_id="u-1", app_id=42)

    assert service.list_for_app(app_id=42, user_id="u-1") == []
    assert service.list_for_app(app_id=42, user_id="u-2") == []
    assert [r.app_id for r in service.list_for_bot(bot_id="b-1", owner_id="u-1")] == [99], (
        "a different application keeps its authorization"
    )
    assert _log(sessions).count(GrantAction.REVOKED) == 2, "one event per row"


def test_owner_override_on_nothing_raises_distinctly(service):
    """"Nothing to remove" must not read as "removed", here too."""
    with pytest.raises(GrantNotFoundError):
        service.revoke_app(bot_id="b-1", owner_id="u-1", app_id=42)


def test_deletion_sweep_withdraws_every_delegation_whoever_made_it(service, sessions):
    """A deleted bot has no authorizations, from anyone, to any application."""
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )
    service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=99, app_name="other"
    )
    service.grant(
        bot_id="b-2", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )

    assert service.revoke_all_for_bot(bot_id="b-1", owner_id="u-1") == 2

    assert service.list_for_bot(bot_id="b-1", owner_id="u-1") == []
    assert [r.bot_id for r in service.list_for_bot(bot_id="b-2", owner_id="u-1")] == ["b-2"], (
        "another bot's authorizations are untouched"
    )
    assert _log(sessions).count(GrantAction.REVOKED) == 2


def test_bot_scoped_reads_and_sweeps_do_not_cross_owners(service, sessions):
    """``bot_id`` is not unique across owners, and these operations must respect it.

    ``ac_bots`` carries no unique key on ``bot_id`` — the retired ``default``
    convention gave many owners a bot of that id — so "this bot" is
    ``(bot_id, owner_id)``. An earlier revision dropped ``owner_id`` from the
    bot-scoped read and both sweeps while dropping the *delegating-user* scope,
    conflating two different columns. The result: one owner could read the
    applications authorized on a stranger's same-named bot, and deleting their
    own bot would hard-delete the stranger's live grants and log revocations for
    them — silently killing an unrelated integration.
    """
    service.grant(
        bot_id="default", user_id="u-1", owner_id="u-1", app_id=42, app_name="mine"
    )
    service.grant(
        bot_id="default", user_id="u-2", owner_id="u-2", app_id=42, app_name="theirs"
    )

    assert [r.app_name for r in service.list_for_bot(bot_id="default", owner_id="u-1")] == [
        "mine"
    ], "one owner must not see what is authorized on another's same-named bot"

    assert service.revoke_all_for_bot(bot_id="default", owner_id="u-1") == 1

    survivors = service.list_for_bot(bot_id="default", owner_id="u-2")
    assert [r.app_name for r in survivors] == ["theirs"], (
        "deleting one owner's bot must not withdraw a stranger's authorizations"
    )
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 1


def test_withdrawing_one_bot_does_not_destroy_a_same_named_bots_delegation(
    service, sessions
):
    """A delete keyed on ``bot_id`` alone destroys the wrong authorization.

    ``u-1`` collaborates on ``u-9``'s bot called ``default`` and has delegated
    an application on it. ``u-1`` also owns a bot called ``default``, with no
    delegation of its own — the unique key is per
    ``(app, bot_id, delegating user)``, so they could not hold two anyway.

    Withdrawing on their *own* ``default`` matches the surviving row on every
    column that key holds. Unlike a colliding read, which fails safe by handing
    back access the caller already had, a colliding delete destroys a live
    authorization on a bot the caller never addressed and writes a revocation
    event for it. The resolved owner tells the two apart, and the caller always
    knows it.
    """
    service.grant(
        bot_id="default", user_id="u-1", owner_id="u-9", app_id=42, app_name="theirs"
    )

    with pytest.raises(GrantNotFoundError):
        service.revoke(bot_id="default", user_id="u-1", owner_id="u-1", app_id=42)

    with sessions() as session:
        survivors = session.query(BotAppGrantModel).all()
    assert [(r.owner_id, r.app_name) for r in survivors] == [("u-9", "theirs")], (
        "the shared bot's delegation must survive a withdrawal aimed elsewhere"
    )
    assert GrantAction.REVOKED not in _log(sessions), "and no revocation logged"


def test_withdrawing_the_bot_the_delegation_names_still_works(service, sessions):
    """The narrowing must not break the ordinary withdrawal it scopes."""
    service.grant(
        bot_id="default", user_id="u-1", owner_id="u-9", app_id=42, app_name="theirs"
    )

    service.revoke(bot_id="default", user_id="u-1", owner_id="u-9", app_id=42)

    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 0
    assert GrantAction.REVOKED in _log(sessions)


def test_owner_override_does_not_cross_owners(service):
    """The same identity rule, on the owner's outright revocation."""
    service.grant(
        bot_id="default", user_id="u-1", owner_id="u-1", app_id=42, app_name="mine"
    )
    service.grant(
        bot_id="default", user_id="u-2", owner_id="u-2", app_id=42, app_name="theirs"
    )

    service.revoke_app(bot_id="default", owner_id="u-1", app_id=42)

    assert [r.app_name for r in service.list_for_bot(bot_id="default", owner_id="u-2")] == [
        "theirs"
    ]


def test_deletion_sweep_of_an_unauthorized_bot_is_not_an_error(service):
    """Deleting a bot no application could reach is an ordinary deletion.

    Unlike the two withdrawals, this reports a count rather than answering a
    request to remove one named thing — so zero is an answer, not a failure.
    """
    assert service.revoke_all_for_bot(bot_id="b-1", owner_id="u-1") == 0


def test_sweeps_record_the_delegating_user_in_the_history(service, sessions):
    """The audit has to answer "who let this application in" after the fact.

    Both sweeps build their log rows from the live rows rather than from their
    arguments, which is the only way they *can* record a delegator they were
    never told about.
    """
    service.grant(
        bot_id="b-1", user_id="u-2", owner_id="u-1", app_id=42, app_name="partner"
    )

    service.revoke_all_for_bot(bot_id="b-1", owner_id="u-1")

    revoked = [
        row for row in _rows(sessions, BotAppGrantLogModel)
        if row.action == GrantAction.REVOKED
    ]
    assert [(r.user_id, r.owner_id, r.app_name) for r in revoked] == [
        ("u-2", "u-1", "partner")
    ]


def test_one_bot_authorization_conveys_nothing_about_another(service):
    service.grant(
        bot_id="b-1", user_id="u-1", owner_id="u-1", app_id=42, app_name="partner"
    )

    assert service.list_for_bot(bot_id="b-2", owner_id="u-1") == []


def test_grant_ignores_a_caller_supplied_env(repo, sessions):
    """A caller-chosen ``env`` would write an unreachable row.

    It would be live, invisible to every read (which all use the process env),
    impossible to revoke, and would still occupy the unique key. The repository
    takes ``env`` from the process, so the extra key is inert.
    """
    written = repo.grant({**GRANT, "env": "pre"})

    assert repo.find("b-1", "u-1", 42) is not None, "must stay findable"
    assert repo.revoke("b-1", "u-1", 42, "u-1") is True, "must stay revocable"
    assert written.env != "pre"


def test_regranting_onto_another_owners_same_named_bot_is_refused(service, sessions):
    """The slot collision, refused loudly instead of reported as success.

    The unique key is ``(tenant, app_id, bot_id, user_id, env)`` — no owner, for
    the byte budget — so one user has one slot per ``bot_id`` per application,
    while ``bot_id`` is not unique across owners.

    Reaching this needs the caller's *reach* to change, not two bots addressed
    at once: the resolve is deterministic on ``(bot_id, caller)``, so a caller
    cannot name the second bot while the first is still resolvable. Delegate one
    owner's ``default``, lose that collaboration, gain another owner's, delegate
    again — and the second grant lands on the first's slot.

    Reported as an idempotent success it would be a lie in the way that matters:
    the caller is told their application may act on the bot they named, the row
    says a different bot, and the owner comparison at request time then refuses
    the access they were told they had — silently, permanently, with the grant
    showing as live in every listing.
    """
    service.grant(
        bot_id="default", user_id="u-1", owner_id="owner-a", app_id=42, app_name="p"
    )

    with pytest.raises(GrantOwnerConflictError):
        service.grant(
            bot_id="default",
            user_id="u-1",
            owner_id="owner-b",
            app_id=42,
            app_name="p",
        )

    rows = _rows(sessions, BotAppGrantModel)
    assert [row.owner_id for row in rows] == ["owner-a"], "the live grant is untouched"
    assert _log(sessions) == [GrantAction.GRANTED], "no phantom second period"


def test_withdrawing_first_lets_the_other_owners_bot_be_delegated(service, sessions):
    """The remedy the refusal points at actually works.

    A refusal with no way forward would just be the silent failure made loud.
    Withdrawing frees the slot, and the second bot can then be delegated.
    """
    service.grant(
        bot_id="default", user_id="u-1", owner_id="owner-a", app_id=42, app_name="p"
    )
    service.revoke(bot_id="default", user_id="u-1", owner_id="owner-a", app_id=42)

    record = service.grant(
        bot_id="default", user_id="u-1", owner_id="owner-b", app_id=42, app_name="p"
    )

    assert record.owner_id == "owner-b"
    assert [row.owner_id for row in _rows(sessions, BotAppGrantModel)] == ["owner-b"]


def test_the_same_owners_bot_is_still_idempotent(service, sessions):
    """The guard must not turn an ordinary repeat into a conflict.

    Same owner, same everything: the caller asked for a state that already
    holds, and a partner retrying a timed-out request must not get an error for
    a request that succeeded.
    """
    first = service.grant(
        bot_id="default", user_id="u-1", owner_id="owner-a", app_id=42, app_name="p"
    )
    second = service.grant(
        bot_id="default", user_id="u-1", owner_id="owner-a", app_id=42, app_name="p"
    )

    assert first.id == second.id
    assert len(_rows(sessions, BotAppGrantModel)) == 1


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

    def blind_first_check(self, db, bot_id, user_id, app_id, env, *, lock=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_live_row(self, db, bot_id, user_id, app_id, env, lock=lock)

    monkeypatch.setattr(BotAppGrantRepository, "_live_row", blind_first_check)

    loser = repo.grant(dict(GRANT))

    assert loser.id == winner.id
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 1
    assert _log(sessions) == [GrantAction.GRANTED], "no phantom period"

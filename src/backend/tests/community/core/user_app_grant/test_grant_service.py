"""Repository and service tests for user-granted account-level authorizations.

Run against a real SQLite database rather than a mock repository, for the
reason the bot-level tests give: the behaviours worth pinning are database
behaviours — a unique key that has to survive a second withdrawal, an
append-only log that has to outlive the row it describes, and an insert race
that has to resolve to the winner's row instead of a 500.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.bot.user_app_grant import (
    UserAppGrantRepository,
)
from agentclaw.community.core.user_app_grant.errors import (
    UserGrantIdentityTooLongError,
    UserGrantNotFoundError,
)
from agentclaw.community.core.user_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    IDENTITY_MAX_LENGTH,
    UserAppGrantLogModel,
    UserAppGrantModel,
    UserGrantAction,
)
from agentclaw.community.core.user_app_grant.services import UserAppGrantService


@pytest.fixture
def sessions():
    """A fresh in-memory database with only this feature's two tables."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[UserAppGrantModel.__table__, UserAppGrantLogModel.__table__]
    )
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def db(sessions):
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
    return UserAppGrantRepository(db)


@pytest.fixture
def service(repo):
    return UserAppGrantService(repo)


USER = "u-1"
APP = 42


def _log_rows(sessions):
    with sessions() as s:
        return [
            (row.app_id, row.user_id, row.action)
            for row in s.query(UserAppGrantLogModel).order_by(UserAppGrantLogModel.id)
        ]


# ── granting ─────────────────────────────────────────────────────────────────


def test_grant_creates_a_live_row_and_a_granted_event(service, sessions):
    record = service.grant(user_id=USER, app_id=APP, app_name="partner")

    assert (record.app_id, record.user_id, record.app_name) == (APP, USER, "partner")
    assert service.find(user_id=USER, app_id=APP) is not None
    assert _log_rows(sessions) == [(APP, USER, UserGrantAction.GRANTED)]


def test_regranting_is_idempotent_and_keeps_the_start_time(service, sessions):
    first = service.grant(user_id=USER, app_id=APP, app_name="partner")
    second = service.grant(user_id=USER, app_id=APP, app_name="partner renamed")

    assert second.id == first.id
    assert second.gmt_create == first.gmt_create
    # The snapshot is what was consented to; a later rename does not rewrite it.
    assert second.app_name == "partner"
    assert len(service.list_for_user(user_id=USER)) == 1
    assert _log_rows(sessions) == [(APP, USER, UserGrantAction.GRANTED)]


def test_two_users_may_each_authorize_the_same_app(service):
    service.grant(user_id=USER, app_id=APP, app_name="partner")
    service.grant(user_id="u-2", app_id=APP, app_name="partner")

    assert service.find(user_id=USER, app_id=APP) is not None
    assert service.find(user_id="u-2", app_id=APP) is not None
    assert service.find(user_id="u-3", app_id=APP) is None


def test_a_long_app_name_is_truncated_not_refused(service):
    record = service.grant(user_id=USER, app_id=APP, app_name="n" * 5000)

    assert len(record.app_name) == APP_NAME_MAX_LENGTH


def test_a_user_id_too_long_to_store_is_refused(service):
    with pytest.raises(UserGrantIdentityTooLongError):
        service.grant(user_id="u" * (IDENTITY_MAX_LENGTH + 1), app_id=APP, app_name="p")
    assert service.list_for_user(user_id="u" * (IDENTITY_MAX_LENGTH + 1)) == []


def test_the_insert_race_resolves_to_the_winners_row(repo, sessions):
    """The loser of a concurrent grant gets the live row, not a 500."""
    calls = {"n": 0}
    original = repo._insert_grant

    def racing(app_id, app_name, user_id, env):
        calls["n"] += 1
        if calls["n"] == 1:
            # Someone else commits between our existence check and our insert.
            original(app_id, "winner", user_id, env)
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return original(app_id, app_name, user_id, env)

    repo._insert_grant = racing
    record = repo.grant({"app_id": APP, "app_name": "loser", "user_id": USER})

    assert record.app_name == "winner"
    assert _log_rows(sessions) == [(APP, USER, UserGrantAction.GRANTED)]


# ── withdrawing ──────────────────────────────────────────────────────────────


def test_revoke_deletes_the_live_row_and_logs_the_revocation(service, sessions):
    service.grant(user_id=USER, app_id=APP, app_name="partner")

    service.revoke(user_id=USER, app_id=APP)

    assert service.find(user_id=USER, app_id=APP) is None
    assert service.list_for_user(user_id=USER) == []
    assert _log_rows(sessions) == [
        (APP, USER, UserGrantAction.GRANTED),
        (APP, USER, UserGrantAction.REVOKED),
    ]


def test_revoking_nothing_is_distinguishable_from_revoking_something(service):
    with pytest.raises(UserGrantNotFoundError):
        service.revoke(user_id=USER, app_id=APP)


def test_revoke_is_scoped_to_one_user(service):
    service.grant(user_id=USER, app_id=APP, app_name="partner")
    service.grant(user_id="u-2", app_id=APP, app_name="partner")

    service.revoke(user_id=USER, app_id=APP)

    assert service.find(user_id=USER, app_id=APP) is None
    assert service.find(user_id="u-2", app_id=APP) is not None


def test_grant_withdraw_grant_withdraw_survives_the_unique_key(service, sessions):
    """The two-table split: the second withdrawal must not collide."""
    for _ in range(2):
        service.grant(user_id=USER, app_id=APP, app_name="partner")
        service.revoke(user_id=USER, app_id=APP)

    assert service.find(user_id=USER, app_id=APP) is None
    assert [action for _, _, action in _log_rows(sessions)] == [
        UserGrantAction.GRANTED,
        UserGrantAction.REVOKED,
        UserGrantAction.GRANTED,
        UserGrantAction.REVOKED,
    ]


# ── reads ────────────────────────────────────────────────────────────────────


def test_list_for_user_is_the_users_own_view(service):
    service.grant(user_id=USER, app_id=APP, app_name="partner")
    service.grant(user_id=USER, app_id=43, app_name="other")
    service.grant(user_id="u-2", app_id=44, app_name="theirs")

    listed = service.list_for_user(user_id=USER)

    assert sorted(r.app_id for r in listed) == [APP, 43]

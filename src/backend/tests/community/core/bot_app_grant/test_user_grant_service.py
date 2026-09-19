"""Repository and service tests for user-level application delegations.

Against a real SQLite database for the reason ``test_grant_service.py`` gives:
the behaviours worth pinning are database behaviours — a unique key that has
to survive grant → withdraw → grant, an append-only log that has to outlive
the row it describes, and an idempotent grant that must not open a new period.

And one behaviour that belongs to the *bot* grant service but exists for this
record: ``grant_for_creation`` writes a bot grant for a bot that is not live
yet, which ``grant`` refuses.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_app_grant.errors import (
    GrantBotNotLiveError,
    GrantIdentityTooLongError,
    GrantNotFoundError,
)
from agentclaw.community.core.bot_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    IDENTITY_MAX_LENGTH,
    BotAppGrantLogModel,
    BotAppGrantModel,
    GrantAction,
    UserAppGrantLogModel,
    UserAppGrantModel,
)
from agentclaw.community.core.bot_app_grant.services import (
    BotAppGrantService,
    UserAppGrantService,
)
from agentclaw.community.core.repository.implementations.bot.app_grant import (
    BotAppGrantRepository,
)
from agentclaw.community.core.repository.implementations.bot.user_app_grant import (
    UserAppGrantRepository,
)

USER = "u-1"
OTHER = "u-2"
APP = 42
OTHER_APP = 43


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            UserAppGrantModel.__table__,
            UserAppGrantLogModel.__table__,
            BotAppGrantModel.__table__,
            BotAppGrantLogModel.__table__,
        ],
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
def service(db):
    return UserAppGrantService(repository=UserAppGrantRepository(db))


def _log_rows(sessions):
    with sessions() as s:
        return [
            (row.app_id, row.user_id, row.action)
            for row in s.query(UserAppGrantLogModel).order_by(UserAppGrantLogModel.id)
        ]


# ── the user-level delegation ────────────────────────────────────────────────


def test_a_grant_is_findable_and_listed(service):
    record = service.grant(user_id=USER, app_id=APP, app_name="partner")

    assert record.app_id == APP
    assert record.user_id == USER
    assert record.app_name == "partner"
    assert service.find(user_id=USER, app_id=APP) is not None
    assert [r.app_id for r in service.list_for_user(user_id=USER)] == [APP]


def test_a_delegation_is_scoped_to_the_pair(service):
    service.grant(user_id=USER, app_id=APP, app_name="partner")

    assert service.find(user_id=OTHER, app_id=APP) is None
    assert service.find(user_id=USER, app_id=OTHER_APP) is None
    assert service.list_for_user(user_id=OTHER) == []


def test_regranting_is_idempotent_and_keeps_the_original_period(service, sessions):
    first = service.grant(user_id=USER, app_id=APP, app_name="partner")
    second = service.grant(user_id=USER, app_id=APP, app_name="renamed")

    assert second.id == first.id
    assert second.gmt_create == first.gmt_create
    # The snapshot is the consent-time name, and a repeat is not a new event.
    assert second.app_name == "partner"
    assert _log_rows(sessions) == [(APP, USER, GrantAction.GRANTED)]


def test_withdrawal_removes_the_row_and_keeps_the_history(service, sessions):
    service.grant(user_id=USER, app_id=APP, app_name="partner")

    service.revoke(user_id=USER, app_id=APP)

    assert service.find(user_id=USER, app_id=APP) is None
    assert _log_rows(sessions) == [
        (APP, USER, GrantAction.GRANTED),
        (APP, USER, GrantAction.REVOKED),
    ]


def test_withdrawing_nothing_is_distinct_from_withdrawing_something(service):
    with pytest.raises(GrantNotFoundError):
        service.revoke(user_id=USER, app_id=APP)


def test_grant_withdraw_grant_withdraw_survives_the_unique_key(service, sessions):
    """The two-table split: the second withdrawal must not collide."""
    for _ in range(2):
        service.grant(user_id=USER, app_id=APP, app_name="partner")
        service.revoke(user_id=USER, app_id=APP)

    assert service.find(user_id=USER, app_id=APP) is None
    assert len(_log_rows(sessions)) == 4


def test_an_over_long_app_name_is_truncated_not_refused(service):
    record = service.grant(
        user_id=USER, app_id=APP, app_name="n" * (APP_NAME_MAX_LENGTH + 5)
    )
    assert len(record.app_name) == APP_NAME_MAX_LENGTH


def test_an_over_long_user_id_is_refused_not_truncated(service):
    with pytest.raises(GrantIdentityTooLongError):
        service.grant(
            user_id="u" * (IDENTITY_MAX_LENGTH + 1), app_id=APP, app_name="partner"
        )
    assert service.list_for_user(user_id="u" * (IDENTITY_MAX_LENGTH + 1)) == []


# ── the creation grant on the bot record ─────────────────────────────────────


class _NoLiveBots:
    """A bot repository double under which no bot is live — the state a
    creation is in when its grant is written."""

    def filter_live_bots(self, pairs):
        return set()


@pytest.fixture
def bot_grants(db):
    return BotAppGrantService(
        repository=BotAppGrantRepository(db), bots=_NoLiveBots()
    )


def test_the_ordinary_grant_refuses_a_bot_that_is_not_live(bot_grants):
    with pytest.raises(GrantBotNotLiveError):
        bot_grants.grant(
            bot_id="b-new", user_id=USER, owner_id=USER, app_id=APP, app_name="p"
        )


def test_the_creation_grant_is_written_before_the_bot_is_live(bot_grants):
    """The row the polls of a pending creation are authorized against."""
    record = bot_grants.grant_for_creation(
        bot_id="b-new", user_id=USER, owner_id=USER, app_id=APP, app_name="p"
    )

    assert record.bot_id == "b-new"
    assert record.owner_id == USER
    found = bot_grants.find(bot_id="b-new", owner_id=USER, user_id=USER, app_id=APP)
    assert found is not None and found.id == record.id


def test_the_creation_grant_stays_out_of_the_apps_listing_until_the_bot_is_live(
    bot_grants,
):
    """Inert while the bot does not exist: ``list_for_app`` filters it."""
    bot_grants.grant_for_creation(
        bot_id="b-new", user_id=USER, owner_id=USER, app_id=APP, app_name="p"
    )
    assert bot_grants.list_for_app(app_id=APP, user_id=USER) == []


def test_the_creation_grant_is_withdrawable_like_any_other(bot_grants):
    bot_grants.grant_for_creation(
        bot_id="b-new", user_id=USER, owner_id=USER, app_id=APP, app_name="p"
    )
    bot_grants.revoke(bot_id="b-new", user_id=USER, owner_id=USER, app_id=APP)
    assert (
        bot_grants.find(bot_id="b-new", owner_id=USER, user_id=USER, app_id=APP)
        is None
    )


def test_the_creation_grant_is_swept_with_the_bot(bot_grants):
    """What the manifest creation job calls when a creation gives up."""
    bot_grants.grant_for_creation(
        bot_id="b-new", user_id=USER, owner_id=USER, app_id=APP, app_name="p"
    )
    assert bot_grants.revoke_all_for_bot(bot_id="b-new", owner_id=USER) == 1
    assert (
        bot_grants.find(bot_id="b-new", owner_id=USER, user_id=USER, app_id=APP)
        is None
    )


def test_the_creation_grant_refuses_an_over_long_identity(bot_grants):
    with pytest.raises(GrantIdentityTooLongError):
        bot_grants.grant_for_creation(
            bot_id="b-new",
            user_id="u" * (IDENTITY_MAX_LENGTH + 1),
            owner_id=USER,
            app_id=APP,
            app_name="p",
        )

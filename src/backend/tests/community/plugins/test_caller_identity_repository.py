"""Regression tests for transactional Caller identity lock fencing."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.caller_identity.repository import (
    CallerIdentityLockMismatchError,
)
from agentclaw.community.plugins.caller_identity_repository import (
    CallerIdentityRepository,
)


class InMemorySqliteDB:
    """Expose the transaction seam used by Caller identity persistence."""

    def __init__(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def transactional_orm_session(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def database() -> InMemorySqliteDB:
    return InMemorySqliteDB()


@pytest.fixture
def repository(database: InMemorySqliteDB) -> CallerIdentityRepository:
    return CallerIdentityRepository(database)


def _add_lock(database: InMemorySqliteDB) -> None:
    with database.transactional_orm_session() as session:
        session.add(
            BotCollabLockModel(
                lock_key="bot:1",
                holder_user_id="other-editor",
                env="dev",
            )
        )


def _replace_call_type(
    repository: CallerIdentityRepository, lock_epoch: int | None
) -> None:
    repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=lock_epoch,
    )


def _compensate_call_type(
    repository: CallerIdentityRepository, lock_epoch: int | None
) -> None:
    repository.compensate_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        previous_explicit_call_type=None,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        expected_revision=1,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=lock_epoch,
    )


def test_replace_rejects_missing_exact_lock(
    repository: CallerIdentityRepository,
) -> None:
    with pytest.raises(CallerIdentityLockMismatchError):
        _replace_call_type(repository, lock_epoch=1)


def test_replace_rejects_lock_created_before_unlocked_write(
    repository: CallerIdentityRepository, database: InMemorySqliteDB
) -> None:
    _add_lock(database)

    with pytest.raises(CallerIdentityLockMismatchError):
        _replace_call_type(repository, lock_epoch=None)


def test_compensation_rejects_missing_exact_lock(
    repository: CallerIdentityRepository,
) -> None:
    with pytest.raises(CallerIdentityLockMismatchError):
        _compensate_call_type(repository, lock_epoch=1)


def test_compensation_rejects_lock_created_before_unlocked_rollback(
    repository: CallerIdentityRepository, database: InMemorySqliteDB
) -> None:
    _add_lock(database)

    with pytest.raises(CallerIdentityLockMismatchError):
        _compensate_call_type(repository, lock_epoch=None)

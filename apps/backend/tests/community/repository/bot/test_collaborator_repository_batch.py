from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.bot.collaborator import (
    CollaboratorRepository,
)
from agentclaw.community.core.repository.implementations.bot.collab_lock import (
    BotCollabLockRepository,
)


class _InMemorySqliteDB:
    def __init__(self, engine) -> None:
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def repo():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return CollaboratorRepository(_InMemorySqliteDB(engine))


@pytest.fixture
def lock_repo():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return BotCollabLockRepository(_InMemorySqliteDB(engine))


def _insert(repo, *, bot_pk: int, bot_id: str, owner_id: str, user_id: str):
    return repo.insert(
        {
            "bot_pk": bot_pk,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "user_id": user_id,
            "user_name": user_id,
            "operator_id": owner_id,
            "env": "dev",
        }
    )


def test_list_by_bot_owner_pairs_filters_cross_product_matches(repo) -> None:
    _insert(
        repo,
        bot_pk=1,
        bot_id="shared-id",
        owner_id="owner-1",
        user_id="editor-1",
    )
    _insert(
        repo,
        bot_pk=2,
        bot_id="service-2",
        owner_id="owner-2",
        user_id="editor-2",
    )
    _insert(
        repo,
        bot_pk=3,
        bot_id="shared-id",
        owner_id="owner-2",
        user_id="cross-product-only",
    )

    records = repo.list_by_bot_owner_pairs(
        [("shared-id", "owner-1"), ("service-2", "owner-2")],
        "dev",
    )

    assert {(record.bot_id, record.owner_id, record.user_id) for record in records} == {
        ("shared-id", "owner-1", "editor-1"),
        ("service-2", "owner-2", "editor-2"),
    }


def test_list_by_bot_owner_pairs_skips_database_for_empty_input(repo) -> None:
    assert repo.list_by_bot_owner_pairs([], "dev") == []


def test_list_by_keys_returns_only_requested_locks(lock_repo, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "dev")
    lock_repo.acquire("lock-key-001", "user-001")
    lock_repo.acquire("lock-key-002", "user-002")
    lock_repo.acquire("lock-key-003", "user-003")

    records = lock_repo.list_by_keys(["lock-key-001", "lock-key-003"])

    assert {record.lock_key for record in records} == {
        "lock-key-001",
        "lock-key-003",
    }


def test_list_by_keys_skips_database_for_empty_input(lock_repo) -> None:
    assert lock_repo.list_by_keys([]) == []

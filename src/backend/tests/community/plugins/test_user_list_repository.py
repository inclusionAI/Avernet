"""Exact, environment-scoped behavior for the frontend user-list repository."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.plugins.user_list_repository import UserListRepository
from agentclaw.community.utils.env_utils import get_current_env


class _SqliteDatabase:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def repository(tmp_path) -> UserListRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'user-list.db'}")
    EntityUserListModel.__table__.create(engine)
    return UserListRepository(_SqliteDatabase(engine))


def _insert(
    repository: UserListRepository,
    *,
    entity_id: str,
    user_list_type: str,
    env: str,
) -> None:
    with repository._db.orm_session() as session:
        session.add(
            EntityUserListModel(
                entity_id=entity_id,
                user_list_type=user_list_type,
                env=env,
            )
        )


def test_exists_requires_an_exact_current_environment_scope(repository):
    current_env = get_current_env()
    _insert(
        repository,
        entity_id="member",
        user_list_type="caller_identity",
        env=current_env,
    )

    assert repository.exists(
        entity_id="member",
        user_list_type="caller_identity",
    )
    assert not repository.exists(
        entity_id="another_member",
        user_list_type="caller_identity",
    )
    assert not repository.exists(
        entity_id="member",
        user_list_type="another_feature",
    )


def test_exists_does_not_match_the_same_member_in_another_environment(repository):
    current_env = get_current_env()
    other_env = "prod" if current_env != "prod" else "dev"
    _insert(
        repository,
        entity_id="member",
        user_list_type="caller_identity",
        env=other_env,
    )

    assert not repository.exists(
        entity_id="member",
        user_list_type="caller_identity",
    )


def test_set_membership_upserts_then_removes_only_the_current_environment(repository):
    current_env = get_current_env()
    other_env = "prod" if current_env != "prod" else "dev"
    _insert(
        repository,
        entity_id="member",
        user_list_type="caller_identity",
        env=other_env,
    )

    repository.set_membership(
        entity_id="member",
        user_list_type="caller_identity",
        in_whitelist=True,
    )
    assert repository.exists(
        entity_id="member",
        user_list_type="caller_identity",
    )

    repository.set_membership(
        entity_id="member",
        user_list_type="caller_identity",
        in_whitelist=False,
    )
    assert not repository.exists(
        entity_id="member",
        user_list_type="caller_identity",
    )
    with repository._db.orm_session() as session:
        assert session.query(EntityUserListModel).filter(
            EntityUserListModel.env == other_env,
        ).count() == 1

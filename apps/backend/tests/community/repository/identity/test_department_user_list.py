"""Legacy case-insensitive unique keys must never mutate another department."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Query

from agentclaw.community.core.repository.implementations.identity.user_list import UserListRepository
from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.core.user_list.rules import MembershipConflictError
from tests.community.repository.identity.test_user_list_repository import _SqliteDatabase


@pytest.fixture
def nocase_repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nocase.db'}")
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE ac_entity_user_list (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entity_id VARCHAR(1024) COLLATE NOCASE NOT NULL,
          user_list_type VARCHAR(64) NOT NULL, env VARCHAR(20) NOT NULL,
          subject_type VARCHAR(20) NOT NULL DEFAULT 'user',
          bot_type VARCHAR(20) NOT NULL DEFAULT 'all',
          gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(entity_id, user_list_type, env, subject_type, bot_type)
        )"""))
    repository = UserListRepository(_SqliteDatabase(engine))
    repository.set_membership(entity_id="Group/Platform", subject_type="department", user_list_type="feature", env="pre", in_whitelist=True)
    with repository._db.orm_session() as session:
        session.query(EntityUserListModel).update({EntityUserListModel.gmt_modified: datetime(2000, 1, 1)})
    yield repository
    engine.dispose()


@pytest.mark.parametrize("enabled", [True, False])
def test_case_colliding_department_write_is_explicitly_rejected(nocase_repository, enabled):
    with pytest.raises(MembershipConflictError):
        nocase_repository.set_membership(entity_id="Group/platform", subject_type="department", user_list_type="feature", env="pre", in_whitelist=enabled)
    with nocase_repository._db.orm_session() as session:
        row = session.query(EntityUserListModel).one()
        assert row.entity_id == "Group/Platform"
        assert row.gmt_modified == datetime(2000, 1, 1)
    assert not nocase_repository.exists(entity_id="Group/platform", subject_type="department", user_list_type="feature", env="pre")
    assert not nocase_repository.exists_any(entity_ids=("Group/platform",), subject_type="department", user_list_type="feature", env="pre", bot_types=("personal", "all"))


def test_case_collision_after_precheck_rolls_back_upsert(nocase_repository, monkeypatch):
    original_first = Query.first
    calls = 0

    def hide_competing_row_once(query):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original_first(query)

    monkeypatch.setattr(Query, "first", hide_competing_row_once)
    with pytest.raises(MembershipConflictError):
        nocase_repository.set_membership(entity_id="Group/platform", subject_type="department", user_list_type="feature", env="pre", in_whitelist=True)
    with nocase_repository._db.orm_session() as session:
        row = session.query(EntityUserListModel).one()
        assert row.entity_id == "Group/Platform"
        assert row.gmt_modified == datetime(2000, 1, 1)


def test_old_user_comparison_preserves_database_collation(nocase_repository):
    nocase_repository.set_membership(entity_id="Staff", user_list_type="feature", env="pre", in_whitelist=True)
    assert nocase_repository.exists(entity_id="staff", user_list_type="feature", env="pre")


def test_mysql_department_comparison_compiles_to_binary():
    from sqlalchemy import select
    from sqlalchemy.dialects import mysql
    from unittest.mock import Mock
    session = Mock()
    session.get_bind.return_value.dialect.name = "mysql"
    column = UserListRepository._entity_column(session, "department")
    statement = select(EntityUserListModel.id).where(column.in_(("Group/Platform",)))
    compiled = statement.compile(dialect=mysql.dialect())
    assert "CAST(ac_entity_user_list.entity_id AS BINARY)" in str(compiled)
    assert "Group/Platform" not in str(compiled)
    assert list(compiled.params.values()) == [["Group/Platform"]]

"""Department matching follows the database string collation."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Query

from agentclaw.community.core.repository.implementations.identity.user_list import UserListRepository
from agentclaw.community.core.user_list.models import EntityUserListModel
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


def test_department_read_and_candidates_follow_database_collation(nocase_repository):
    assert nocase_repository.exists(entity_id="group/platform", subject_type="department", user_list_type="feature", env="pre")
    assert nocase_repository.exists_any(entity_ids=("other", "GROUP/PLATFORM"), subject_type="department", user_list_type="feature", env="pre", bot_types=("personal", "all"))
    assert not nocase_repository.exists(entity_id="group/platform", subject_type="department", user_list_type="feature", env="prod")
    assert not nocase_repository.exists_any(entity_ids=("group/platform",), subject_type="department", user_list_type="feature", env="pre", bot_types=("personal",))


def test_case_variant_upsert_is_idempotent(nocase_repository):
    nocase_repository.set_membership(entity_id="group/platform", subject_type="department", user_list_type="feature", env="pre", in_whitelist=True)
    with nocase_repository._db.orm_session() as session:
        row = session.query(EntityUserListModel).one()
        assert row.entity_id == "Group/Platform"
        assert row.gmt_modified > datetime(2000, 1, 1)


def test_case_variant_delete_preserves_other_scopes(nocase_repository):
    for env, bot_type in (("prod", "all"), ("pre", "personal")):
        nocase_repository.set_membership(entity_id="Group/Platform", subject_type="department", user_list_type="feature", env=env, bot_type=bot_type, in_whitelist=True)
    nocase_repository.set_membership(entity_id="group/platform", subject_type="department", user_list_type="feature", env="pre", in_whitelist=False)
    assert not nocase_repository.exists(entity_id="Group/Platform", subject_type="department", user_list_type="feature", env="pre")
    assert nocase_repository.exists(entity_id="group/platform", subject_type="department", user_list_type="feature", env="prod")
    assert nocase_repository.exists(entity_id="group/platform", subject_type="department", user_list_type="feature", env="pre", bot_type="personal")


def test_old_user_comparison_preserves_database_collation(nocase_repository):
    nocase_repository.set_membership(entity_id="Staff", user_list_type="feature", env="pre", in_whitelist=True)
    assert nocase_repository.exists(entity_id="staff", user_list_type="feature", env="pre")


@pytest.mark.parametrize("candidate_query", [False, True])
def test_mysql_department_parameters_remain_strings(nocase_repository, monkeypatch, candidate_query):
    from sqlalchemy.dialects.mysql import pymysql
    from sqlalchemy.sql.sqltypes import String
    from types import SimpleNamespace

    dialect = pymysql.dialect(dbapi=SimpleNamespace(Binary=bytes, paramstyle="pyformat"))
    statements = []

    def capture_query(query):
        statements.append(query.statement)
        return None

    # Select the MySQL-specific repository path without opening a remote DB.
    with nocase_repository._db.orm_session() as session:
        monkeypatch.setattr(session.get_bind().dialect, "name", "mysql")
    monkeypatch.setattr(Query, "first", capture_query)
    path = "集团/AI平台"
    if candidate_query:
        nocase_repository.exists_any(entity_ids=(path,), subject_type="department", user_list_type="feature", env="pre", bot_types=("personal", "all"))
    else:
        nocase_repository.exists(entity_id=path, subject_type="department", user_list_type="feature", env="pre")
    compiled = statements[0].compile(dialect=dialect)
    assert "CAST(" not in str(compiled)
    assert path not in str(compiled)
    found_path = False
    for name, value in compiled.params.items():
        bind = compiled.binds[name]
        processor = bind.type.dialect_impl(dialect).bind_processor(dialect)
        for item in value if isinstance(value, (tuple, list)) else (value,):
            processed = processor(item) if processor else item
            if item == path:
                found_path = True
                assert isinstance(bind.type, String)
                assert processed == path
    assert found_path

"""Unified SkillMember repository — behavior + cross-backend contract.

Round-3/session-2 criteria: single body, 10-method Protocol parity,
auto-save via real ``SqliteDB.orm_session``. No ZDAS-skipped test; the
prod round-trip is the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.skill_member_repository import (
    SkillMemberRepository,
)

pytestmark = pytest.mark.integration


def _create_schema(engine):
    from agentclaw.community.core.models.skill import AcSkillMember

    src = AcSkillMember.__table__
    md = MetaData()
    Table(
        src.name,
        md,
        *[
            Column(
                c.name,
                c.type,
                primary_key=c.primary_key,
                nullable=c.nullable,
                autoincrement=c.autoincrement,
                server_default=c.server_default.arg
                if c.server_default is not None
                else None,
            )
            for c in src.columns
        ],
    )
    md.create_all(engine)


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sm.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return SkillMemberRepository(_make_db(tmp_path))


def test_add_member_and_get(repo):
    r = repo.add_member("sk-1", "u1", role="admin")
    assert r["skill_uuid"] == "sk-1"
    assert r["user_id"] == "u1"
    assert r["role"] == "admin"
    assert r["id"] is not None

    got = repo.get_member("sk-1", "u1")
    assert got is not None
    assert got["role"] == "admin"


def test_add_member_default_role_is_member(repo):
    r = repo.add_member("sk-1", "u1")
    assert r["role"] == "member"


def test_add_member_rejects_duplicate(repo):
    repo.add_member("sk-1", "u1")
    with pytest.raises(ValueError, match="already a member"):
        repo.add_member("sk-1", "u1")


def test_add_member_rejects_bad_role(repo):
    with pytest.raises(ValueError, match="Invalid role"):
        repo.add_member("sk-1", "u1", role="owner")


def test_get_members_by_skill_uuid_ordered(repo):
    repo.add_member("sk-1", "u1")
    repo.add_member("sk-1", "u2")
    repo.add_member("sk-1", "u3")
    rows = repo.get_members_by_skill_uuid("sk-1")
    assert [r["user_id"] for r in rows] == ["u1", "u2", "u3"]


def test_remove_member(repo):
    repo.add_member("sk-1", "u1")
    assert repo.remove_member("sk-1", "u1") is True
    assert repo.get_member("sk-1", "u1") is None


def test_remove_member_missing_raises(repo):
    with pytest.raises(ValueError, match="Member not found"):
        repo.remove_member("sk-1", "u1")


def test_update_member_role(repo):
    repo.add_member("sk-1", "u1", role="member")
    r = repo.update_member_role("sk-1", "u1", "admin")
    assert r["role"] == "admin"
    assert repo.get_member_role("sk-1", "u1") == "admin"


def test_update_member_role_missing_raises(repo):
    with pytest.raises(ValueError, match="Member not found"):
        repo.update_member_role("sk-1", "u1", "admin")


def test_update_member_role_bad_role(repo):
    repo.add_member("sk-1", "u1")
    with pytest.raises(ValueError, match="Invalid role"):
        repo.update_member_role("sk-1", "u1", "owner")


def test_is_member(repo):
    repo.add_member("sk-1", "u1")
    assert repo.is_member("sk-1", "u1") is True
    assert repo.is_member("sk-1", "u2") is False


def test_get_member_role_missing_returns_none(repo):
    assert repo.get_member_role("sk-1", "u1") is None


def test_get_skill_uuids_by_user_id(repo):
    repo.add_member("sk-1", "u1")
    repo.add_member("sk-2", "u1")
    repo.add_member("sk-3", "u2")
    uuids = repo.get_skill_uuids_by_user_id("u1")
    assert set(uuids) == {"sk-1", "sk-2"}


def test_has_admin_role(repo):
    repo.add_member("sk-1", "u1", role="admin")
    repo.add_member("sk-1", "u2", role="member")
    assert repo.has_admin_role("sk-1", "u1") is True
    assert repo.has_admin_role("sk-1", "u2") is False
    assert repo.has_admin_role("sk-1", "u3") is False


def test_get_members_by_skill_uuids_batch(repo):
    repo.add_member("sk-1", "u1", role="admin")
    repo.add_member("sk-1", "u2", role="member")
    repo.add_member("sk-2", "u3", role="admin")
    result = repo.get_members_by_skill_uuids(["sk-1", "sk-2", "sk-3"])
    assert set(result.keys()) == {"sk-1", "sk-2"}
    assert len(result["sk-1"]) == 2
    assert len(result["sk-2"]) == 1
    assert result["sk-2"][0] == {"user_id": "u3", "role": "admin"}


def test_get_members_by_skill_uuids_empty(repo):
    assert repo.get_members_by_skill_uuids([]) == {}

"""Unified Resource repository — behavior + cross-backend contract.

Round-3/session-2 criteria for this entity: single body, full
Protocol coverage, string user_id/created_by semantics, ``gmt_created``
typo preserved on the model. No ZDAS-skipped test (zero skips policy);
prod round-trip is the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.resource_repository import ResourceRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    from agentclaw.community.plugin_api.models import ResourceModel

    src = ResourceModel.__table__
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
        f"sqlite:///{tmp_path / 'res.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return ResourceRepository(_make_db(tmp_path))


def _make_resource(**overrides):
    data = {
        "name": "file.txt",
        "resource_type": "file",
        "attributes": {"path": "/a/b/file.txt", "parent_path": "/a/b"},
        "user_id": "123",
        "created_by": "123",
        "bolt_id": "bolt-1",
    }
    data.update(overrides)
    return data


def test_create_returns_string_ids(repo):
    r = repo.create(_make_resource())
    # Both twins return id/user_id/created_by as strings.
    assert isinstance(r["id"], str)
    assert r["user_id"] == "123"
    assert r["created_by"] == "123"
    assert r["status"] == "active"  # default
    assert r["attributes"] == {"path": "/a/b/file.txt", "parent_path": "/a/b"}


def test_get_by_id_round_trip(repo):
    rid = repo.create(_make_resource())["id"]
    got = repo.get_by_id(rid)
    assert got is not None
    assert got["id"] == rid
    assert got["user_id"] == "123"


def test_get_by_id_missing(repo):
    assert repo.get_by_id("99999") is None


def test_create_accepts_non_numeric_owner_fields(repo):
    r = repo.create(
        _make_resource(user_id="user_abc", created_by="creator_abc")
    )
    assert r["user_id"] == "user_abc"
    assert r["created_by"] == "creator_abc"


def test_list_resources_filters_deleted_by_default(repo):
    r1 = repo.create(_make_resource(name="a"))
    r2 = repo.create(_make_resource(name="b"))
    repo.delete(r2["id"])
    rows = repo.list_resources(bolt_id="bolt-1")
    assert [r["id"] for r in rows] == [r1["id"]]


def test_list_resources_filter_user_id_string(repo):
    repo.create(_make_resource(user_id="123"))
    repo.create(_make_resource(user_id="456"))
    rows = repo.list_resources(user_id="123", bolt_id="bolt-1")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "123"


def test_list_resources_filter_non_numeric_user_id(repo):
    repo.create(_make_resource(user_id="user_abc"))
    repo.create(_make_resource(user_id="user_xyz"))
    rows = repo.list_resources(user_id="user_abc", bolt_id="bolt-1")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user_abc"


def test_list_resources_unknown_user_id_returns_empty(repo):
    repo.create(_make_resource(user_id="123"))
    repo.create(_make_resource(user_id="456"))
    rows = repo.list_resources(user_id="abc", bolt_id="bolt-1")
    assert rows == []


def test_list_resources_parent_path_filter(repo):
    a = repo.create(
        _make_resource(
            name="a",
            attributes={"path": "/x/y/a", "parent_path": "/x/y"},
        )
    )
    repo.create(
        _make_resource(
            name="b",
            attributes={"path": "/x/z/b", "parent_path": "/x/z"},
        )
    )
    rows = repo.list_resources(parent_path="/x/y", bolt_id="bolt-1")
    assert [r["id"] for r in rows] == [a["id"]]


def test_get_by_path(repo):
    r = repo.create(
        _make_resource(attributes={"path": "/p/q.txt"})
    )
    got = repo.get_by_path("/p/q.txt", bolt_id="bolt-1")
    assert got is not None
    assert got["id"] == r["id"]
    assert repo.get_by_path("/no/such", bolt_id="bolt-1") is None


def test_update_metadata_round_trip(repo):
    rid = repo.create(_make_resource())["id"]
    updated = repo.update(rid, {"metadata": {"key": "value"}})
    assert updated["metadata"] == {"key": "value"}


def test_update_accepts_non_numeric_owner_fields(repo):
    rid = repo.create(_make_resource())["id"]
    updated = repo.update(
        rid,
        {"user_id": "user_abc", "created_by": "creator_abc"},
    )
    assert updated["user_id"] == "user_abc"
    assert updated["created_by"] == "creator_abc"


def test_update_missing_returns_none(repo):
    assert repo.update("99999", {"status": "deleted"}) is None


def test_delete_sets_status_deleted(repo):
    rid = repo.create(_make_resource())["id"]
    assert repo.delete(rid) is True
    got = repo.get_by_id(rid)
    assert got["status"] == "deleted"


def test_delete_missing_returns_false(repo):
    assert repo.delete("99999") is False


def test_hard_delete_removes_row(repo):
    rid = repo.create(_make_resource())["id"]
    assert repo.hard_delete(rid) is True
    assert repo.get_by_id(rid) is None


def test_hard_delete_missing_returns_false(repo):
    assert repo.hard_delete("99999") is False


def test_count_resources(repo):
    repo.create(_make_resource(name="a"))
    repo.create(_make_resource(name="b"))
    assert repo.count_resources(bolt_id="bolt-1") == 2

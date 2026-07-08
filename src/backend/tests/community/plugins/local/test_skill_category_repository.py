"""SQLite repository tests for SkillCategory CRUD + list_descendant_codes."""
import pytest

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import SkillCategory  # noqa: F401 — register table
from agentclaw.community.plugins.skill_category_repository import (
    SkillCategoryRepository,
)


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite db with the ac_skill_category table."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    class _DB:
        # Unified repo uses orm_session(): commit on clean exit,
        # rollback on exception (the SqliteDB.orm_session contract).
        def orm_session(self):
            return _Ctx(Session)

        session = orm_session

    class _Ctx:
        def __init__(self, factory):
            self._factory = factory

        def __enter__(self):
            self._s = self._factory()
            return self._s

        def __exit__(self, exc_type, *a):
            try:
                if exc_type is None:
                    self._s.commit()
                else:
                    self._s.rollback()
            finally:
                self._s.close()

    return _DB()


@pytest.fixture
def repo(db):
    return SkillCategoryRepository(db)


# ── list_active ─────────────────────────────────────────────────────────────

class TestListActive:
    def test_empty(self, repo):
        assert repo.list_active() == []

    def test_returns_only_active(self, repo):
        repo.create(code="on", name="On", parent_code="", path="/on/", level=0, sort_order=0)
        repo.update("on", status=0)
        repo.create(code="active", name="Active", parent_code="", path="/active/", level=0, sort_order=0)
        result = repo.list_active()
        assert len(result) == 1
        assert result[0]["code"] == "active"

    def test_ordered_by_level_and_sort(self, repo):
        repo.create(code="b", name="B", parent_code="", path="/b/", level=0, sort_order=2)
        repo.create(code="a", name="A", parent_code="", path="/a/", level=0, sort_order=1)
        repo.create(code="c", name="C", parent_code="", path="/a/c/", level=1, sort_order=0)
        result = repo.list_active()
        codes = [r["code"] for r in result]
        assert codes == ["a", "b", "c"]


# ── get_by_code ─────────────────────────────────────────────────────────────

class TestGetByCode:
    def test_found(self, repo):
        repo.create(code="cat", name="Cat", parent_code="", path="/cat/", level=0, sort_order=0)
        result = repo.get_by_code("cat")
        assert result is not None
        assert result["code"] == "cat"
        assert result["name"] == "Cat"

    def test_not_found(self, repo):
        assert repo.get_by_code("missing") is None


# ── create ──────────────────────────────────────────────────────────────────

class TestCreate:
    def test_root_category(self, repo):
        result = repo.create(code="root", name="Root", parent_code="", path="/root/", level=0, sort_order=0)
        assert result["code"] == "root"
        assert result["level"] == 0
        assert result["status"] == 1
        assert result["parent_code"] == ""

    def test_child_category(self, repo):
        repo.create(code="root", name="Root", parent_code="", path="/root/", level=0, sort_order=0)
        result = repo.create(code="child", name="Child", parent_code="root", path="/root/child/", level=1, sort_order=0)
        assert result["code"] == "child"
        assert result["level"] == 1
        assert result["parent_code"] == "root"

    def test_persists_to_db(self, repo):
        repo.create(code="persist", name="Persist", parent_code="", path="/persist/", level=0, sort_order=0)
        fetched = repo.get_by_code("persist")
        assert fetched is not None
        assert fetched["name"] == "Persist"


# ── update ──────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_update_name(self, repo):
        repo.create(code="cat", name="Old", parent_code="", path="/cat/", level=0, sort_order=0)
        result = repo.update("cat", name="New")
        assert result["name"] == "New"

    def test_update_sort_order(self, repo):
        repo.create(code="cat", name="Cat", parent_code="", path="/cat/", level=0, sort_order=0)
        result = repo.update("cat", sort_order=10)
        assert result["sort_order"] == 10

    def test_update_status(self, repo):
        repo.create(code="cat", name="Cat", parent_code="", path="/cat/", level=0, sort_order=0)
        result = repo.update("cat", status=0)
        assert result["status"] == 0

    def test_not_found_returns_none(self, repo):
        assert repo.update("missing", name="X") is None


# ── list_descendant_codes ───────────────────────────────────────────────────

class TestListDescendantCodes:
    def test_single_category(self, repo):
        repo.create(code="root", name="Root", parent_code="", path="/root/", level=0, sort_order=0)
        result = repo.list_descendant_codes("/root/")
        assert "root" in result

    def test_includes_children(self, repo):
        repo.create(code="root", name="Root", parent_code="", path="/root/", level=0, sort_order=0)
        repo.create(code="child", name="Child", parent_code="root", path="/root/child/", level=1, sort_order=0)
        result = repo.list_descendant_codes("/root/")
        assert "root" in result
        assert "child" in result

    def test_excludes_other_branches(self, repo):
        repo.create(code="a", name="A", parent_code="", path="/a/", level=0, sort_order=0)
        repo.create(code="a1", name="A1", parent_code="a", path="/a/a1/", level=1, sort_order=0)
        repo.create(code="b", name="B", parent_code="", path="/b/", level=0, sort_order=0)
        result = repo.list_descendant_codes("/a/")
        assert "a" in result
        assert "a1" in result
        assert "b" not in result

    def test_excludes_disabled(self, repo):
        repo.create(code="root", name="Root", parent_code="", path="/root/", level=0, sort_order=0)
        repo.create(code="disabled", name="Disabled", parent_code="root", path="/root/disabled/", level=1, sort_order=0)
        repo.update("disabled", status=0)
        result = repo.list_descendant_codes("/root/")
        assert "disabled" not in result

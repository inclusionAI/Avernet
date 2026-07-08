"""FileRepository — ac_file CRUD + path queries (sqlite-backed).

Pins the teclaw workspace-file metadata store: insert, exact-path lookup,
directory-prefix listing, per-bot listing (compose's read), env isolation, and
hard delete.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import FileModel
from agentclaw.community.plugins.file_repository import FileRepository

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ac_file.db'}",
        connect_args={"check_same_thread": False},
    )
    FileModel.__table__.create(engine)
    return FileRepository(_FileSqliteDB(engine))


def _data(**overrides):
    base = dict(
        bot_id="bot7",
        entity_id="u1",
        entity_type="staff",
        engine_type="moltis",
        env="dev",
        path="docs/a.md",
        name="a.md",
        parent_path="docs",
        size=12,
        mime_type="text/markdown",
        source="upload",
        created_by="u1",
        user_id="u1",
    )
    base.update(overrides)
    return base


def test_create_and_get_by_path(repo):
    rec = repo.create(_data())
    assert rec.id is not None
    assert rec.path == "docs/a.md" and rec.name == "a.md" and rec.size == 12
    got = repo.get_by_path(bot_id="bot7", env="dev", path="docs/a.md")
    assert got is not None and got.id == rec.id


def test_get_by_path_missing_returns_none(repo):
    repo.create(_data())
    assert repo.get_by_path(bot_id="bot7", env="dev", path="nope.md") is None
    # wrong bot / wrong env are isolated
    assert repo.get_by_path(bot_id="other", env="dev", path="docs/a.md") is None
    assert repo.get_by_path(bot_id="bot7", env="pre", path="docs/a.md") is None


def test_list_by_bot_and_env_isolation(repo):
    repo.create(_data(path="a.md", name="a.md"))
    repo.create(_data(path="b.md", name="b.md"))
    repo.create(_data(env="pre", path="c.md", name="c.md"))
    repo.create(_data(bot_id="other", path="d.md", name="d.md"))
    dev = repo.list_by_bot(bot_id="bot7", env="dev")
    assert {r.path for r in dev} == {"a.md", "b.md"}


def test_list_by_bot_filters_by_engine_type(repo):
    # After an engine switch, compose must not surface the old engine's rows.
    repo.create(_data(path="a.md", name="a.md", engine_type="moltis"))
    repo.create(_data(path="b.md", name="b.md", engine_type="openclaw"))
    moltis = repo.list_by_bot(bot_id="bot7", env="dev", engine_type="moltis")
    assert {r.path for r in moltis} == {"a.md"}
    # No engine_type filter → all rows (back-compat).
    assert {r.path for r in repo.list_by_bot(bot_id="bot7", env="dev")} == {"a.md", "b.md"}


def test_list_by_path_prefix_is_subtree_only(repo):
    repo.create(_data(path="skills/my-skill/SKILL.md", name="SKILL.md"))
    repo.create(_data(path="skills/my-skill/run.py", name="run.py"))
    repo.create(_data(path="skills/my-skill-2/X.md", name="X.md"))  # sibling
    repo.create(_data(path="docs/a.md", name="a.md"))
    sub = repo.list_by_path_prefix(
        bot_id="bot7", env="dev", prefix="skills/my-skill/"
    )
    assert {r.path for r in sub} == {
        "skills/my-skill/SKILL.md", "skills/my-skill/run.py",
    }


def test_list_by_path_prefix_escapes_like_wildcards(repo):
    # A dir name containing '_' (LIKE single-char wildcard) must not over-match a
    # sibling like "docsXv2/". Escaping keeps the prefix subtree-only.
    repo.create(_data(path="docs_v2/a.md", name="a.md"))
    repo.create(_data(path="docsXv2/b.md", name="b.md"))
    sub = repo.list_by_path_prefix(bot_id="bot7", env="dev", prefix="docs_v2/")
    assert {r.path for r in sub} == {"docs_v2/a.md"}


def test_delete_is_hard(repo):
    rec = repo.create(_data())
    assert repo.delete(rec.id) is True
    assert repo.get_by_path(bot_id="bot7", env="dev", path="docs/a.md") is None
    assert repo.delete(rec.id) is False

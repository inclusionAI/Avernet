"""``ac_bot_config_managed_files`` on SQLite: the index behind the teclaw store (W8)."""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_config_manifest.repository.managed_files_models import (  # noqa: F401
    BotConfigManagedFileModel,
    managed_path_hash,
)
from agentclaw.community.core.repository.implementations.bot.managed_files import (
    BotConfigManagedFilesRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Db:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

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


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return BotConfigManagedFilesRepository(_Db(engine))


_KEY = dict(env="dev", entity_id="u_1", bot_id="b_1")


def _put(repo, category="identity", rel_path="identity/RULES.md", digest="sha256:aa", **over):
    return repo.upsert(
        **_KEY,
        category=category,
        name=over.pop("name", rel_path.rsplit("/", 1)[-1]),
        rel_path=rel_path,
        store_key=over.pop("store_key", f"teclaw/dev/bolt_data/staff_u_1/b_1_manifest/teclaw/{rel_path}"),
        digest=digest,
        size_bytes=over.pop("size_bytes", 3),
        apply_id=over.pop("apply_id", "ap_1"),
    )


def test_upsert_replaces_rather_than_duplicates(repo) -> None:
    first = _put(repo, digest="sha256:aa")
    second = _put(repo, digest="sha256:bb", apply_id="ap_2")
    assert first.id == second.id
    rows = repo.list_by_category(**_KEY, category="identity")
    assert [r.digest for r in rows] == ["sha256:bb"]
    assert rows[0].apply_id == "ap_2"
    assert rows[0].rel_path == "identity/RULES.md"


def test_the_path_is_hashed_into_the_key_and_kept_readable(repo) -> None:
    long_path = "workspace/" + "d/" * 300 + "x.csv"
    row = _put(repo, category="resources", rel_path=long_path)
    assert row.rel_path == long_path
    assert repo.get(**_KEY, category="resources", rel_path=long_path) is not None
    # Same path, different category: a different row.
    _put(repo, category="skills", rel_path=long_path)
    assert len(repo.list_all(**_KEY)) == 2
    assert managed_path_hash(long_path) == managed_path_hash(long_path)


def test_listing_is_scoped_and_ordered(repo) -> None:
    _put(repo, category="resources", rel_path="workspace/b.md")
    _put(repo, category="resources", rel_path="workspace/a.md")
    _put(repo, category="identity", rel_path="identity/SOUL.md")
    repo.upsert(**{**_KEY, "bot_id": "b_other"}, category="resources", name="z", rel_path="workspace/z.md", store_key="k", digest="sha256:zz", size_bytes=1, apply_id=None)
    assert [r.rel_path for r in repo.list_by_category(**_KEY, category="resources")] == [
        "workspace/a.md",
        "workspace/b.md",
    ]
    assert [(r.category, r.rel_path) for r in repo.list_all(**_KEY)] == [
        ("identity", "identity/SOUL.md"),
        ("resources", "workspace/a.md"),
        ("resources", "workspace/b.md"),
    ]


def test_delete_removes_one_row(repo) -> None:
    _put(repo, category="identity", rel_path="identity/SOUL.md")
    _put(repo, category="resources", rel_path="workspace/a.md")
    assert repo.delete(**_KEY, category="identity", rel_path="identity/SOUL.md")
    assert not repo.delete(**_KEY, category="identity", rel_path="identity/SOUL.md")
    assert [r.rel_path for r in repo.list_all(**_KEY)] == ["workspace/a.md"]
    assert repo.delete(**_KEY, category="resources", rel_path="workspace/a.md")
    assert repo.list_all(**_KEY) == []


def test_rows_are_confined_to_the_tenant(repo) -> None:
    with avernet_tenant_scope("tenant-a"):
        _put(repo)
    with avernet_tenant_scope("tenant-b"):
        assert repo.list_all(**_KEY) == []
        _put(repo, digest="sha256:bb")
    with avernet_tenant_scope("tenant-a"):
        assert [r.digest for r in repo.list_all(**_KEY)] == ["sha256:aa"]

"""BotFileService — record-before-deliver upload, delete-by-path, ordering.

Pins the teclaw mutation invariants: the ``ac_file`` row exists before the byte
write's redeliver would fire (no stale-by-one-edit), a write failure rolls the
row back, delete removes the row before the device delete, prefix delete is
subtree-only, and a legacy (no-row) delete still deletes the bytes.
"""
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.files.service import BotFileService
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


def _repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ac_file.db'}",
        connect_args={"check_same_thread": False},
    )
    FileModel.__table__.create(engine)
    return FileRepository(_FileSqliteDB(engine))


class _SpyFS:
    """Stand-in DeviceFileSystemPlugin; records calls + fires a hook so a test
    can observe DB state at write/delete time (the redeliver ordering point)."""

    def __init__(self, *, on_write=None, on_delete=None, write_raises=False):
        self.writes = []
        self.deletes = []
        self._on_write = on_write
        self._on_delete = on_delete
        self._write_raises = write_raises

    async def write_file(self, path, content):
        if self._on_write:
            self._on_write()
        if self._write_raises:
            raise OSError("write boom")
        self.writes.append((path, content))

    async def delete_file(self, path):
        if self._on_delete:
            self._on_delete()
        self.deletes.append(path)
        return True


def _svc(repo):
    return BotFileService(
        repository=repo,
        workspace_dir=Path("/ws"),
        bot_id="bot7",
        entity_id="u1",
        entity_type="staff",
        engine_type="moltis",
        env="dev",
    )


@pytest.mark.asyncio
async def test_upload_records_row_before_write(tmp_path):
    repo = _repo(tmp_path)
    seen = {}
    fs = _SpyFS(on_write=lambda: seen.update(
        row=repo.get_by_path(bot_id="bot7", env="dev", path="docs/a.md")
    ))
    rec = await _svc(repo).upload_file(
        data=b"hello", filename="a.md", target_dir="docs", device_fs=fs
    )
    assert rec.size == 5 and rec.path == "docs/a.md" and rec.parent_path == "docs"
    # the row existed at write time → the redeliver would already see it
    assert seen["row"] is not None and seen["row"].id == rec.id
    assert fs.writes == [("/ws/docs/a.md", b"hello")]


@pytest.mark.asyncio
async def test_upload_same_path_upserts_single_row(tmp_path):
    # Re-uploading the same path overwrites; exactly one row survives (no dup
    # ref in compose, no dangling ref after a later delete).
    repo = _repo(tmp_path)
    svc = _svc(repo)
    await svc.upload_file(data=b"v1", filename="a.md", target_dir="docs", device_fs=_SpyFS())
    await svc.upload_file(data=b"v22", filename="a.md", target_dir="docs", device_fs=_SpyFS())
    rows = [r for r in repo.list_by_bot(bot_id="bot7", env="dev") if r.path == "docs/a.md"]
    assert len(rows) == 1
    assert rows[0].size == 3  # latest upload (len "v22")


@pytest.mark.asyncio
async def test_upload_write_failure_rolls_back_row(tmp_path):
    repo = _repo(tmp_path)
    fs = _SpyFS(write_raises=True)
    with pytest.raises(OSError):
        await _svc(repo).upload_file(
            data=b"x", filename="a.md", target_dir="docs", device_fs=fs
        )
    assert repo.get_by_path(bot_id="bot7", env="dev", path="docs/a.md") is None


@pytest.mark.asyncio
async def test_delete_by_path_exact_removes_row_before_device_delete(tmp_path):
    repo = _repo(tmp_path)
    svc = _svc(repo)
    await svc.upload_file(data=b"x", filename="a.md", target_dir="docs", device_fs=_SpyFS())
    seen = {}
    fs = _SpyFS(on_delete=lambda: seen.update(
        row=repo.get_by_path(bot_id="bot7", env="dev", path="docs/a.md")
    ))
    n = await svc.delete_by_path(path="docs/a.md", device_fs=fs)
    assert n == 1
    assert seen["row"] is None  # row gone before the device delete → redeliver drops it
    assert fs.deletes == ["/ws/docs/a.md"]


@pytest.mark.asyncio
async def test_delete_by_path_prefix_is_subtree_only(tmp_path):
    repo = _repo(tmp_path)
    svc = _svc(repo)
    for rel in ("skills/my-skill/SKILL.md", "skills/my-skill/run.py", "skills/my-skill-2/X.md"):
        await svc.upload_file(
            data=b"x", filename=rel, target_dir="", device_fs=_SpyFS(),
            preserve_structure=True,
        )
    n = await svc.delete_by_path(path="skills/my-skill", device_fs=_SpyFS())
    assert n == 2
    assert repo.get_by_path(bot_id="bot7", env="dev", path="skills/my-skill-2/X.md") is not None


@pytest.mark.asyncio
async def test_delete_by_path_legacy_no_row_still_deletes_bytes(tmp_path):
    repo = _repo(tmp_path)
    fs = _SpyFS()
    n = await _svc(repo).delete_by_path(path="orphan.md", device_fs=fs)
    assert n == 0
    assert fs.deletes == ["/ws/orphan.md"]


@pytest.mark.asyncio
async def test_create_directory_records_keep(tmp_path):
    repo = _repo(tmp_path)
    fs = _SpyFS()
    rec = await _svc(repo).create_directory(path="newdir", device_fs=fs)
    assert rec.path == "newdir/.keep" and rec.size == 0
    assert fs.writes == [("/ws/newdir/.keep", b"")]

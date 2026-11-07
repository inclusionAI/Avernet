"""Profile descriptions survive on the existing SQLite table layout."""

from app.domain.models.worker_profile_content import WorkerProfileContent
from app.infra.public.stores.sqlite_worker_profile_content_store import (
    SQLiteWorkerProfileContentStore,
)


def test_sqlite_profile_description_persists_without_new_column(tmp_path):
    path = str(tmp_path / "legacy-profiles.sqlite")
    store = SQLiteWorkerProfileContentStore(db_path=path)
    store.save(WorkerProfileContent(worker_id="bot:owner", soul_md="Old soul"))
    store.close()

    reopened = SQLiteWorkerProfileContentStore(db_path=path)
    previous = reopened.get("bot:owner", "default")
    assert previous.soul_md == "Old soul"
    reopened.save(WorkerProfileContent(
        worker_id="bot:owner", soul_md="Old soul", description="New description",
        metadata={"source": "api"},
    ))
    persisted = reopened.get("bot:owner", "default")
    assert persisted.description == "New description"
    assert persisted.metadata == {"source": "api"}
    row = reopened._conn.execute(
        "SELECT metadata FROM bcsfuse_worker_profile_contents WHERE worker_id = ?",
        ("bot:owner",),
    ).fetchone()
    assert "New description" in row["metadata"]
    columns = {row[1] for row in reopened._conn.execute(
        "PRAGMA table_info(bcsfuse_worker_profile_contents)"
    )}
    assert "description" not in columns
    reopened.close()


def test_versioned_save_can_clear_description_without_a_new_column(tmp_path):
    path = str(tmp_path / "legacy-profiles.sqlite")
    store = SQLiteWorkerProfileContentStore(db_path=path)
    store.close()

    reopened = SQLiteWorkerProfileContentStore(db_path=path)
    original = reopened.save_if_version(WorkerProfileContent(
        worker_id="bot:owner", description="Initial", metadata={"revision": 1},
    ), None)
    assert original is not None
    cleared = reopened.save_if_version(original.model_copy(update={
        "description": "", "metadata": {},
    }), original.version)
    assert cleared is not None
    persisted = reopened.get("bot:owner", "default")
    assert persisted.description == ""
    assert persisted.metadata == {}
    columns = {row[1] for row in reopened._conn.execute(
        "PRAGMA table_info(bcsfuse_worker_profile_contents)"
    )}
    assert "description" not in columns
    reopened.close()

"""Existing SQLite Profile databases gain the description column safely."""

from src.domain.models.worker_profile_content import WorkerProfileContent
from src.infra.public.stores.sqlite_worker_profile_content_store import (
    SQLiteWorkerProfileContentStore,
)


def test_legacy_sqlite_profile_table_is_upgraded_without_losing_rows(tmp_path):
    path = str(tmp_path / "legacy-profiles.sqlite")
    store = SQLiteWorkerProfileContentStore(db_path=path)
    store.save(WorkerProfileContent(worker_id="bot:owner", soul_md="Old soul"))
    store._conn.execute(
        "ALTER TABLE bcsfuse_worker_profile_contents DROP COLUMN description"
    )
    store._conn.commit()
    store.close()

    upgraded = SQLiteWorkerProfileContentStore(db_path=path)
    previous = upgraded.get("bot:owner", "default")
    assert previous.soul_md == "Old soul"
    upgraded.save(WorkerProfileContent(
        worker_id="bot:owner", soul_md="Old soul", description="New description",
    ))
    assert upgraded.get("bot:owner", "default").description == "New description"
    upgraded.close()

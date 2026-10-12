"""Concurrent sparse Worker syncs must preserve independently supplied fields."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from src.domain.models.worker import Capability, CapabilityLevel, SkillRef, SkillSource, TrustLevel
from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore
from tests.contract.adapters.helpers import make_sample_worker


def test_sqlite_worker_update_rejects_stale_write_after_another_process_commits(tmp_path, monkeypatch):
    path = str(tmp_path / "workers.sqlite")
    first = SQLiteWorkerRegistryStore(db_path=path)
    second = SQLiteWorkerRegistryStore(db_path=path)
    initial = first.create(make_sample_worker(worker_id="bot:owner"))
    changed_capabilities = initial.model_copy(deep=True)
    changed_capabilities.capabilities = [Capability(name="new", level=CapabilityLevel.EXPERT)]
    changed_skills = initial.model_copy(deep=True)
    changed_skills.skills = [SkillRef(name="new-skill", source=SkillSource.MANAGED, trust_level=TrustLevel.TRUSTED)]
    second_is_ready = Event()
    first_committed = Event()
    original_get = second.get_by_id

    def get_then_wait(worker_id):
        snapshot = original_get(worker_id)
        second_is_ready.set()
        assert first_committed.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(second, "get_by_id", get_then_wait)
    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_write = executor.submit(second.update, changed_skills)
        assert second_is_ready.wait(timeout=5)
        first.update(changed_capabilities)
        first_committed.set()
        with pytest.raises(ValueError, match="Version conflict"):
            stale_write.result()

    persisted = first.get_by_id("bot:owner")
    assert [item.name for item in persisted.capabilities] == ["new"]
    assert persisted.skills == []


def test_mysql_worker_update_uses_version_predicate_and_rejects_conflict():
    pool = MagicMock()
    connection = pool.get_connection.return_value
    cursor = connection.cursor.return_value
    cursor.rowcount = 0
    with patch.object(MySQLWorkerRegistryStore, "_ensure_schema"):
        store = MySQLWorkerRegistryStore(pool)
    worker = make_sample_worker(worker_id="bot:owner")
    with patch.object(store, "get_by_id", return_value=worker):
        with pytest.raises(ValueError, match="Version conflict"):
            store.update(worker)

    update_sql, params = cursor.execute.call_args_list[0].args
    assert "WHERE id = %s AND version = %s" in update_sql
    assert params[-1] == worker.version
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_sparse_worker_merge_retries_after_competing_update(tmp_path, monkeypatch):
    from src.application.services.worker_sync_merge import save_merged_worker

    path = str(tmp_path / "workers.sqlite")
    first = SQLiteWorkerRegistryStore(db_path=path)
    second = SQLiteWorkerRegistryStore(db_path=path)
    first.create(make_sample_worker(worker_id="bot:owner"))
    competing = first.get_by_id("bot:owner")
    competing.capabilities = [Capability(name="new", level=CapabilityLevel.EXPERT)]
    original_update = second.update
    calls = 0

    def race_once(worker):
        nonlocal calls
        calls += 1
        if calls == 1:
            first.update(competing)
        return original_update(worker)

    monkeypatch.setattr(second, "update", race_once)
    saved = save_merged_worker(
        second,
        {"name": "Bot", "skills": [{
            "name": "new-skill", "source": "managed", "trust_level": "trusted",
        }]},
        {"skills"},
        worker_id="bot:owner",
        active_profile_key=None,
    )

    assert calls == 2
    assert [item.name for item in saved.capabilities] == ["new"]
    assert [item.name for item in saved.skills] == ["new-skill"]

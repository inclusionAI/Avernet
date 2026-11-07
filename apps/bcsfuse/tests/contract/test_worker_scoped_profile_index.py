"""Scoped indexing must preserve the full scan's selection and merge rules."""

from unittest.mock import MagicMock

import pytest

from app.domain.models.worker import Worker, WorkerIdentity, WorkerState, WorkerType
from app.domain.models.worker_lifecycle_state import WorkerLifecycleState
from app.domain.models.worker_profile_content import WorkerProfileContent
from app.domain.models.worker_runtime_state import WorkerRuntimeState
from app.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore,
)
from app.infra.adapters.in_memory_worker_runtime_state_store import (
    InMemoryWorkerRuntimeStateStore,
)
from app.infra.public.stores.in_memory_worker_profile_content_store import (
    InMemoryWorkerProfileContentStore,
)
from app.infra.public.stores.mysql_worker_profile_content_store import (
    MySQLWorkerProfileContentStore,
)
from app.infra.public.stores.sqlite_worker_profile_content_store import (
    SQLiteWorkerProfileContentStore,
)
from app.infra.worker_profiles.sources.api_profile_source import APIProfileSource
from app.infra.worker_profiles.sources.composite_worker_profile_source import (
    CompositeWorkerProfileSource,
)
from app.infra.worker_profiles.sources.registry_worker_profile_source import (
    RegistryWorkerProfileSource,
)


@pytest.mark.parametrize("api_profile_id", ["selected", "fallback"])
@pytest.mark.parametrize(
    ("runtime", "lifecycle"),
    [
        (WorkerRuntimeState.ONLINE, WorkerLifecycleState.ACTIVE),
        (WorkerRuntimeState.OFFLINE, WorkerLifecycleState.ACTIVE),
        (WorkerRuntimeState.ONLINE, WorkerLifecycleState.INACTIVE),
    ],
)
def test_scoped_composite_preserves_merge_aliases_and_filters(
    monkeypatch,
    api_profile_id,
    runtime,
    lifecycle,
):
    workers = InMemoryWorkerRegistryStore()
    states = InMemoryWorkerRuntimeStateStore()
    contents = InMemoryWorkerProfileContentStore()
    worker_id = "bot_42"
    for owner, profile_id in [
        ("42", "default"),
        (worker_id, api_profile_id),
        ("other", "default"),
    ]:
        contents.upsert_profile(
            owner,
            profile_id,
            {
                "worker_id": owner,
                "profile_id": profile_id,
                "soul_md": f"Rich profile content for {owner} " * 10,
            },
        )
        contents.activate_profile(owner, profile_id)
    contents.upsert_profile(
        worker_id,
        "draft",
        {
            "worker_id": worker_id,
            "profile_id": "draft",
            "soul_md": "Never index this draft",
        },
    )
    workers.create(
        Worker(
            id=worker_id,
            type=WorkerType.BOT,
            responsibilities=["general"],
            capabilities=[],
            identity=WorkerIdentity(
                name="Bot", handle="@bot_42", description="Short registry content"
            ),
            state=WorkerState(availability="public", trust_level="guarded"),
            lifecycle_state=lifecycle,
            active_profile_key="bot_42:selected",
        )
    )
    states.set_runtime_state(worker_id, runtime)
    source = CompositeWorkerProfileSource(
        registry_source=RegistryWorkerProfileSource(workers, states),
        api_source=APIProfileSource(content_store=contents),
    )
    expected = [
        p.model_dump() for p in source.scan().profiles if p.staff_id == worker_id
    ]
    calls = []
    scoped_read = getattr(contents, "get_active_by_workers", None)
    if scoped_read is not None:

        def read_owners(owners):
            calls.append(set(owners))
            return scoped_read(owners)

        monkeypatch.setattr(contents, "get_active_by_workers", read_owners)
    monkeypatch.setattr(
        contents, "get_all_active", lambda: pytest.fail("all-Profile read")
    )
    monkeypatch.setattr(
        workers, "list", lambda **kwargs: pytest.fail("all-Worker read")
    )

    actual = source.scan_for_workers([worker_id])

    assert [p.model_dump() for p in actual.profiles] == expected
    assert calls == [{"42", "bot_42"}]
    assert all(p.profile_id != "draft" for p in actual.profiles)


@pytest.mark.parametrize(
    "store_class", [InMemoryWorkerProfileContentStore, SQLiteWorkerProfileContentStore]
)
def test_scoped_active_store_preserves_records_and_excludes_other_workers(
    monkeypatch, store_class
):
    store = store_class()
    for worker_id in ("target", "other"):
        store.upsert_profile(
            worker_id,
            "active",
            {
                "worker_id": worker_id,
                "profile_id": "active",
                "soul_md": f"Profile for {worker_id}",
            },
        )
        store.activate_profile(worker_id, "active")
    store.upsert_profile(
        "target", "draft", {"worker_id": "target", "profile_id": "draft"}
    )
    expected = [
        p.model_dump() for p in store.get_all_active() if p.worker_id == "target"
    ]
    monkeypatch.setattr(
        store, "get_all_active", lambda: pytest.fail("unexpected global read")
    )

    try:
        assert [
            p.model_dump() for p in store.get_active_by_workers(["target"])
        ] == expected
        assert store.get_active_by_workers([]) == []
    finally:
        close = getattr(store, "close", None)
        if close is not None:
            close()


def test_mysql_active_profiles_are_bounded_at_sql_boundary():
    content = WorkerProfileContent(
        worker_id="target", profile_id="active", soul_md="Profile", is_active=True
    )
    row = content.model_dump()
    row.update(gmt_create=content.created_at, gmt_modify=content.updated_at)
    store = MySQLWorkerProfileContentStore.__new__(MySQLWorkerProfileContentStore)
    store._pool = MagicMock()
    connection = store._pool.get_connection.return_value
    cursor = connection.cursor.return_value
    cursor.fetchall.return_value = [row]

    assert store.get_active_by_workers(["target", "bot_target"]) == [content]
    sql, params = cursor.execute.call_args.args
    assert "is_active = 1 AND worker_id IN (%s, %s)" in sql
    assert params == ("target", "bot_target")
    cursor.close.assert_called_once()
    connection.close.assert_called_once()

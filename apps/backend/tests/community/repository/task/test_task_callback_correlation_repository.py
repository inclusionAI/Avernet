"""TDD tests for ``TaskCallbackCorrelationRepository`` (REQ-P1).

Persists the callback↔(task, node, retry) correlation so in-flight callbacks
arriving after an instance restart can be re-attached to the right node by
``event_id`` (idempotent). The repository exposes:

* ``upsert_on_register`` — idempotent on ``event_id``: if a row with ``event_id``
  exists, return it unchanged (DO NOT duplicate; do not mutate); else INSERT.
* ``find_by_event_id`` — return the record (``None`` when unknown).
"""
from agentclaw.community.core.repository.implementations.task.task_callback_correlation_repository import (
    TaskCallbackCorrelationRepository,
)
from agentclaw.community.core.task.repository.types import (
    TaskCallbackCorrelationRecord,
)


def test_upsert_on_register_inserts_row(db):
    repo = TaskCallbackCorrelationRepository(db)
    rec = repo.upsert_on_register(
        event_id="evt-1",
        main_session_id="S-1",
        task_id="T-1",
        node_id="N-1",
        retry=0,
    )
    assert isinstance(rec, TaskCallbackCorrelationRecord)
    assert rec.id > 0
    assert rec.event_id == "evt-1"
    assert rec.main_session_id == "S-1"
    assert rec.task_id == "T-1"
    assert rec.node_id == "N-1"
    assert rec.retry == 0
    assert rec.gmt_create is not None


def test_upsert_on_register_is_idempotent_on_event_id(db):
    """A second call with the SAME event_id returns the existing row unchanged —
    no duplicate row, no mutation of the correlation fields."""
    repo = TaskCallbackCorrelationRepository(db)
    r1 = repo.upsert_on_register(
        event_id="evt-1",
        main_session_id="S-1",
        task_id="T-1",
        node_id="N-1",
        retry=0,
    )
    # second call with the SAME event_id but DIFFERENT correlation fields —
    # must NOT duplicate, must NOT mutate the existing row.
    r2 = repo.upsert_on_register(
        event_id="evt-1",
        main_session_id="S-OTHER",
        task_id="T-OTHER",
        node_id="N-OTHER",
        retry=9,
    )
    assert r2.id == r1.id
    assert r2.event_id == "evt-1"
    # existing correlation fields preserved (NOT mutated to the new args)
    assert r2.main_session_id == "S-1"
    assert r2.task_id == "T-1"
    assert r2.node_id == "N-1"
    assert r2.retry == 0
    # exactly one row for evt-1 (no duplicate)
    found = repo.find_by_event_id("evt-1")
    assert found is not None
    assert found.id == r1.id


def test_upsert_on_register_distinct_event_ids_insert_distinct_rows(db):
    """Different event_ids → different rows."""
    repo = TaskCallbackCorrelationRepository(db)
    a = repo.upsert_on_register("evt-a", "S-1", "T-1", "N-1", 0)
    b = repo.upsert_on_register("evt-b", "S-1", "T-1", "N-2", 1)
    assert a.id != b.id
    assert repo.find_by_event_id("evt-a").id == a.id
    assert repo.find_by_event_id("evt-b").id == b.id


def test_find_by_event_id_returns_none_when_unknown(db):
    repo = TaskCallbackCorrelationRepository(db)
    assert repo.find_by_event_id("missing") is None


def test_find_by_event_id_returns_record(db):
    repo = TaskCallbackCorrelationRepository(db)
    repo.upsert_on_register(
        event_id="evt-2",
        main_session_id="S-1",
        task_id="T-1",
        node_id="N-2",
        retry=1,
    )
    found = repo.find_by_event_id("evt-2")
    assert isinstance(found, TaskCallbackCorrelationRecord)
    assert found.event_id == "evt-2"
    assert found.main_session_id == "S-1"
    assert found.task_id == "T-1"
    assert found.node_id == "N-2"
    assert found.retry == 1

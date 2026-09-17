"""TDD tests for ``TaskTrajectoryRepository`` (REQ-11, repository half).

Exercises the five methods the P1b repository exposes on top of the P1a ORM
models / records / DDL:

* ``insert_event`` — append-only, NO idempotency check (duplicate emissions may
  produce duplicate rows, business-accepted).
* ``upsert_head`` — insert a new head with ``analysis=None`` and fresh
  ``gmt_create``/``gmt_modified``; if a row already exists, do NOT overwrite
  ``analysis``/``gmt_modified`` (preserve the already-backfilled analysis + mtime).
* ``backfill_analysis`` — two-table UPDATE that sets ``analysis`` AND
  ``gmt_modified`` explicitly (NOT relying on the ORM ``onupdate=func.now()``)
  on BOTH ``task_trajectory`` and ``task_trajectory_events``; returns total
  affected rows.
* ``list_events_by_task`` — SELECT all event rows for ``task_id`` ordered by
  ``gmt_create ASC, id ASC`` (the ``id`` tiebreaker is REQUIRED —
  ``gmt_create`` is timestamp-second-precision and same-second ties are
  explicitly accepted by the spec).
* ``list_head`` — read counterpart of ``upsert_head`` (the P4 assembler reads
  the head's persisted ``analysis``).
"""
from datetime import datetime

from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.task.repository.types import (
    TaskTrajectoryRecord,
    TrajectoryEventRecord,
)


def _event(task_id: str = "T-1", node_id: str = "N-1", action_type: str = "dispatch",
           **overrides) -> TrajectoryEventRecord:
    """Build a fully-populated ``TrajectoryEventRecord`` for insert_event.

    ``id=0`` is a placeholder; the repository ignores it (autoincrement assigns
    the real id) and returns a fresh record carrying the stored id.
    """
    defaults = dict(
        id=0,
        task_id=task_id,
        node_id=node_id,
        action_type=action_type,
        attempt=0,
        action_input="candidates=3,dispatch_target=N-1",
        action_result="hit_single",
        status_from="planning",
        status_to="running",
        error_type=None,
        error_msg=None,
        ext_info='{"schema_v":1,"strategy":"direct"}',
        analysis=None,
        gmt_create=datetime(2026, 9, 17, 10, 0, 0),
        gmt_modified=datetime(2026, 9, 17, 10, 0, 0),
    )
    defaults.update(overrides)
    return TrajectoryEventRecord(**defaults)


# ---------------------------------------------------------------------------
# insert_event
# ---------------------------------------------------------------------------


def test_insert_event_inserts_one_row(db):
    repo = TaskTrajectoryRepository(db)
    rec = repo.insert_event(_event())
    assert rec.id > 0
    assert rec.task_id == "T-1"
    assert rec.node_id == "N-1"
    assert rec.action_type == "dispatch"
    assert rec.action_input == "candidates=3,dispatch_target=N-1"
    assert rec.action_result == "hit_single"
    assert rec.status_from == "planning"
    assert rec.status_to == "running"
    assert rec.attempt == 0
    assert rec.ext_info == '{"schema_v":1,"strategy":"direct"}'
    assert rec.analysis is None
    assert rec.gmt_create == datetime(2026, 9, 17, 10, 0, 0)
    assert rec.gmt_modified == datetime(2026, 9, 17, 10, 0, 0)
    # exactly one event row visible via list_events_by_task
    events = repo.list_events_by_task("T-1")
    assert len(events) == 1
    assert events[0] == rec


def test_insert_event_is_append_only_duplicates_allowed(db):
    """No idempotency check: two inserts with the same payload produce 2 rows
    (duplicate emissions are business-accepted; the table has no unique key)."""
    repo = TaskTrajectoryRepository(db)
    repo.insert_event(_event(action_type="dispatch"))
    repo.insert_event(_event(action_type="dispatch"))  # duplicate emission
    events = repo.list_events_by_task("T-1")
    assert len(events) == 2
    assert all(e.action_type == "dispatch" for e in events)
    assert events[0].id != events[1].id


def test_insert_event_writes_gmt_from_record_and_ext_info_as_is(db):
    """insert_event writes gmt_create/gmt_modified from the record (datetime),
    and stores ext_info/analysis as-is (raw strings, no JSON parsing)."""
    repo = TaskTrajectoryRepository(db)
    custom = _event(
        ext_info='{"k":"v","n":1}',
        analysis='{"analysis_type":"tc_bot"}',
        gmt_create=datetime(2026, 9, 17, 14, 30, 15),
        gmt_modified=datetime(2026, 9, 17, 14, 30, 45),
    )
    stored = repo.insert_event(custom)
    assert stored.gmt_create == datetime(2026, 9, 17, 14, 30, 15)
    assert stored.gmt_modified == datetime(2026, 9, 17, 14, 30, 45)
    assert stored.ext_info == '{"k":"v","n":1}'
    assert stored.analysis == '{"analysis_type":"tc_bot"}'


# ---------------------------------------------------------------------------
# upsert_head
# ---------------------------------------------------------------------------


def test_upsert_head_inserts_new_head_with_analysis_none(db):
    repo = TaskTrajectoryRepository(db)
    head = repo.upsert_head("T-1")
    assert isinstance(head, TaskTrajectoryRecord)
    assert head.id > 0
    assert head.task_id == "T-1"
    assert head.analysis is None
    assert head.gmt_create is not None
    assert head.gmt_modified is not None


def test_upsert_head_preserves_existing_analysis_and_gmt_modified(db):
    """Second upsert_head MUST NOT overwrite analysis/gmt_modified (spec: "已有行的
    analysis/gmt_modified 不被覆盖"). Backfill analysis first, then call upsert_head
    and assert analysis + gmt_modified are unchanged."""
    repo = TaskTrajectoryRepository(db)
    repo.upsert_head("T-1")
    backfill_ts = datetime(2026, 9, 17, 11, 30, 0)
    affected = repo.backfill_analysis(
        "T-1", '{"analysis_type":"tc_bot"}', now=backfill_ts
    )
    assert affected >= 1  # at least the head row updated

    head_before = repo.list_head("T-1")
    assert head_before.analysis == '{"analysis_type":"tc_bot"}'
    assert head_before.gmt_modified == backfill_ts

    # second upsert_head: must preserve analysis + gmt_modified, same row
    head_after = repo.upsert_head("T-1")
    assert head_after.id == head_before.id
    assert head_after.analysis == '{"analysis_type":"tc_bot"}'  # preserved
    assert head_after.gmt_modified == backfill_ts  # preserved


# ---------------------------------------------------------------------------
# backfill_analysis
# ---------------------------------------------------------------------------


def test_backfill_analysis_updates_both_tables_and_gmt_modified(db):
    """backfill sets analysis AND gmt_modified on BOTH task_trajectory and
    task_trajectory_events; returns total affected rows; gmt_create is NOT
    touched."""
    repo = TaskTrajectoryRepository(db)
    repo.upsert_head("T-1")
    repo.insert_event(
        _event(task_id="T-1", node_id="N-1", gmt_create=datetime(2026, 9, 17, 10, 0, 0))
    )
    repo.insert_event(
        _event(task_id="T-1", node_id="N-2", gmt_create=datetime(2026, 9, 17, 10, 5, 0))
    )

    orig_head = repo.list_head("T-1")
    orig_events = repo.list_events_by_task("T-1")
    assert orig_head.analysis is None
    assert all(e.analysis is None for e in orig_events)

    backfill_ts = datetime(2026, 9, 17, 12, 0, 0)
    affected = repo.backfill_analysis(
        "T-1", '{"analysis_type":"tc_bot"}', now=backfill_ts
    )
    # head (1) + events (2) = 3
    assert affected == 3

    head = repo.list_head("T-1")
    assert head.analysis == '{"analysis_type":"tc_bot"}'
    assert head.gmt_modified == backfill_ts  # explicitly set, not onupdate

    events = repo.list_events_by_task("T-1")
    assert len(events) == 2
    assert all(e.analysis == '{"analysis_type":"tc_bot"}' for e in events)
    assert all(e.gmt_modified == backfill_ts for e in events)  # explicitly set
    # gmt_create must NOT be touched by backfill
    assert {e.gmt_create for e in events} == {
        datetime(2026, 9, 17, 10, 0, 0),
        datetime(2026, 9, 17, 10, 5, 0),
    }


def test_backfill_analysis_explicit_gmt_modified_not_relying_on_onupdate(db):
    """gmt_modified is set EXPLICITLY in the UPDATE dict (do NOT rely on the ORM
    ``onupdate=func.now()`` — ``Query.update()`` bypasses Python-side onupdate
    callbacks, so the column would otherwise be stale). Verify the exact value
    passed via ``now=`` is what lands in the row."""
    repo = TaskTrajectoryRepository(db)
    repo.upsert_head("T-1")
    repo.insert_event(_event(task_id="T-1", node_id="N-1"))

    backfill_ts = datetime(2026, 9, 17, 15, 42, 17)
    repo.backfill_analysis("T-1", '{"a":1}', now=backfill_ts)

    head = repo.list_head("T-1")
    assert head.gmt_modified == backfill_ts
    [event] = repo.list_events_by_task("T-1")
    assert event.gmt_modified == backfill_ts


def test_backfill_analysis_falls_back_to_utcnow_when_now_omitted(db):
    """When ``now=None``, gmt_modified is set to ~utcnow inside the repo (still
    explicitly — NOT via onupdate)."""
    repo = TaskTrajectoryRepository(db)
    repo.upsert_head("T-1")
    before = datetime.utcnow()
    affected = repo.backfill_analysis("T-1", '{"a":1}')
    after = datetime.utcnow()
    assert affected == 1  # only the head row
    head = repo.list_head("T-1")
    assert head.analysis == '{"a":1}'
    assert head.gmt_modified is not None
    assert before <= head.gmt_modified <= after


def test_backfill_analysis_unknown_task_affects_zero_rows(db):
    """backfill on a task with no head and no events returns 0 (both UPDATEs
    match zero rows)."""
    repo = TaskTrajectoryRepository(db)
    assert repo.backfill_analysis("missing", '{"a":1}') == 0
    assert repo.list_head("missing") is None
    assert repo.list_events_by_task("missing") == []


# ---------------------------------------------------------------------------
# list_events_by_task
# ---------------------------------------------------------------------------


def test_list_events_by_task_orders_by_gmt_create_then_id(db):
    """M2 guarantee: same-second ties are broken by ``id ASC`` (the deterministic
    tiebreaker). Plant two events sharing the SAME ``gmt_create`` second and
    assert they come back in ``id`` order; a third event with an earlier
    ``gmt_create`` must come first regardless of id."""
    repo = TaskTrajectoryRepository(db)
    same_second = datetime(2026, 9, 17, 10, 0, 0)
    # first insert → lowest id
    repo.insert_event(
        _event(task_id="T-1", node_id="N-1", action_type="dispatch",
               gmt_create=same_second, gmt_modified=same_second)
    )
    # second insert → next id
    repo.insert_event(
        _event(task_id="T-1", node_id="N-2", action_type="execute",
               gmt_create=same_second, gmt_modified=same_second)
    )
    # third event with an EARLIER gmt_create — must come first regardless of id
    earlier = datetime(2026, 9, 17, 9, 0, 0)
    repo.insert_event(
        _event(task_id="T-1", node_id="N-0", action_type="plan",
               gmt_create=earlier, gmt_modified=earlier)
    )

    events = repo.list_events_by_task("T-1")
    assert [e.action_type for e in events] == ["plan", "dispatch", "execute"]
    # the two same-second events must be in ascending id order
    same_sec = [e for e in events if e.gmt_create == same_second]
    assert len(same_sec) == 2
    assert same_sec[0].id < same_sec[1].id


def test_list_events_by_task_scopes_to_task_id(db):
    """list_events_by_task only returns rows for the given task_id."""
    repo = TaskTrajectoryRepository(db)
    repo.insert_event(_event(task_id="T-1", node_id="N-1"))
    repo.insert_event(_event(task_id="T-2", node_id="N-1"))
    events = repo.list_events_by_task("T-1")
    assert len(events) == 1
    assert all(e.task_id == "T-1" for e in events)
    assert repo.list_events_by_task("no-events") == []


# ---------------------------------------------------------------------------
# list_head
# ---------------------------------------------------------------------------


def test_list_head_returns_none_for_unknown_task(db):
    repo = TaskTrajectoryRepository(db)
    assert repo.list_head("missing") is None


def test_list_head_returns_record_for_known_task(db):
    repo = TaskTrajectoryRepository(db)
    repo.upsert_head("T-1")
    repo.backfill_analysis("T-1", '{"x":1}', now=datetime(2026, 9, 17, 12, 0, 0))
    head = repo.list_head("T-1")
    assert head is not None
    assert isinstance(head, TaskTrajectoryRecord)
    assert head.task_id == "T-1"
    assert head.analysis == '{"x":1}'

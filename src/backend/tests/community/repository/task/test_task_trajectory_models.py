"""Smoke + roundtrip tests for the trajectory storage ORM models (REQ-11 + REQ-P1).

Pure-schema task (P1a): verifies ``Base.metadata.create_all`` provisions the three
new tables, the unique indexes reject duplicates, the non-unique timeline indexes
exist, and ``to_record()`` round-trips a fully-populated event row. Repository
behavior (INSERT/UPSERT/backfill/list) is exercised in P1b.
"""
from datetime import datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.repository.models import (
    TaskCallbackCorrelationModel,
    TaskTrajectoryEventModel,
    TaskTrajectoryModel,
)
from agentclaw.community.core.task.repository.types import (
    TaskCallbackCorrelationRecord,
    TaskTrajectoryRecord,
    TrajectoryEventRecord,
)


def _full_event(**overrides) -> TaskTrajectoryEventModel:
    defaults = dict(
        task_id="T-1",
        node_id="N-1",
        action_type="dispatch",
        action_input="candidates=3,dispatch_target=N-1",
        action_result="hit_single",
        status_from="planning",
        status_to="running",
        attempt=0,
        error_type=None,
        error_msg=None,
        ext_info='{"schema_v":1,"strategy":"direct"}',
        analysis=None,
        gmt_create=datetime(2026, 9, 17, 10, 0, 0),
        gmt_modified=datetime(2026, 9, 17, 10, 0, 0),
    )
    defaults.update(overrides)
    return TaskTrajectoryEventModel(**defaults)


def test_three_trajectory_tables_build(engine):
    """create_all provisions task_trajectory / task_trajectory_events /
    task_callback_correlation (conftest imports the models module)."""
    inspector = inspect(engine)
    for table in (
        "task_trajectory",
        "task_trajectory_events",
        "task_callback_correlation",
    ):
        assert inspector.has_table(table), f"missing table {table}"


def test_event_model_roundtrip(db):
    """Insert a fully-populated event row, read it back, and to_record() matches."""
    with db.orm_session() as session:
        session.add(_full_event())

    with db.orm_session() as session:
        row = session.execute(select(TaskTrajectoryEventModel)).scalar_one()
        record = row.to_record()

    assert isinstance(record, TrajectoryEventRecord)
    assert record.id > 0
    assert record.task_id == "T-1"
    assert record.node_id == "N-1"
    assert record.action_type == "dispatch"
    assert record.action_input == "candidates=3,dispatch_target=N-1"
    assert record.action_result == "hit_single"
    assert record.status_from == "planning"
    assert record.status_to == "running"
    assert record.attempt == 0
    assert record.error_type is None
    assert record.error_msg is None
    assert record.ext_info == '{"schema_v":1,"strategy":"direct"}'
    assert record.analysis is None
    assert record.gmt_create == datetime(2026, 9, 17, 10, 0, 0)
    assert record.gmt_modified == datetime(2026, 9, 17, 10, 0, 0)


def test_task_trajectory_unique_task_id_rejects_duplicate(db):
    """uk_task_trajectory_task(task_id) is a unique index: a second row with the
    same task_id must raise IntegrityError on commit."""
    with db.orm_session() as session:
        session.add(TaskTrajectoryModel(task_id="T-1", analysis=None))

    with pytest.raises(IntegrityError):
        with db.orm_session() as session:
            session.add(TaskTrajectoryModel(task_id="T-1", analysis=None))


def test_callback_correlation_unique_event_id_rejects_duplicate(db):
    """uk_task_callback_correlation_event(event_id) is unique: a second row with
    the same event_id must raise IntegrityError."""
    with db.orm_session() as session:
        session.add(TaskCallbackCorrelationModel(
            event_id="evt-1",
            main_session_id="S-1",
            task_id="T-1",
            node_id="N-1",
            retry=0,
        ))

    with pytest.raises(IntegrityError):
        with db.orm_session() as session:
            session.add(TaskCallbackCorrelationModel(
                event_id="evt-1",
                main_session_id="S-2",
                task_id="T-2",
                node_id="N-2",
                retry=1,
            ))


def test_non_unique_indexes_exist(engine):
    """The timeline/node lookup indexes (non-unique) are declared on metadata and
    surface on SQLite via get_indexes."""
    inspector = inspect(engine)

    def index_cols(table):
        return {i["name"]: tuple(i["column_names"]) for i in inspector.get_indexes(table)}

    events = index_cols("task_trajectory_events")
    assert events["idx_task_trajectory_events_task"] == ("task_id", "gmt_create")
    assert events["idx_task_trajectory_events_node"] == ("task_id", "node_id", "gmt_create")

    correlation = index_cols("task_callback_correlation")
    assert correlation["idx_task_callback_correlation_node"] == ("task_id", "node_id")


def test_trajectory_head_and_correlation_roundtrip(db):
    """to_record() on TaskTrajectoryModel and TaskCallbackCorrelationModel."""
    with db.orm_session() as session:
        session.add(TaskTrajectoryModel(
            task_id="T-1",
            analysis='{"analysis_type":"tc_bot"}',
            gmt_create=datetime(2026, 9, 17, 11, 0, 0),
            gmt_modified=datetime(2026, 9, 17, 11, 5, 0),
        ))
        session.add(TaskCallbackCorrelationModel(
            event_id="evt-2",
            main_session_id="S-1",
            task_id="T-1",
            node_id="N-3",
            retry=2,
            gmt_create=datetime(2026, 9, 17, 12, 0, 0),
        ))

    # Build records inside the session while the ORM instances are still bound
    # (mirrors the repository idiom: to_record() is called pre-detach).
    with db.orm_session() as session:
        head = session.execute(
            select(TaskTrajectoryModel).where(TaskTrajectoryModel.task_id == "T-1")
        ).scalar_one()
        head_rec = head.to_record()
        corr = session.execute(
            select(TaskCallbackCorrelationModel).where(
                TaskCallbackCorrelationModel.event_id == "evt-2"
            )
        ).scalar_one()
        corr_rec = corr.to_record()

    assert isinstance(head_rec, TaskTrajectoryRecord)
    assert head_rec.task_id == "T-1"
    assert head_rec.analysis == '{"analysis_type":"tc_bot"}'
    assert head_rec.gmt_create == datetime(2026, 9, 17, 11, 0, 0)
    assert head_rec.gmt_modified == datetime(2026, 9, 17, 11, 5, 0)

    assert isinstance(corr_rec, TaskCallbackCorrelationRecord)
    assert corr_rec.event_id == "evt-2"
    assert corr_rec.main_session_id == "S-1"
    assert corr_rec.task_id == "T-1"
    assert corr_rec.node_id == "N-3"
    assert corr_rec.retry == 2
    assert corr_rec.gmt_create == datetime(2026, 9, 17, 12, 0, 0)

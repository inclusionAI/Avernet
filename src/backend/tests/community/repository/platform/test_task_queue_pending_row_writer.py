"""Session-bound pending-row writer integration contracts."""

from contextlib import contextmanager
import inspect

import pytest
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.repository.pending_row_writer import (
    TaskQueuePendingRowWriter,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import DEFAULT_APP, TaskStatus


pytestmark = pytest.mark.integration


class _Database:
    def __init__(self, engine) -> None:
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        with self.transactional_orm_session() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    metadata = MetaData()
    business_facts = Table(
        "phase2_test_business_fact",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", String(50), nullable=False),
    )
    metadata.create_all(engine)
    return _Database(engine), engine, business_facts


def _write(writer, session, **overrides):
    values = {
        "task_type": "phase2-test",
        "payload": {"attempt_id": 42},
        "delay_seconds": 0,
        "deadline_seconds": 3600,
        "env": "dev",
        "app": DEFAULT_APP,
        "idempotency_key": None,
    }
    values.update(overrides)
    return writer.write_pending(session, **values)


def test_public_service_enqueue_does_not_expose_a_session_parameter() -> None:
    assert "session" not in inspect.signature(TaskQueueService.enqueue).parameters


def test_key_conflict_resolution_is_a_mysql_current_locking_read() -> None:
    statement = TaskQueuePendingRowWriter()._key_holder_statement(
        env="prod",
        app=DEFAULT_APP,
        task_type="publication",
        idempotency_key="publication:42",
    )

    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "FOR UPDATE" in sql
    assert statement.get_execution_options()["populate_existing"] is True


def test_business_fact_and_pending_task_commit_atomically(database) -> None:
    db, engine, business_facts = database
    writer = TaskQueuePendingRowWriter()

    with db.transactional_orm_session() as session:
        session.execute(business_facts.insert().values(id=1, value="frozen"))
        result = _write(writer, session)
        assert result.created is True
        assert result.record.status is TaskStatus.PENDING

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(business_facts)) == 1
        assert connection.scalar(
            select(func.count()).select_from(TaskQueueModel.__table__)
        ) == 1


def test_outer_transaction_rollback_removes_business_fact_and_pending_task(
    database,
) -> None:
    db, engine, business_facts = database
    writer = TaskQueuePendingRowWriter()

    with pytest.raises(RuntimeError, match="abort publication"):
        with db.transactional_orm_session() as session:
            session.execute(business_facts.insert().values(id=1, value="frozen"))
            _write(writer, session)
            raise RuntimeError("abort publication")

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(business_facts)) == 0
        assert connection.scalar(
            select(func.count()).select_from(TaskQueueModel.__table__)
        ) == 0


def test_active_idempotency_joins_existing_row_inside_same_transaction(
    database,
) -> None:
    db, _, _ = database
    writer = TaskQueuePendingRowWriter()

    with db.transactional_orm_session() as session:
        first = _write(writer, session, idempotency_key="publication:42")
        second = _write(writer, session, idempotency_key="publication:42")

    assert first.created is True
    assert second.created is False
    assert second.record.id == first.record.id


def test_duplicate_savepoint_does_not_rollback_outer_business_fact(
    database,
) -> None:
    db, engine, business_facts = database
    writer = TaskQueuePendingRowWriter()
    with db.transactional_orm_session() as session:
        first = _write(writer, session, idempotency_key="publication:42")

    with db.transactional_orm_session() as session:
        session.execute(business_facts.insert().values(id=1, value="joined"))
        duplicate = _write(
            writer,
            session,
            idempotency_key="publication:42",
        )
        assert duplicate.created is False
        assert duplicate.record.id == first.record.id

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(business_facts)) == 1
        assert connection.scalar(
            select(func.count()).select_from(TaskQueueModel.__table__)
        ) == 1


def test_writer_preserves_payload_owner_and_db_clock_fields(database) -> None:
    db, _, _ = database
    writer = TaskQueuePendingRowWriter()

    with db.transactional_orm_session() as session:
        result = _write(
            writer,
            session,
            payload={"nested": [1, 2], "message": "你好"},
            delay_seconds=5,
            deadline_seconds=30,
            env="pre",
            app="teclaw",
        )

    stored = TaskQueueRepository(db).get_by_id(result.record.id)
    assert stored is not None
    assert stored.payload == {"nested": [1, 2], "message": "你好"}
    assert stored.env == "pre"
    assert stored.app == "teclaw"
    assert stored.run_at is not None
    assert stored.deadline_at is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("idempotency_key", ""),
        ("idempotency_key", " key"),
        ("idempotency_key", "k" * 191),
        ("task_type", " task"),
        ("task_type", "t" * 101),
    ],
)
def test_writer_keeps_key_and_scope_validation(database, field, value) -> None:
    db, _, _ = database
    writer = TaskQueuePendingRowWriter()
    overrides = {field: value, "idempotency_key": "key"}
    if field == "idempotency_key":
        overrides = {field: value}

    with pytest.raises(ValueError):
        with db.transactional_orm_session() as session:
            _write(writer, session, **overrides)

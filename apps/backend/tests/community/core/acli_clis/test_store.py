"""``SqlAcliOperationStore`` against in-memory SQLite, plus the DDL beside the model."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.acli_clis.repository.models import (
    AcliOperationModel,
    AcliOperationRecord,
)
from agentclaw.community.core.acli_clis.repository.store import (
    AcliOperationNotRecordedError,
    SqlAcliOperationStore,
)
from agentclaw.community.core.base import Base


class _DB:
    def __init__(self, engine):
        self._sessions = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._sessions()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def store() -> SqlAcliOperationStore:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[AcliOperationModel.__table__])
    return SqlAcliOperationStore(_DB(engine))


def _record(op_id: str, *, kind: str = "operation", stage: str = "draft", **fields):
    return AcliOperationRecord(
        operation_id=op_id, task_id=fields.pop("task_id", 1), kind=kind, action="install",
        cli_code="yuque-cli", bot_pk=fields.pop("bot_pk", 7), stage=stage, **fields,
    )


def test_a_row_reads_back_and_records_its_engine_operation(store) -> None:
    store.create(_record("a" * 32, task_id=41))

    store.set_engine_operation_id("a" * 32, "e" * 32)

    assert store.get("a" * 32) == _record("a" * 32, task_id=41, engine_operation_id="e" * 32)
    assert store.get("b" * 32) is None


def test_an_operation_id_is_written_once(store) -> None:
    store.create(_record("a" * 32))

    with pytest.raises(IntegrityError):
        store.create(_record("a" * 32))


def test_recording_an_engine_id_for_no_row_raises(store) -> None:
    with pytest.raises(AcliOperationNotRecordedError):
        store.set_engine_operation_id("c" * 32, "e" * 32)


def test_latest_is_per_runtime_and_kind(store) -> None:
    store.create(_record("1" * 32))
    store.create(_record("2" * 32))
    store.create(_record("3" * 32, kind="cancel", target_operation_id="2" * 32))
    store.create(_record("4" * 32, stage="online"))
    store.create(_record("5" * 32, bot_pk=8))

    assert store.latest(bot_pk=7, stage="draft", kind="operation").operation_id == "2" * 32
    assert store.latest(bot_pk=7, stage="draft", kind="cancel").target_operation_id == "2" * 32
    assert store.latest(bot_pk=7, stage="verify", kind="operation") is None


def test_the_ddl_declares_every_model_column() -> None:
    ddl = (
        Path(__file__).parents[4]
        / "src/agentclaw/community/core/acli_clis/sql/2026_10_21_acli_operation.sql"
    ).read_text()
    for column in AcliOperationModel.__table__.columns:
        assert f"`{column.name}`" in ddl, column.name
    assert "UNIQUE KEY `uk_operation_id`" in ddl
    assert "KEY `idx_env_bot_stage_kind`" in ddl

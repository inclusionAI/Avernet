"""Integration tests for BotCopyRepository against real in-memory SQLite.

The same ORM body runs on prod OceanBase, so the operation-ledger round-trip,
the batch-by-dst read, and the retry CAS throttle are all exercised against a
real database here.
"""

from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Side-effect import: registers AcBotCopyModel on Base.metadata so create_all()
# builds the ac_bot_copy table.
from agentclaw.community.core.bot_copy.repository.models import (  # noqa: F401
    AcBotCopyModel,
)
from agentclaw.community.core.bot_copy.repository.sqlalchemy_impl import (
    BotCopyRepositoryImpl,
)
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus

pytestmark = pytest.mark.integration

ENV = "dev"
TENANT = "teamclaw"


class _SessionDB:
    """Mirrors the task_queue test's in-memory SQLite session wrapper."""

    def __init__(self, engine):
        self._sf = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._sf()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def copy_repo():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return BotCopyRepositoryImpl(_SessionDB(engine))


def _row(**overrides) -> BotCopyRow:
    base = dict(
        operation_id="op_1",
        source_bot_id="s1",
        source_owner_id="u1",
        dst_bot_id="d1",
        dst_owner_id="u1",
        operator_id="u1",
        status=BotCopyStatus.INITIAL,
        env=ENV,
        avernet_tenant=TENANT,
    )
    base.update(overrides)
    return BotCopyRow(**base)


def test_insert_get_by_operation_id_and_dst_roundtrip(copy_repo):
    copy_repo.insert(_row())
    by_op = copy_repo.get_by_operation_id(env=ENV, operation_id="op_1")
    assert by_op is not None
    assert by_op.status == BotCopyStatus.INITIAL
    assert by_op.source_bot_id == "s1"

    by_dst = copy_repo.get_by_dst_bot_id(env=ENV, dst_bot_id="d1")
    assert by_dst is not None
    assert by_dst.operation_id == "op_1"


def test_get_many_by_dst_bot_ids_skips_missing(copy_repo):
    copy_repo.insert(_row(operation_id="op_a", dst_bot_id="d1"))
    copy_repo.insert(_row(operation_id="op_b", dst_bot_id="d2"))
    batch = copy_repo.get_many_by_dst_bot_ids(env=ENV, dst_bot_ids=["d1", "d_missing"])
    assert set(batch) == {"d1"}
    assert batch["d1"].operation_id == "op_a"


def test_get_returns_none_when_missing(copy_repo):
    assert copy_repo.get_by_operation_id(env=ENV, operation_id="nope") is None
    assert copy_repo.get_by_dst_bot_id(env=ENV, dst_bot_id="nope") is None


def test_cas_retry_advances_then_throttles_for_60s(copy_repo):
    copy_repo.insert(_row())
    t0 = datetime(2026, 11, 2, 10, 0, 0)
    assert copy_repo.cas_retry(env=ENV, operation_id="op_1", now=t0) is True
    refreshed = copy_repo.get_by_operation_id(env=ENV, operation_id="op_1")
    assert refreshed.retries == 1
    assert refreshed.last_retry_at == t0

    # 30s later — within the 60s throttle window, no-op.
    assert copy_repo.cas_retry(env=ENV, operation_id="op_1", now=datetime(2026, 11, 2, 10, 0, 30)) is False
    assert copy_repo.get_by_operation_id(env=ENV, operation_id="op_1").retries == 1

    # 61s later — past the window, advances again.
    assert copy_repo.cas_retry(env=ENV, operation_id="op_1", now=datetime(2026, 11, 2, 10, 1, 1)) is True
    assert copy_repo.get_by_operation_id(env=ENV, operation_id="op_1").retries == 2


def test_cas_retry_refused_for_terminal_statuses(copy_repo):
    copy_repo.insert(_row(status=BotCopyStatus.CONVERGED, last_retry_at=None))
    assert copy_repo.cas_retry(env=ENV, operation_id="op_1", now=datetime(2026, 11, 2, 10, 0, 0)) is False


def test_update_progress_ext_persists_json(copy_repo):
    copy_repo.insert(_row())
    copy_repo.update_progress_ext(
        env=ENV, operation_id="op_1", progress_ext={"screen_copied": True}
    )
    refreshed = copy_repo.get_by_operation_id(env=ENV, operation_id="op_1")
    assert refreshed.progress_ext == {"screen_copied": True}

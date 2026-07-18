"""Step1 谓词收口纯重构的语义守卫测试。

目的:钉死 ticket 侧 ``governance_status`` 谓词收口到 ``ACTIVE_STATUSES``
常量 / 枚举替字符串之后,各态工单被正确纳入或排除。这不是"改前后快照对比"
(那需双跑基线),而是**钉死当前正确查询语义**:加新状态(如 OBSERVED)时,
这些断言会显式暴露"新态该不该被命中",防止散点谓词静默漂移。

覆盖的收口点(见 tasks.md Task 2/3):
  - find_active_ticket  → in_(ACTIVE_STATUSES)
  - count_active_open   → in_(ACTIVE_STATUSES)
  - find_latest_closed_by_worker → == GovernanceStatus.CLOSED

复用 test_task_record_repo_coverage.py 的 fixture 范式。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.economy.governance.domain.enums import (
    ACTIVE_STATUSES,
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.repositories.orm import (
    Base,
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)

from .conftest import FakeDB


# ── Fixtures (复用 test_task_record_repo_coverage 范式) ────────────────


@pytest.fixture()
def engine():
    from sqlalchemy import create_engine

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    import agentclaw.community.core.economy.governance.repositories.orm  # noqa: F401
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(Session)


@pytest.fixture()
def repo(db):
    return TaskRecordRepository(db=db)


def _make_ticket(**overrides) -> GovernanceTicketOrm:
    """Build a valid GovernanceTicketOrm with sensible defaults."""
    import uuid
    now = datetime.now()
    uid = uuid.uuid4().hex[:8]
    defaults = dict(
        worker_id=f"w:{uid}",
        owner_id=f"o:{uid}",
        bot_id=f"b:{uid}",
        bot_name="TestBot",
        dt_version="20260710",
        governance_decision="actionable",
        latest_decision="actionable",
        governance_status="open",
        active_worker=f"w:{uid}",
        ticket_id=f"tkt-{uid}",
        analysis_status="completed",
        last_sync_at=now,
    )
    defaults.update(overrides)
    return GovernanceTicketOrm(**defaults)


def _insert(engine, *rows: GovernanceTicketOrm) -> None:
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        for r in rows:
            s.add(r)
        s.commit()


# ── ACTIVE_STATUSES 常量一致性 ─────────────────────────────────────────


class TestActiveStatusesConstant:
    """ACTIVE_STATUSES 必须等于三活跃态(OBSERVED 加入后仍不含它)。"""

    def test_active_statuses_equals_three_active_states(self):
        assert ACTIVE_STATUSES == frozenset({
            GovernanceStatus.OPEN,
            GovernanceStatus.SCHEDULED,
            GovernanceStatus.WAITING_REVIEW,
        })

    def test_closed_not_in_active(self):
        assert GovernanceStatus.CLOSED not in ACTIVE_STATUSES


# ── find_active_ticket → in_(ACTIVE_STATUSES) ──────────────────────────


class TestFindActiveTicketPredicate:
    """find_active_ticket 只命中 ACTIVE_STATUSES 内的态,closed 排除。"""

    @pytest.mark.parametrize(
        "status,expected_found",
        [
            ("open", True),
            ("scheduled", True),
            ("waiting_review", True),
            ("closed", False),
        ],
    )
    def test_status_inclusion(self, repo, engine, status, expected_found):
        worker = "ownerX:botX"
        row = _make_ticket(
            worker_id=worker,
            active_worker=worker,
            ticket_id=f"tkt-{status}",
            governance_status=status,
        )
        _insert(engine, row)

        result = repo.find_active_ticket(worker)
        if expected_found:
            assert result is not None
            assert result.governance_status == status
        else:
            assert result is None

    def test_picks_one_among_active_for_same_worker(self, repo, engine):
        """同 worker 只能有一条活跃单(UNIQUE 约束语义);命中活跃那条。"""
        worker = "ownerM:botM"
        # closed 历史单 + 活跃单 同 worker
        _insert(
            engine,
            _make_ticket(
                worker_id=worker,
                active_worker=None,
                ticket_id="tkt-closed",
                governance_status="closed",
                closed_at=datetime.now(),
            ),
            _make_ticket(
                worker_id=worker,
                active_worker=worker,
                ticket_id="tkt-open",
                governance_status="open",
            ),
        )
        result = repo.find_active_ticket(worker)
        assert result is not None
        assert result.ticket_id == "tkt-open"


# ── count_active_open → in_(ACTIVE_STATUSES) ───────────────────────────


class TestCountActiveOpenPredicate:
    """count_active_open 只数 ACTIVE_STATUSES 内 + active_worker 非空的。"""

    def test_counts_only_active_with_worker(self, repo, engine):
        _insert(
            engine,
            # 活跃 + 有 worker → 计
            _make_ticket(
                ticket_id="t1", governance_status="open",
                active_worker="w1", worker_id="w1",
            ),
            # 活跃 + 有 worker → 计
            _make_ticket(
                ticket_id="t2", governance_status="scheduled",
                active_worker="w2", worker_id="w2",
            ),
            # 活跃 + 有 worker → 计
            _make_ticket(
                ticket_id="t3", governance_status="waiting_review",
                active_worker="w3", worker_id="w3",
            ),
            # closed → 不计
            _make_ticket(
                ticket_id="t4", governance_status="closed",
                active_worker="w4", worker_id="w4",
            ),
            # open 但 active_worker NULL → 不计
            _make_ticket(
                ticket_id="t5", governance_status="open",
                active_worker=None, worker_id="w5",
            ),
        )
        assert repo.count_active_open() == 3


# ── find_latest_closed_by_worker → == GovernanceStatus.CLOSED ─────────


class TestFindLatestClosedPredicate:
    """find_latest_closed_by_worker 只命中 closed 态(单态精确查询,非集合)。"""

    def test_returns_most_recently_closed(self, repo, engine):
        worker = "ownerC:botC"
        base = dict(
            worker_id=worker,
            active_worker=None,
            governance_status="closed",
        )
        older = datetime(2026, 1, 1)
        newer = datetime(2026, 6, 1)
        _insert(
            engine,
            _make_ticket(
                ticket_id="old", closed_at=older, gmt_create=older, **base,
            ),
            _make_ticket(
                ticket_id="new", closed_at=newer, gmt_create=newer, **base,
            ),
        )
        result = repo.find_latest_closed_by_worker(worker)
        assert result is not None
        assert result.ticket_id == "new"

    def test_open_not_returned(self, repo, engine):
        """非 closed 的活跃单不被 find_latest_closed 命中。"""
        worker = "ownerO:botO"
        _insert(
            engine,
            _make_ticket(
                ticket_id="open", worker_id=worker,
                active_worker=worker, governance_status="open",
            ),
        )
        assert repo.find_latest_closed_by_worker(worker) is None

    def test_no_closed_returns_none(self, repo):
        assert repo.find_latest_closed_by_worker("nobody:here") is None
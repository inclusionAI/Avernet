"""Tests for GovernanceWorkflowService.list_ticket_history_by_worker。

按 worker 取最近 N 条工单历史(全状态)的解析与回显;只读路径,不碰状态机。
worker_id 解析与 GovernanceAuditReadService._parse_worker_id 同款(单冒号、两段
非空、无空白);解析后 owner/bot/worker 全空 → ValueError(路由侧 400)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.economy.governance.services.workflow_service import (
    GovernanceWorkflowService,
)

from tests.community.core.economy.governance.test_admin_service import (
    _build_workflow_svc,
    _make_task_record,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def engine():
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

    import agentclaw.community.core.economy.governance.orm  # noqa: F401
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


def _insert(session, *, ticket_id, worker_id, bot_id=None, owner_id=None, gmt_create=None):
    """Insert one closed-history ticket (active_worker=None 避 UK 冲突)."""
    return _make_task_record(
        session,
        ticket_id=ticket_id,
        governance_status="closed",
        worker_id=worker_id,
        bot_id=bot_id or worker_id.split(":", 1)[1],
        owner_id=owner_id or worker_id.split(":", 1)[0],
        gmt_create=gmt_create or datetime.now(),
    )


def _insert_open(session, *, ticket_id, worker_id, gmt_create=None):
    """Insert the single active ticket for a worker (UK active_worker 唯一)."""
    return _make_task_record(
        session,
        ticket_id=ticket_id,
        governance_status="open",
        worker_id=worker_id,
        bot_id=worker_id.split(":", 1)[1],
        owner_id=worker_id.split(":", 1)[0],
        gmt_create=gmt_create or datetime.now(),
    )


# ── Tests ─────────────────────────────────────────────────────────


class TestListTicketHistoryByWorker:
    """list_ticket_history_by_worker(...) → (tickets, owner, bot, worker_echo)"""

    def test_worker_id_priority_overrides_owner_bot(self, engine, session):
        svc, _ = _build_workflow_svc(engine)
        # worker_id=owner:bot 应覆盖传入的独立 owner_id/bot_id。
        tickets, owner, bot, echo = svc.list_ticket_history_by_worker(
            worker_id="ownerA:botX",
            owner_id="owner-something-else",
            bot_id="bot-something-else",
        )
        assert owner == "ownerA"
        assert bot == "botX"
        assert echo == "ownerA:botX"
        assert tickets == []

    def test_returns_recent_tickets_gmt_create_desc(self, engine, session):
        svc, _ = _build_workflow_svc(engine)
        now = datetime.now()
        _insert(session, ticket_id="t-old", worker_id="o:b", gmt_create=now - timedelta(days=2))
        _insert(session, ticket_id="t-mid", worker_id="o:b", gmt_create=now - timedelta(days=1))
        _insert_open(session, ticket_id="t-new", worker_id="o:b", gmt_create=now)

        tickets, owner, bot, echo = svc.list_ticket_history_by_worker(
            worker_id="o:b", limit=10,
        )
        assert [t.ticket_id for t in tickets] == ["t-new", "t-mid", "t-old"]
        assert (owner, bot, echo) == ("o", "b", "o:b")

    def test_limit_passed_to_repo(self, engine, session):
        svc, _ = _build_workflow_svc(engine)
        now = datetime.now()
        for i in range(5):
            _insert(
                session, ticket_id=f"t-{i}", worker_id="o:b",
                gmt_create=now - timedelta(days=i),
            )

        tickets, *_ = svc.list_ticket_history_by_worker(worker_id="o:b", limit=2)
        assert [t.ticket_id for t in tickets] == ["t-0", "t-1"]

    def test_owner_only_no_worker_echo(self, engine, session):
        svc, _ = _build_workflow_svc(engine)
        now = datetime.now()
        _insert(session, ticket_id="t-a1", worker_id="o:a:b:1",
                bot_id="b:1", owner_id="o:a", gmt_create=now)
        _insert(session, ticket_id="t-a2", worker_id="o:a:b:2",
                bot_id="b:2", owner_id="o:a", gmt_create=now - timedelta(hours=1))

        tickets, owner, bot, echo = svc.list_ticket_history_by_worker(
            owner_id="o:a", limit=10,
        )
        assert {t.ticket_id for t in tickets} == {"t-a1", "t-a2"}
        assert owner == "o:a"
        assert bot is None
        assert echo is None  # 仅 owner 单维度,无法合成 worker

    def test_owner_plus_bot_composes_echo(self, engine, session):
        svc, _ = _build_workflow_svc(engine)
        now = datetime.now()
        _insert(session, ticket_id="t-hit", worker_id="o:a:b:1",
                bot_id="b:1", owner_id="o:a", gmt_create=now)
        _insert(session, ticket_id="t-miss", worker_id="o:b:b:1",
                bot_id="b:1", owner_id="o:b", gmt_create=now)

        tickets, owner, bot, echo = svc.list_ticket_history_by_worker(
            owner_id="o:a", bot_id="b:1",
        )
        assert [t.ticket_id for t in tickets] == ["t-hit"]
        assert (owner, bot, echo) == ("o:a", "b:1", "o:a:b:1")

    # ── worker_id 解析校验(对齐 audit_read_service._parse_worker_id) ──

    @pytest.mark.parametrize("bad_id", ["no-colon", "a:b:c", "a:", ":b", "a b:c"])
    def test_invalid_worker_id_raises(self, engine, bad_id):
        svc, _ = _build_workflow_svc(engine)
        with pytest.raises(ValueError, match="invalid worker_id"):
            svc.list_ticket_history_by_worker(worker_id=bad_id)

    def test_all_empty_raises(self, engine):
        svc, _ = _build_workflow_svc(engine)
        with pytest.raises(ValueError, match="at least one"):
            svc.list_ticket_history_by_worker()

    def test_invalid_worker_id_checked_before_all_empty_check(self, engine, session):
        """worker_id 非法(传了非法 worker_id 但 owner/bot 也空)→ worker_id 解析先报。"""
        svc, _ = _build_workflow_svc(engine)
        with pytest.raises(ValueError, match="invalid worker_id"):
            svc.list_ticket_history_by_worker(worker_id="bad")


# ── _parse_worker_id 直接单测(纯函数,逐字对齐 audit_read_service) ──


class TestParseWorkerId:
    def test_valid(self):
        assert GovernanceWorkflowService._parse_worker_id("owner:bot") == ("owner", "bot")

    def test_strips_whitespace(self):
        assert GovernanceWorkflowService._parse_worker_id(" o : b ") == ("o", "b")

    def test_whitespace_around_separator_is_stripped_valid(self):
        # 分隔符两侧的空白被 strip,属合法(段内空白才非法)。
        assert GovernanceWorkflowService._parse_worker_id("a : b") == ("a", "b")

    @pytest.mark.parametrize("bad", ["", ":", "a:", ":b", "a:b:c", "a b", "a:b c", "a\tb"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            GovernanceWorkflowService._parse_worker_id(bad)

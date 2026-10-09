"""Coverage supplement — task_record_repo uncovered methods.

Targets (from coverage report on lyp_dev_0710):
  - find_by_ticket_id (filter clause)
  - find_latest_closed_by_worker (return path)
  - list_scheduled_due (filter)
  - list_auto_silence_eligible (filter)
  - list_remindable_tickets (filter)
  - list_tickets_by_owner_and_statuses (filter)
  - find_ticket_by_notification_id
  - insert_ticket

Note: get_completed_decisions, _normalize_dt_field, _parse_gmt_create
were removed in the lyp_dev_0710 refactor — no longer in scope.

Repo methods return domain GovernanceTicket objects (not dicts).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.orm import GovernanceNotificationOrm, GovernanceTicketOrm
from agentclaw.community.core.repository.implementations.governance.task_record import TaskRecordRepository

from .conftest import FakeDB


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


def _make_notify(**overrides) -> GovernanceNotificationOrm:
    """Build a valid GovernanceNotificationOrm with sensible defaults."""
    import uuid
    uid = uuid.uuid4().hex[:8]
    defaults = dict(
        notification_id=f"notif-{uid}",
        ticket_id=f"tkt-{uid}",
        owner_id="owner1",
        bot_id="bot1",
        bot_name="TestBot",
        worker_id="owner1:bot1",
        dt_version="20260710",
        governance_decision="actionable",
        governance_cycle_id="cycle-001",
        governance_status="open",
        notify_status="pending",
    )
    defaults.update(overrides)
    return GovernanceNotificationOrm(**defaults)


# ── Tests ─────────────────────────────────────────────────────────


class TestFindByTicketId:
    """find_by_ticket_id(ticket_id) → GovernanceTicket | None"""

    def test_found(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-find-001", active_worker="w:find1"))
            s.commit()

        result = repo.find_by_ticket_id("tkt-find-001")
        assert result is not None
        assert result.ticket_id == "tkt-find-001"

    def test_not_found(self, repo):
        assert repo.find_by_ticket_id("nonexistent") is None


class TestSaveTicket:
    """save_ticket(ticket): 方案 A 持久化原语。

    find→apply_to→commit round-trip;apply_to 只写生命周期态,不碰快照。
    """

    def test_save_persists_lifecycle_change(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-save-001", active_worker="w:sv1",
                               governance_status="open"))
            s.commit()

        ticket = repo.find_by_ticket_id("tkt-save-001")
        assert ticket is not None
        # 模型上做状态机转移(驱动服务职责,此处直接调模型模拟)
        ticket.close(close_reason="auto_silenced_normal", closed_at=datetime.now())
        assert repo.save_ticket(ticket) is True

        reloaded = repo.find_by_ticket_id("tkt-save-001")
        assert reloaded.governance_status == GovernanceStatus.CLOSED
        assert reloaded.close_reason == "auto_silenced_normal"
        assert reloaded.assignee is None  # active_worker released on close
        assert reloaded.remind_at is None

    def test_save_not_found_returns_false(self, repo):
        # Build a detached model that has no DB row.
        ticket = GovernanceTicket.create(
            ticket_id="no-such-ticket",
            worker_id="w:x",
            bot_id="b:x",
            owner_id="o:x",
            owner_name=None,
            bot_name="X",
            snapshot=MutableSnapshot(
                dt_version="v1", initial_decision="actionable",
                current_decision="actionable", triggered_dimensions=None,
                hit_dimensions_count=None, severity=None,
                estimated_saving_tokens=None, saving_ratio=None,
                task_summary=None, notification_structured=None,
                analysis_status=None, consecutive_normal_days=0,
                last_decision_dt_version=None, last_seen_at=None,
                last_sync_at=datetime.now(),
            ),
        )
        assert repo.save_ticket(ticket) is False

    def test_save_preserves_snapshot(self, repo, engine):
        """save_ticket 用 apply_to,不改快照字段(dt_version 等)。"""
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-snap-001", active_worker="w:sn1",
                               governance_status="open", dt_version="vOrig"))
            s.commit()

        ticket = repo.find_by_ticket_id("tkt-snap-001")
        assert ticket is not None
        ticket.close(close_reason="auto_silenced_normal", closed_at=datetime.now())
        # 故意改模型快照(模拟不该发生的事),验证 save_ticket 不会落库它
        ticket.refresh_snapshot(dt_version="vTampered")
        repo.save_ticket(ticket)

        reloaded = repo.find_by_ticket_id("tkt-snap-001")
        assert reloaded.governance_status == GovernanceStatus.CLOSED
        assert reloaded.dt_version == "vOrig"  # 快照未被 save_ticket 改动

    def test_save_ticket_with_snapshot_writes_snapshot(self, repo, engine):
        """_save_ticket_with_snapshot 用 to_orm,快照字段会落库(refresh_snapshot 路径)。"""
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-snapsave-001", active_worker="w:ss1",
                               governance_status="open", dt_version="vOrig"))
            s.commit()

        ticket = repo.find_by_ticket_id("tkt-snapsave-001")
        assert ticket is not None
        ticket.refresh_snapshot(dt_version="vRefreshed", current_decision="normal")
        assert repo._save_ticket_with_snapshot(ticket) is True  # noqa: SLF001

        reloaded = repo.find_by_ticket_id("tkt-snapsave-001")
        assert reloaded.dt_version == "vRefreshed"
        assert reloaded.current_decision == "normal"
        assert reloaded.governance_status == GovernanceStatus.OPEN  # 状态不变


class TestFindLatestClosedByWorker:
    """find_latest_closed_by_worker(worker_id) → GovernanceTicket | None"""

    def test_returns_most_recently_closed(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-c1", worker_id="w:cls1",
                active_worker=None,
                governance_status="closed", closed_at=now - timedelta(hours=1),
                close_reason="user_close",
            ))
            s.add(_make_ticket(
                ticket_id="tkt-c2", worker_id="w:cls1",
                active_worker=None,
                governance_status="closed", closed_at=now,
                close_reason="whitelist_close",
            ))
            s.commit()

        result = repo.find_latest_closed_by_worker("w:cls1")
        assert result is not None
        # Domain model: close_reason exposed via GovernanceTicket
        assert result.close_reason == "whitelist_close"

    def test_no_closed(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(worker_id="w:cls2", active_worker="w:cls2"))
            s.commit()

        assert repo.find_latest_closed_by_worker("w:cls2") is None


class TestFindLatestTicketsByWorkerKeys:
    """find_latest_tickets_by_worker_keys(worker_keys) → dict[worker, latest ticket]"""

    def test_picks_most_recent_per_worker(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            # w:wl1 有两条(含 closed),应取 gmt_create 最新那条
            s.add(_make_ticket(
                ticket_id="tkt-wl1-old", worker_id="w:wl1",
                active_worker=None, governance_status="closed",
                close_reason="user_close", gmt_create=now - timedelta(days=2),
            ))
            s.add(_make_ticket(
                ticket_id="tkt-wl1-new", worker_id="w:wl1",
                active_worker="w:wl1", governance_status="open",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-wl2", worker_id="w:wl2",
                active_worker="w:wl2", governance_status="open",
                gmt_create=now - timedelta(hours=1),
            ))
            s.commit()

        result = repo.find_latest_tickets_by_worker_keys(["w:wl1", "w:wl2"])
        assert set(result.keys()) == {"w:wl1", "w:wl2"}
        assert result["w:wl1"].ticket_id == "tkt-wl1-new"  # gmt_create DESC 取最新
        assert result["w:wl2"].ticket_id == "tkt-wl2"

    def test_empty_worker_keys(self, repo):
        assert repo.find_latest_tickets_by_worker_keys([]) == {}

    def test_worker_without_ticket_absent(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-x", worker_id="w:x",
                active_worker="w:x", gmt_create=now,
            ))
            s.commit()

        result = repo.find_latest_tickets_by_worker_keys(["w:x", "w:nobody"])
        assert "w:x" in result
        assert "w:nobody" not in result  # 无工单的 worker 不在 dict


class TestListRecentTicketsByWorker:
    """list_recent_tickets_by_worker(...) → list[GovernanceTicket] (gmt_create DESC, 全状态)"""

    def test_by_worker_id_returns_ordered(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            # 历史:closed 行(active_worker=None,UK active_worker 不约束 NULL);
            # 至多一条 active(open)。三张同 worker 倒序溯源。
            s.add(_make_ticket(
                ticket_id="tkt-h1-old", worker_id="w:hw",
                active_worker=None, governance_status="closed",
                closed_at=now - timedelta(days=2),
                gmt_create=now - timedelta(days=2),
            ))
            s.add(_make_ticket(
                ticket_id="tkt-h1-mid", worker_id="w:hw",
                active_worker=None, governance_status="closed",
                closed_at=now - timedelta(days=1),
                gmt_create=now - timedelta(days=1),
            ))
            s.add(_make_ticket(
                ticket_id="tkt-h1-new", worker_id="w:hw",
                active_worker="w:hw", governance_status="open",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-other", worker_id="w:other",
                active_worker="w:other", gmt_create=now,
            ))
            s.commit()

        result = repo.list_recent_tickets_by_worker(worker_id="w:hw")
        assert [t.ticket_id for t in result] == [
            "tkt-h1-new", "tkt-h1-mid", "tkt-h1-old",
        ]

    def test_limit_caps_result(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            # 全是 closed(active_worker=None),避免 UK active_worker 冲突。
            for i in range(5):
                s.add(_make_ticket(
                    ticket_id=f"tkt-l{i}", worker_id="w:lim",
                    active_worker=None, governance_status="closed",
                    closed_at=now - timedelta(days=i),
                    gmt_create=now - timedelta(days=i),
                ))
            s.commit()

        result = repo.list_recent_tickets_by_worker(worker_id="w:lim", limit=2)
        assert [t.ticket_id for t in result] == ["tkt-l0", "tkt-l1"]

    def test_by_owner_only(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-oa-b1", owner_id="o:a", bot_id="b:1",
                worker_id="o:a:b:1", active_worker="o:a:b:1",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-oa-b2", owner_id="o:a", bot_id="b:2",
                worker_id="o:a:b:2", active_worker="o:a:b:2",
                gmt_create=now - timedelta(hours=1),
            ))
            s.add(_make_ticket(
                ticket_id="tkt-ob-b1", owner_id="o:b", bot_id="b:1",
                worker_id="o:b:b:1", active_worker="o:b:b:1",
                gmt_create=now,
            ))
            s.commit()

        result = repo.list_recent_tickets_by_worker(owner_id="o:a")
        assert {t.ticket_id for t in result} == {"tkt-oa-b1", "tkt-oa-b2"}

    def test_by_bot_only(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-oa-b1", owner_id="o:a", bot_id="b:1",
                worker_id="o:a:b:1", active_worker="o:a:b:1",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-ob-b1", owner_id="o:b", bot_id="b:1",
                worker_id="o:b:b:1", active_worker="o:b:b:1",
                gmt_create=now - timedelta(hours=1),
            ))
            s.add(_make_ticket(
                ticket_id="tkt-oa-b2", owner_id="o:a", bot_id="b:2",
                worker_id="o:a:b:2", active_worker="o:a:b:2",
                gmt_create=now,
            ))
            s.commit()

        result = repo.list_recent_tickets_by_worker(bot_id="b:1")
        assert {t.ticket_id for t in result} == {"tkt-oa-b1", "tkt-ob-b1"}

    def test_owner_and_bot_is_and(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-ab-hit", owner_id="o:a", bot_id="b:1",
                worker_id="o:a:b:1", active_worker="o:a:b:1",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-ab-miss-owner", owner_id="o:b", bot_id="b:1",
                worker_id="o:b:b:1", active_worker="o:b:b:1",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-ab-miss-bot", owner_id="o:a", bot_id="b:2",
                worker_id="o:a:b:2", active_worker="o:a:b:2",
                gmt_create=now,
            ))
            s.commit()

        result = repo.list_recent_tickets_by_worker(owner_id="o:a", bot_id="b:1")
        assert [t.ticket_id for t in result] == ["tkt-ab-hit"]

    def test_all_params_none_returns_empty(self, repo):
        # 防全表扫兜底:service 层已拦 400,repo 双保险返 []。
        assert repo.list_recent_tickets_by_worker() == []

    def test_does_not_filter_by_status(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-st-open", worker_id="w:st",
                active_worker="w:st", governance_status="open",
                gmt_create=now,
            ))
            s.add(_make_ticket(
                ticket_id="tkt-st-closed", worker_id="w:st",
                active_worker=None, governance_status="closed",
                gmt_create=now - timedelta(days=1),
            ))
            s.add(_make_ticket(
                ticket_id="tkt-st-observed", worker_id="w:st",
                active_worker=None, governance_status="observed",
                gmt_create=now - timedelta(days=2),
            ))
            s.commit()

        result = repo.list_recent_tickets_by_worker(worker_id="w:st", limit=10)
        assert {t.ticket_id for t in result} == {
            "tkt-st-open", "tkt-st-closed", "tkt-st-observed",
        }


class TestListScheduledDue:
    """list_scheduled_due(now) — tickets with mute_until <= now."""

    def test_returns_due_tickets(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:sch1",
                governance_status="scheduled",
                mute_until=now - timedelta(hours=1),
            ))
            s.commit()

        result = repo.list_scheduled_due(now)
        assert len(result) == 1
        assert result[0].governance_status == "scheduled"

    def test_skips_not_yet_due(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:sch2",
                governance_status="scheduled",
                mute_until=now + timedelta(hours=1),
            ))
            s.commit()

        assert repo.list_scheduled_due(now) == []


class TestListAutoSilenceEligible:
    """list_auto_silence_eligible(min_consecutive_days)."""

    def test_returns_eligible(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:as1",
                governance_status="open",
                latest_decision="normal",
                consecutive_normal_days=10,
            ))
            s.commit()

        result = repo.list_auto_silence_eligible(min_consecutive_days=7)
        assert len(result) == 1

    def test_skips_insufficient_days(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:as2",
                governance_status="open",
                latest_decision="normal",
                consecutive_normal_days=3,
            ))
            s.commit()

        assert repo.list_auto_silence_eligible(min_consecutive_days=7) == []


class TestListRemindableTickets:
    """list_remindable_tickets(now) — open + actionable + remind_at <= now."""

    def test_returns_remindable(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:rem1",
                governance_status="open",
                latest_decision="actionable",
                remind_at=now - timedelta(hours=1),
            ))
            s.commit()

        result = repo.list_remindable_tickets(now)
        assert len(result) == 1

    def test_skips_without_remind_at(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:rem2",
                governance_status="open",
                latest_decision="actionable",
                remind_at=None,
            ))
            s.commit()

        assert repo.list_remindable_tickets(now) == []


class TestListTicketsByOwnerAndStatuses:
    """list_tickets_by_owner_and_statuses(owner_id, statuses, …)."""

    def test_filters_by_owner_and_status(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                owner_id="o1", governance_status="open", active_worker="w:o1a",
            ))
            s.add(_make_ticket(
                owner_id="o1", governance_status="closed", active_worker=None,
            ))
            s.add(_make_ticket(
                owner_id="o2", governance_status="open", active_worker="w:o2",
            ))
            s.commit()

        result = repo.list_tickets_by_owner_and_statuses("o1", ["open"])
        assert len(result) == 1
        assert result[0].owner_id == "o1"


class TestListTicketsByStatuses:
    """list_tickets_by_statuses / count_tickets_by_statuses (跨 owner, 评审场景)."""

    def test_cross_owner_filter_and_paging(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(owner_id="o1", governance_status="open"))
            s.add(_make_ticket(owner_id="o2", governance_status="scheduled"))
            s.add(_make_ticket(owner_id="o3", governance_status="waiting_review"))
            s.add(_make_ticket(owner_id="o4", governance_status="closed"))
            s.add(_make_ticket(owner_id="o5", governance_status="open"))
            s.commit()

        # 过滤 open(scheduled 被排除)→ 跨 o1/o5 两名 owner
        result = repo.list_tickets_by_statuses(["open"], limit=50)
        assert {t.owner_id for t in result} == {"o1", "o5"}
        # 返回领域模型(非 ORM / dict)
        assert all(type(t).__name__ == "GovernanceTicket" for t in result)

        # 多状态过滤: open×2 + scheduled×1 + waiting_review×1 = 4
        active = repo.list_tickets_by_statuses(
            ["open", "scheduled", "waiting_review"], limit=50,
        )
        assert len(active) == 4
        assert {t.owner_id for t in active} == {"o1", "o2", "o3", "o5"}
        # gmt_create 经 from_orm 正确灌入(Task 1 链路验证)
        assert all(t.gmt_create is not None for t in active)

    def test_count_matches_list(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(owner_id="o1", governance_status="open"))
            s.add(_make_ticket(owner_id="o2", governance_status="open"))
            s.add(_make_ticket(owner_id="o3", governance_status="closed"))
            s.commit()

        assert repo.count_tickets_by_statuses(["open"]) == 2
        assert repo.count_tickets_by_statuses(["closed"]) == 1
        assert repo.count_tickets_by_statuses(["open", "closed"]) == 3

    def test_ordering_newest_first(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        t_old = datetime(2026, 7, 1, 9, 0, 0)
        t_new = datetime(2026, 7, 9, 9, 0, 0)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="t-old", governance_status="open", gmt_create=t_old,
            ))
            s.add(_make_ticket(
                ticket_id="t-new", governance_status="open", gmt_create=t_new,
            ))
            s.commit()

        result = repo.list_tickets_by_statuses(["open"], limit=50)
        # newest first → t-new 在前;同时验证 gmt_create 透传
        assert result[0].ticket_id == "t-new"
        assert result[0].gmt_create == t_new
        assert result[1].ticket_id == "t-old"

    def test_empty_statuses_returns_empty_safe(self, repo):
        assert repo.list_tickets_by_statuses([]) == []
        assert repo.count_tickets_by_statuses([]) == 0


class TestFindTicketByNotificationId:
    """find_ticket_by_notification_id(notification_id)."""

    def test_found_via_notify_log(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-abc", active_worker="w:nfy1"))
            s.commit()
        with Session() as s:
            s.add(_make_notify(
                notification_id="notif-001",
                ticket_id="tkt-abc",
            ))
            s.commit()

        result = repo.find_ticket_by_notification_id("notif-001")
        assert result is not None
        assert result.ticket_id == "tkt-abc"

    def test_not_found_notification(self, repo):
        assert repo.find_ticket_by_notification_id("nonexistent") is None

    def test_notification_without_ticket_id(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_notify(
                notification_id="notif-002",
                ticket_id=None,
            ))
            s.commit()

        assert repo.find_ticket_by_notification_id("notif-002") is None


class TestInsertTicket:
    """insert_ticket(row) — self-managed session insert."""

    def test_inserts_and_flushes(self, repo, engine):
        row = _make_ticket(ticket_id="tkt-ins", active_worker="w:ins1")
        repo.insert_ticket(row)

        result = repo.find_by_ticket_id("tkt-ins")
        assert result is not None
        assert result.ticket_id == "tkt-ins"


_TASK_ENV_PATCH = (
    "agentclaw.community.core.repository.implementations.governance."
    "task_record.get_current_env"
)


class TestDeleteByTicketId:
    """delete_by_ticket_id(ticket_id) -> int — ticket-cascade 数据层。

    env-scoped 按 ticket_id 精确删单条工单;返回删除数(0/1);
    不同 env 同 ticket_id 不交叉;空结果返回 0。
    """

    def test_delete_removes_ticket_and_returns_one(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-del-001"))
            s.commit()

        with patch(_TASK_ENV_PATCH, return_value="dev"):
            assert repo.delete_by_ticket_id("tkt-del-001") == 1
            assert repo.find_by_ticket_id("tkt-del-001") is None

    def test_delete_zero_when_no_match(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-del-002"))
            s.commit()

        with patch(_TASK_ENV_PATCH, return_value="dev"):
            assert repo.delete_by_ticket_id("no-such-ticket") == 0
            # 无关工单不受影响
            assert repo.find_by_ticket_id("tkt-del-002") is not None

    def test_delete_is_env_scoped(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-del-003"))  # 默认 dev
            s.commit()

        # 删 "pre" env → 命中 0 行(dev 行不受影响)
        with patch(_TASK_ENV_PATCH, return_value="pre"):
            assert repo.delete_by_ticket_id("tkt-del-003") == 0
        with patch(_TASK_ENV_PATCH, return_value="dev"):
            assert repo.find_by_ticket_id("tkt-del-003") is not None
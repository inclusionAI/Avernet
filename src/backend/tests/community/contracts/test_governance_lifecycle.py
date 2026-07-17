"""Contract test — GovernanceLifecycleService 治理工单状态机收口(方案 A)。

端到端验证:三入口(offline-batch / cron / review)经驱动服务推进工单
主状态机(open / scheduled / waiting_review / closed),落库正确;非法转移
被领域守卫拒绝(IllegalTicketTransitionError → 驱动捕获转审计 + False),
不致 HTTP 500、不落库。

Inline in-memory SQLite(循 test_governance_repo_protocols.py:207-210 的
``Base.metadata.create_all`` 先例 + test_feedback.py 的 _build_svc 先例构造
三入口 service)。Import governance ORM 注册表 + 真 repo;从 governance
conftest import ``FakeDB`` / ``FakeGovernanceConfig`` 类(非 fixture)——
``tests/community/contracts/`` 拿不到 governance conftest 的 engine/tables/session
fixture(非子目录作用域),故必须 inline 建表。不依赖 ``world``/``community_world``
(governance 约定刻意不依赖)。

``GovernanceLifecycleServiceProtocol`` 是 service Protocol(落 api/),非
Plugin仓储 Protocol,不在 ``test_protocol_contracts.py`` 扫描范围 —— 契约靠
本测试 + 双 grep 守卫(test_governance_state_machine_guards.py)。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from agentclaw.community.core.economy.governance.domain.enums import (
    CloseReason,
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    IllegalTicketTransitionError,
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.orm import (
    Base,  # noqa: F401 — import 注册表
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.economy.governance.conftest import (  # type: ignore[attr-defined]
    FakeDB,
)


# ---------------------------------------------------------------------------
# Scaffolding — single inline engine + real repos + driver
# ---------------------------------------------------------------------------


def _build_driver():
    """Build the driver + repos backed by inline in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    task_repo = TaskRecordRepository(db=db)
    notify_repo = NotifyLogRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    driver = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    return driver, db, engine


def _seed_ticket(db, *, ticket_id, status="open", worker="o1:b1"):
    """Insert a task_record row directly via ORM."""
    from contextlib import contextmanager

    @contextmanager
    def _sess():
        sf = db._sf  # noqa: SLF001
        s = sf()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    owner, bot = worker.split(":", 1)
    with _sess() as s:
        s.add(GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker,
            active_worker=worker if status != "closed" else None,
            bot_id=bot,
            owner_id=owner,
            bot_name="Bot",
            dt_version="20260711",
            governance_decision="actionable",
            governance_status=status,
            latest_decision="actionable",
            last_sync_at=datetime.now(),
            remind_count=0,
        ))


def _make_ticket_model(ticket_id="T-NEW") -> GovernanceTicket:
    return GovernanceTicket.create(
        ticket_id=ticket_id,
        worker_id="o2:b2",
        bot_id="b2",
        owner_id="o2",
        owner_name=None,
        bot_name="Bot2",
        snapshot=MutableSnapshot(
            dt_version="20260711",
            initial_decision="actionable",
            current_decision="actionable",
            triggered_dimensions="cost",
            hit_dimensions_count=1,
            severity="P1",
            estimated_saving_tokens=5000,
            saving_ratio=0.5,
            task_summary="high cost",
            notification_structured="{}",
            analysis_status="done",
            consecutive_normal_days=0,
            last_decision_dt_version=None,
            last_seen_at=None,
            last_sync_at=datetime(2026, 7, 11),
        ),
    )


# ---------------------------------------------------------------------------
# Contract: 每条合法转移端到端落库正确(经驱动服务)
# ---------------------------------------------------------------------------


class TestLegalTransitionsEndToEnd:
    """三入口 → 驱动服务 → 领域守卫 → save_ticket → task_record 状态对。"""

    def test_open_ticket_persists_open(self):
        driver, db, _ = _build_driver()
        ticket = _make_ticket_model("T-open-1")
        assert driver.open_ticket(ticket=ticket) == "T-open-1"
        row = driver._task_repo.find_by_ticket_id("T-open-1")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.OPEN

    def test_schedule_due_scheduled_to_waiting_review(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-sd", status="scheduled")
        assert driver.transition_schedule_due("T-sd", now=datetime.now()) is True
        row = driver._task_repo.find_by_ticket_id("T-sd")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.WAITING_REVIEW
        assert row.review_reason == "schedule_due"
        assert row.remind_at is None

    def test_auto_silence_open_to_closed(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-as", status="open")
        assert driver.auto_silence_close("T-as", now=datetime.now()) is True
        row = driver._task_repo.find_by_ticket_id("T-as")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.CLOSED
        assert row.close_reason == CloseReason.AUTO_SILENCED_NORMAL

    def test_close_for_whitelist_hit_open_to_closed(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-wl", status="open")
        assert driver.close_for_whitelist_hit("T-wl", now=datetime.now()) is True
        row = driver._task_repo.find_by_ticket_id("T-wl")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.CLOSED
        assert row.close_reason == CloseReason.WHITELIST_FILTERED

    def test_accept_feedback_open_to_waiting_review(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-fb", status="open")
        assert driver.accept_feedback(
            "T-fb",
            user_feedback="optimized",
            feedback_at=datetime.now(),
            feedback_source="http_api",
            target_status=GovernanceStatus.WAITING_REVIEW,
            review_reason="user_optimized",
            actor_id="o1",
        ) is True
        row = driver._task_repo.find_by_ticket_id("T-fb")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.WAITING_REVIEW
        assert row.user_feedback == "optimized"

    def test_pause_open_to_waiting_review(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-p", status="open")
        assert driver.pause_ticket("T-p", review_reason="admin_paused") is True
        row = driver._task_repo.find_by_ticket_id("T-p")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.WAITING_REVIEW

    def test_review_approve_close_to_closed(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-rv", status="waiting_review")
        assert driver.review_ticket(
            "T-rv", review_decision="approve_close",
            reviewed_by="admin", review_remark="ok",
        ) is True
        row = driver._task_repo.find_by_ticket_id("T-rv")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.CLOSED
        assert row.review_decision == "approve_close"

    def test_admin_close_any_to_closed(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-emg", status="open")
        assert driver.admin_close("T-emg", now=datetime.now()) is True
        row = driver._task_repo.find_by_ticket_id("T-emg")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.CLOSED
        assert row.close_reason == CloseReason.ADMIN_CLOSED


# ---------------------------------------------------------------------------
# Contract: 非法转移被领域守卫拒绝(不落库、驱动返 False)
# ---------------------------------------------------------------------------


class TestIllegalTransitionsRejected:
    """IllegalTicketTransitionError 在驱动服务内捕获 → False,状态不变。"""

    def test_schedule_due_from_closed_returns_false(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-c1", status="closed")
        assert driver.transition_schedule_due("T-c1", now=datetime.now()) is False
        row = driver._task_repo.find_by_ticket_id("T-c1")  # noqa: SLF001
        assert row.governance_status == GovernanceStatus.CLOSED

    def test_close_already_closed_returns_false(self):
        driver, db, _ = _build_driver()
        _seed_ticket(db, ticket_id="T-c2", status="closed")
        assert driver.close_for_whitelist_hit("T-c2", now=datetime.now()) is False
        assert driver.admin_close("T-c2", now=datetime.now()) is False

    def test_not_found_returns_false(self):
        driver, _, _ = _build_driver()
        assert driver.transition_schedule_due("nope", now=datetime.now()) is False
        assert driver.admin_close("nope", now=datetime.now()) is False
        assert driver.close_for_whitelist_hit("nope", now=datetime.now()) is False

    def test_resume_from_closed_illegal_at_model(self):
        """领域模型 resume() 从 CLOSED 是非法转移 → ValueError(IllegalTicketTransitionError)。

        驱动 resume_ticket 捕获转审计 + False(无 caller yet, kept for symmetry)。
        """
        t = _make_ticket_model("T-r")
        # 强制置 closed,断言模型方法本身抛 IllegalTicketTransitionError
        t.transition_to(GovernanceStatus.CLOSED)
        with pytest.raises(IllegalTicketTransitionError):
            t.resume()

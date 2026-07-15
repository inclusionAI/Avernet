"""Unit tests for GovernanceLifecycleService — sole driver of the ticket
main state machine.

Inline in-memory SQLite (precedent: test_governance_repo_protocols.py:207-210,
test_feedback.py:32-45). Imports FakeDB / FakeGovernanceConfig / FakeWhitelistService
classes (not fixtures) from the governance conftest — no world/community_world
dependency, no governance conftest fixture scope (this file is in the same dir
so it CAN import from conftest).
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.core.economy.governance.domain.enums import (
    CloseReason,
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.orm import (
    Base,
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

from .conftest import FakeDB


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


def _build_svc():
    """Build a real driver service backed by in-memory SQLite + real repos."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    task_repo = TaskRecordRepository(db=db)
    notify_repo = NotifyLogRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    return svc, db, engine


def _seed_ticket(db, *, ticket_id="T-100", status="open", remind_at=None,
                 worker="owner-1:bot-1"):
    """Insert a task_record row directly via ORM for test seeding.

    ``worker`` sets both ``worker_id`` and ``active_worker``; the table has a
    UNIQUE(env, active_worker) constraint, so distinct active tickets must
    carry distinct workers.
    """
    from contextlib import contextmanager

    @contextmanager
    def _sess():
        sf = db._sf  # noqa: SLF001 — test access to FakeDB session factory
        s = sf()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    owner_id = worker.split(":", 1)[0]
    bot_id = worker.split(":", 1)[1] if ":" in worker else "bot-1"
    with _sess() as s:
        row = GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker,
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name="Bot1",
            dt_version="20260711",
            governance_decision="actionable",
            latest_decision="actionable",
            governance_status=status,
            active_worker=worker,
            last_sync_at=datetime.now(),  # nullable=False, no default
            remind_at=remind_at,
            remind_count=0,
        )
        s.add(row)


def _make_ticket_model(*, ticket_id="T-NEW") -> GovernanceTicket:
    """Build a fresh GovernanceTicket domain model for open_ticket."""
    return GovernanceTicket.create(
        ticket_id=ticket_id,
        worker_id="owner-2:bot-2",
        bot_id="bot-2",
        owner_id="owner-2",
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
# open_ticket (offline-batch entry)
# ---------------------------------------------------------------------------


class TestOpenTicket:
    def test_open_ticket_persists_open_row(self) -> None:
        svc, db, _ = _build_svc()
        ticket = _make_ticket_model()
        returned_id = svc.open_ticket(ticket=ticket)
        assert returned_id == ticket.ticket_id

        persisted = svc._task_repo.find_by_ticket_id(ticket.ticket_id)  # noqa: SLF001
        assert persisted is not None
        assert persisted.governance_status == GovernanceStatus.OPEN
        assert persisted.bot_id == "bot-2"


# ---------------------------------------------------------------------------
# close_for_whitelist_hit (offline-batch entry)
# ---------------------------------------------------------------------------


class TestCloseForWhitelistHit:
    def test_closes_open_ticket(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-wl", status="open")
        now = datetime.now()
        assert svc.close_for_whitelist_hit("T-wl", now=now) is True
        t = svc._task_repo.find_by_ticket_id("T-wl")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.close_reason == CloseReason.WHITELIST_FILTERED

    def test_not_found_returns_false(self) -> None:
        svc, _, _ = _build_svc()
        assert svc.close_for_whitelist_hit("nope", now=datetime.now()) is False


# ---------------------------------------------------------------------------
# transition_schedule_due / auto_silence_close (cron entry)
# ---------------------------------------------------------------------------


class TestCronTransitions:
    def test_schedule_due_scheduled_to_waiting_review(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-sd", status="scheduled")
        assert svc.transition_schedule_due("T-sd", now=datetime.now()) is True
        t = svc._task_repo.find_by_ticket_id("T-sd")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.review_reason == "schedule_due"
        assert t.remind_at is None  # cleared on leaving scheduled (pause clears)

    def test_auto_silence_close_open_to_closed(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-as", status="open")
        assert svc.auto_silence_close("T-as", now=datetime.now()) is True
        t = svc._task_repo.find_by_ticket_id("T-as")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.close_reason == CloseReason.AUTO_SILENCED_NORMAL

    def test_schedule_due_illegal_from_closed_returns_false(self) -> None:
        """CLOSED → waiting_review not whitelisted; 方案 A 下守卫在驱动服务内
        抛出(save_ticket 未被调用 → 无落库),驱动服务捕获转审计 + False。"""
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-closed", status="closed")
        # 监视 save_ticket 是否被调用 —— 方案 A 要求守卫在 save 前抛出
        save_calls: list = []
        original_save = svc._task_repo.save_ticket  # noqa: SLF001
        def _spy_save(ticket):
            save_calls.append(ticket.ticket_id)
            return original_save(ticket)
        svc._task_repo.save_ticket = _spy_save  # type: ignore[assignment]  # noqa: SLF001
        try:
            assert svc.transition_schedule_due("T-closed", now=datetime.now()) is False
        finally:
            svc._task_repo.save_ticket = original_save  # type: ignore[assignment]  # noqa: SLF001
        assert save_calls == []  # 守卫在 save 前抛出,未落库
        t = svc._task_repo.find_by_ticket_id("T-closed")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.CLOSED  # 状态未变


# ---------------------------------------------------------------------------
# accept_feedback (ticket-review entry) — incl. whitelist side effect
# ---------------------------------------------------------------------------


class TestAcceptFeedback:
    def test_accept_optimized_open_to_waiting_review(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-fb", status="open")
        assert svc.accept_feedback(
            "T-fb",
            user_feedback="optimized",
            feedback_at=datetime.now(),
            feedback_source="http_api",
            target_status=GovernanceStatus.WAITING_REVIEW,
            review_reason="user_optimized",
            actor_id="owner-1",
        ) is True
        t = svc._task_repo.find_by_ticket_id("T-fb")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.user_feedback == "optimized"

    def test_accept_whitelist_transitions_ticket(self) -> None:
        """whitelist feedback → driver transitions ticket to WAITING_REVIEW.

        Note: the whitelist-add side effect is owned by feedback_service
        (not the driver) to keep lifecycle_service free of a whitelist_service
        dependency. This test asserts the transition the driver owns.
        """
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-wlfb", status="open")
        assert svc.accept_feedback(
            "T-wlfb",
            user_feedback="whitelist",
            feedback_at=datetime.now(),
            feedback_source="card_callback",
            target_status=GovernanceStatus.WAITING_REVIEW,
            review_reason="user_whitelisted",
            actor_id="owner-1",
        ) is True
        t = svc._task_repo.find_by_ticket_id("T-wlfb")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW


# ---------------------------------------------------------------------------
# pause / review / emergency_close (ticket-review entry)
# ---------------------------------------------------------------------------


class TestPauseReviewEmergency:
    def test_pause_open_to_waiting_review(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-pause", status="open")
        assert svc.pause_ticket("T-pause", review_reason="admin_paused") is True
        t = svc._task_repo.find_by_ticket_id("T-pause")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.review_reason == "admin_paused"

    def test_review_approve_close(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-rev", status="waiting_review")
        assert svc.review_ticket(
            "T-rev", review_decision="approve_close",
            reviewed_by="admin-1", review_remark="ok",
        ) is True
        t = svc._task_repo.find_by_ticket_id("T-rev")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.review_decision == "approve_close"
        assert t.reviewed_by == "admin-1"

    def test_review_approve_scheduled_to_scheduled(self) -> None:
        """approve_scheduled → SCHEDULED(不关单),close_reason='schedule_approved'。"""
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-sch", status="waiting_review")
        assert svc.review_ticket(
            "T-sch", review_decision="approve_scheduled",
            reviewed_by="admin-1", review_remark="排期 ok",
        ) is True
        t = svc._task_repo.find_by_ticket_id("T-sch")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.SCHEDULED
        assert t.review_decision == "approve_scheduled"
        assert t.close_reason == "schedule_approved"
        assert t.reviewed_by == "admin-1"

    def test_emergency_close_open_to_closed(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-emg", status="open")
        assert svc.emergency_close("T-emg", now=datetime.now()) is True
        t = svc._task_repo.find_by_ticket_id("T-emg")  # noqa: SLF001
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.close_reason == CloseReason.EMERGENCY_CLOSED

    def test_emergency_close_idempotent_on_closed(self) -> None:
        """Already-closed → no-op returns False (idempotent guard in driver)."""
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-emg2", status="closed")
        assert svc.emergency_close("T-emg2", now=datetime.now()) is False

    def test_emergency_close_not_found(self) -> None:
        svc, _, _ = _build_svc()
        assert svc.emergency_close("nope", now=datetime.now()) is False


# ---------------------------------------------------------------------------
# bulk_close_open (joint orchestration — ticket side)
# ---------------------------------------------------------------------------


class TestBulkCloseOpen:
    def test_closes_all_open_and_scheduled(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-b1", status="open", worker="o1:b1")
        _seed_ticket(db, ticket_id="T-b2", status="scheduled", worker="o2:b2")
        _seed_ticket(db, ticket_id="T-b3", status="closed", worker="o3:b3")
        count = svc.bulk_close_open(
            close_reason=CloseReason.EMERGENCY_CLOSED, now=datetime.now(),
        )
        assert count == 2  # open + scheduled; closed excluded by WHERE
        for tid in ("T-b1", "T-b2", "T-b3"):
            t = svc._task_repo.find_by_ticket_id(tid)  # noqa: SLF001
            assert t.governance_status == GovernanceStatus.CLOSED


# ---------------------------------------------------------------------------
# refresh_snapshot / advance_reminder (non-state-transition)
# ---------------------------------------------------------------------------


class TestNonTransitions:
    def test_refresh_snapshot_updates_dt_version(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-rs", status="open")
        assert svc.refresh_snapshot("T-rs", dt_version="20260712") is True
        t = svc._task_repo.find_by_ticket_id("T-rs")  # noqa: SLF001
        assert t.dt_version == "20260712"
        assert t.governance_status == GovernanceStatus.OPEN  # status unchanged

    def test_advance_reminder_sets_remind_at(self) -> None:
        svc, db, _ = _build_svc()
        _seed_ticket(db, ticket_id="T-ar", status="open")
        remind = datetime(2026, 8, 1, 9, 0, 0)
        assert svc.advance_reminder(
            "T-ar", remind_at=remind, is_reminder=True, remind_count_delta=1,
        ) is True
        t = svc._task_repo.find_by_ticket_id("T-ar")  # noqa: SLF001
        assert t.remind_at == remind
        assert t.remind_count == 1


# ---------------------------------------------------------------------------
# Protocol conformance (structural)
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_protocol(self) -> None:
        from agentclaw.community.api.governance_service import (
            GovernanceLifecycleServiceProtocol,
        )
        svc, _, _ = _build_svc()
        # runtime_checkable Protocol — isinstance on the instance.
        assert isinstance(svc, GovernanceLifecycleServiceProtocol)

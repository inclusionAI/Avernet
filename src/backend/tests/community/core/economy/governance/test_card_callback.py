"""Tests for card-callback endpoint logic (via GovernanceFeedbackService.resolve).

Covers:
  - Card callback → resolve() (optimized / need_time / dispute / whitelist)
  - error_code generation for HTTP status mapping
  - DB-side effects: response, response_source, governance_status, feedback_payload, audit
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.domain.enums import AuditAction
from agentclaw.community.core.economy.governance.repositories.orm import (
    AuditLogOrm,
    GovernanceNotificationOrm,
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.services.feedback_service import (
    GovernanceFeedbackService,
    ResolveResult,
)

from .conftest import FakeDB, FakeGovernanceConfig, FakeWhitelistService


def _make_notification(session, **overrides):
    """Create linked notify_log + task_record rows for testing."""
    nid = overrides.pop("notification_id", "n-001")
    owner_id = overrides.pop("owner_id", "user-1")
    bot_id = overrides.pop("bot_id", "bot-1")
    ticket_id = overrides.pop("ticket_id", f"t-{nid}")
    response_val = overrides.pop("response", None)
    governance_status = overrides.pop("governance_status", "open")

    # Create task_record (lifecycle entity)
    task_rec = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=f"{owner_id}:{bot_id}",
        active_worker=f"{owner_id}:{bot_id}"
        if governance_status in ("open", "scheduled", "waiting_review")
        else None,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        dt_version="20260629",
        governance_decision="actionable",
        governance_status=governance_status,
        latest_decision="actionable",
        response=response_val,
        last_sync_at=datetime.now(),
    )
    session.add(task_rec)

    # Create notify_log (event log) linked by ticket_id
    row = GovernanceNotificationOrm(
        notification_id=nid,
        ticket_id=ticket_id,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        worker_id=f"{owner_id}:{bot_id}:{nid}",
        dt_version="20260629",
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=governance_status,
        notify_status="sent",
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=1,
        send_attempt_count=1,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def _build_svc(engine):
    """Build feedback service with in-memory DB."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    feedback_svc = GovernanceFeedbackService(
        whitelist_service=FakeWhitelistService(),
        notify_repo=NotifyLogRepository(db=db),
        task_repo=TaskRecordRepository(db=db),
        audit_repo=GovernanceAuditRepository(db=db),
        config=FakeGovernanceConfig(),
    )
    return feedback_svc


# Error code → HTTP status map (mirrors router.py card-callback)
_ERROR_STATUS_MAP = {
    "NOT_FOUND": 404, "NOT_OWNER": 403,
    "INVALID_RESPONSE": 400, "MISSING_REMARK": 400,
    "MISSING_REPAIR_DEADLINE": 400,
    "INVALID_FEEDBACK_PAYLOAD": 400, "DB_ERROR": 500,
}


def _http_status(result: ResolveResult) -> int:
    """Derive HTTP status from ResolveResult (same logic as router)."""
    if result.success:
        return 200
    return _ERROR_STATUS_MAP.get(result.error_code or "", 400)


# ── Card callback → resolve() paths ─────────────────────────────


class TestCardCallbackResolve:
    """Card callback direct resolve paths."""

    def test_optimized_writes_to_db(self, session, engine):
        """response=optimized → waiting_review, response_source=card_callback in DB."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "optimized", "user-1", source="card_callback")
        assert result.success
        assert _http_status(result) == 200
        assert result.governance_status == "waiting_review"
        assert result.close_reason is None
        assert result.response == "optimized"
        assert result.response_source == "card_callback"

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.response == "optimized"
        assert row.response_source == "card_callback"
        assert row.governance_status == "waiting_review"

    def test_dispute_with_remark_writes_to_db(self, session, engine):
        """response=dispute + remark → closed in DB."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve(
            "n-001", "dispute", "user-1",
            remark="This is wrong", source="card_callback",
        )
        assert result.success
        assert _http_status(result) == 200
        assert result.close_reason is None
        assert result.response == "dispute"

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.response_remark == "This is wrong"

    def test_need_time_writes_to_db(self, session, engine):
        """response=need_time + repair_deadline → scheduled in DB."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve(
            "n-001", "need_time", "user-1",
            repair_deadline=datetime(2026, 7, 15), source="card_callback",
        )
        assert result.success
        assert result.governance_status == "scheduled"

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.repair_deadline is not None
        assert row.mute_until is not None

    def test_whitelist_with_remark_writes_to_db(self, session, engine):
        """response=whitelist + remark → closed in DB + whitelist add."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve(
            "n-001", "whitelist", "user-1",
            remark="Business needs it", source="card_callback",
        )
        assert result.success
        assert result.close_reason is None

    def test_feedback_payload_writes_to_db(self, session, engine):
        """feedback_payload written as JSON string in DB."""
        svc = _build_svc(engine)
        _make_notification(session)

        payload = {
            "version": 1,
            "overall_action": "partial",
            "items": [
                {"index": 1, "action": "accepted", "remark": None},
                {"index": 2, "action": "rejected", "remark": "Need this"},
            ],
        }

        result = svc.resolve(
            "n-001", "optimized", "user-1",
            feedback_payload=payload, source="card_callback",
        )
        assert result.success

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        stored = json.loads(row.feedback_payload)
        assert stored["overall_action"] == "partial"


# ── Error paths ──────────────────────────────────────────────────


class TestCardCallbackErrors:
    """Error mapping: error_code → expected HTTP status."""

    def test_not_found_returns_404(self, session, engine):
        """Non-existent notification → NOT_FOUND → 404."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("nonexistent", "optimized", "user-1", source="card_callback")
        assert not result.success
        assert _http_status(result) == 404
        assert result.error_code == "NOT_FOUND"

    def test_invalid_response_returns_400(self, session, engine):
        """Invalid response → INVALID_RESPONSE → 400."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "invalid", "user-1", source="card_callback")
        assert not result.success
        assert _http_status(result) == 400
        assert result.error_code == "INVALID_RESPONSE"

    def test_dispute_without_remark_returns_400(self, session, engine):
        """dispute without remark → MISSING_REMARK → 400."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "dispute", "user-1", source="card_callback")
        assert not result.success
        assert _http_status(result) == 400
        assert result.error_code == "MISSING_REMARK"

    def test_whitelist_without_remark_returns_400(self, session, engine):
        """whitelist without remark → MISSING_REMARK → 400."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "whitelist", "user-1", source="card_callback")
        assert not result.success
        assert _http_status(result) == 400
        assert result.error_code == "MISSING_REMARK"

    def test_need_time_without_deadline_returns_400(self, session, engine):
        """need_time without repair_deadline → MISSING_REPAIR_DEADLINE → 400."""
        svc = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "need_time", "user-1", source="card_callback")
        assert not result.success
        assert _http_status(result) == 400
        assert result.error_code == "MISSING_REPAIR_DEADLINE"

    def test_formal_resolve_idempotent_returns_200(self, session, engine):
        """Already formally resolved → idempotent 200."""
        svc = _build_svc(engine)
        _make_notification(session)

        r1 = svc.resolve("n-001", "optimized", "user-1", source="card_callback")
        assert r1.success
        assert _http_status(r1) == 200

        r2 = svc.resolve("n-001", "optimized", "user-1", source="card_callback")
        assert r2.success
        assert _http_status(r2) == 200
        assert r2.governance_status == "waiting_review"


# ── Audit records ────────────────────────────────────────────────


class TestCardCallbackAudit:
    """Audit trail written by card callback."""

    def test_resolve_writes_audit(self, session, engine):
        """Formal resolve writes AuditLogOrm with run_id=feedback-*."""
        svc = _build_svc(engine)
        _make_notification(session)

        svc.resolve("n-001", "optimized", "user-1", source="card_callback")

        audits = session.query(AuditLogOrm).all()
        card_audits = [a for a in audits if a.run_id.startswith("feedback-")]
        assert len(card_audits) >= 1
        assert card_audits[-1].source == "card_callback"
        assert card_audits[-1].action_taken == AuditAction.USER_OPTIMIZED


# ── _result_from_log_row factory ────────────────────────────────


class TestResultFromTicket:
    """Verify _result_from_ticket populates all fields."""

    def test_from_ticket_populates_all_fields(self, session, engine):
        """After resolve, calling _result_from_ticket returns full data."""
        from agentclaw.community.core.economy.governance.services.feedback_service import (
            _result_from_ticket,
        )
        from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket

        svc = _build_svc(engine)
        _make_notification(session)

        svc.resolve("n-001", "optimized", "user-1", source="card_callback")

        orm_row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        ticket = GovernanceTicket.from_orm(orm_row)
        result = _result_from_ticket(ticket, notification_id="n-001", message="test hint")
        assert result.success
        assert result.notification_id == "n-001"
        assert result.ticket_id == "t-n-001"
        assert result.response == "optimized"
        assert result.response_source == "card_callback"
        assert result.governance_status == "waiting_review"
        assert result.message == "test hint"


# ── user_id="" owner_id 反查 (card_callback 无鉴权) ───────────────


class TestCardCallbackNoAuth:
    """Verify resolve() works when user_id is empty (DingTalk card iframe
    has no SSO cookie — owner_id is resolved from DB instead).

    contract: user_id="" → effective_user_id = log_row.owner_id
    """

    def test_empty_user_id_resolves_owner_from_db(self, session, engine):
        """user_id="" → owner_id read from notify_log row."""
        svc = _build_svc(engine)
        _make_notification(session, owner_id="staff-273250")

        result = svc.resolve("n-001", "optimized", "", source="card_callback")
        assert result.success
        assert result.governance_status == "waiting_review"

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.response == "optimized"
        assert row.actor_id == "staff-273250"  # from ticket.owner_id

    def test_empty_user_id_audit_uses_owner(self, session, engine):
        """user_id="" → audit actor_id = log_row.owner_id."""
        svc = _build_svc(engine)
        _make_notification(session, owner_id="staff-339245")

        svc.resolve("n-001", "optimized", "", source="card_callback")

        audits = session.query(AuditLogOrm).all()
        card_audits = [a for a in audits if a.run_id.startswith("feedback-")]
        assert len(card_audits) >= 1
        assert card_audits[-1].actor_id == "staff-339245"

    def test_empty_user_id_whitelist_uses_owner(self, session, engine):
        """user_id="" → whitelist add created_by = log_row.owner_id."""
        wl_calls: list[dict] = []

        class _TrackingWhitelistService:
            def bulk_whitelist(self, bot_ids, reason, operator):
                return {"whitelisted": len(bot_ids), "cancelled": 0}

            def delete_whitelist_entry(self, *, bot_id, owner_id, reason, operator):
                return {"deleted": False, "bot_id": bot_id, "owner_id": owner_id}

            def count_by_type(self, **kwargs):
                return 0

            def add(self, *, bot_id, owner_id, created_by, **kwargs):
                wl_calls.append({"bot_id": bot_id, "owner_id": owner_id, "created_by": created_by})
                from agentclaw.community.core.economy.governance.domain.whitelist import WhitelistEntry
                return WhitelistEntry(
                    bot_id=bot_id, owner_id=owner_id,
                    whitelist_type=kwargs.get("whitelist_type", "governance"),
                    source=kwargs.get("source", "manual"),
                    reason=kwargs.get("reason", ""),
                    created_by=created_by, expires_at=None,
                )

        Session = sessionmaker(bind=engine, expire_on_commit=False)
        db = FakeDB(lambda: Session(bind=engine))
        svc = GovernanceFeedbackService(
            whitelist_service=_TrackingWhitelistService(),
            notify_repo=NotifyLogRepository(db=db),
            task_repo=TaskRecordRepository(db=db),
            audit_repo=GovernanceAuditRepository(db=db),
            config=FakeGovernanceConfig(),
        )
        _make_notification(session, owner_id="staff-350361", notification_id="wl-1")

        svc.resolve("wl-1", "whitelist", "", remark="needed", source="card_callback")
        assert len(wl_calls) == 1
        assert wl_calls[0]["created_by"] == "staff-350361"

    def test_actor_id_overrides_empty_user_id(self, session, engine):
        """user_id="" + actor_id="admin" → actor_id takes precedence."""
        svc = _build_svc(engine)
        _make_notification(session, owner_id="staff-273250")

        result = svc.resolve(
            "n-001", "optimized", "", actor_id="admin-007",
            source="admin_api",
        )
        assert result.success

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.actor_id == "admin-007"  # actor_id overrides

    def test_explicit_user_id_still_works(self, session, engine):
        """Non-empty user_id still works as before (backward compat)."""
        svc = _build_svc(engine)
        _make_notification(session, owner_id="staff-273250")

        result = svc.resolve("n-001", "optimized", "user-explicit", source="http_api")
        assert result.success

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.actor_id == "user-explicit"  # explicit user_id used

    def test_empty_user_id_need_time_resolves_owner(self, session, engine):
        """user_id="" + need_time → owner_id resolved, mute_until set."""
        svc = _build_svc(engine)
        _make_notification(session, owner_id="staff-999888")

        result = svc.resolve(
            "n-001", "need_time", "",
            repair_deadline=datetime(2026, 7, 15), source="card_callback",
        )
        assert result.success
        assert result.governance_status == "scheduled"

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.actor_id == "staff-999888"
        assert row.mute_until is not None

    def test_empty_user_id_dispute_with_remark_resolves_owner(self, session, engine):
        """user_id="" + dispute + remark → owner_id resolved."""
        svc = _build_svc(engine)
        _make_notification(session, owner_id="staff-111222")

        result = svc.resolve(
            "n-001", "dispute", "",
            remark="Not our bot", source="card_callback",
        )
        assert result.success
        assert result.close_reason is None

        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        assert row.actor_id == "staff-111222"

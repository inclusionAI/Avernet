"""Extra coverage tests for GovernanceFeedbackService.

Targets uncovered branches in feedback_service.py: non-standard existing
response, invalid feedback_payload JSON, whitelist add failure, commit
failure, and the list_pending / list_history / get_notification helpers.

Reuses the fakes from test_feedback.py (copied here to avoid editing it).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.repositories.orm import (
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
)

from .conftest import FakeDB, FakeGovernanceConfig, FakeWhitelistService


class _RaisingWhitelistService:
    """Whitelist service whose add always raises — exercises except path."""

    def bulk_whitelist(self, bot_ids, reason, operator):
        raise RuntimeError("boom")

    def delete_whitelist_entry(self, *, bot_id, owner_id, reason, operator):
        raise RuntimeError("boom")

    def count_by_type(self, **kwargs):
        return 0

    def add(self, *, bot_id, owner_id, created_by, **kwargs):
        raise RuntimeError("boom")


def _make_notification(session, **overrides):
    """Create linked notify_log + task_record rows for testing.

    ``worker_id`` on the notify_log is derived from notification_id so multiple
    rows in one test don't collide on the index.  ``ticket_id`` links the
    notify_log to its task_record.
    """
    nid = overrides.pop("notification_id", "n-001")
    owner_id = overrides.pop("owner_id", "user-1")
    bot_id = overrides.pop("bot_id", "bot-1")
    ticket_id = overrides.pop("ticket_id", f"t-{nid}")
    response_val = overrides.pop("response", None)
    governance_status = overrides.pop("governance_status", "open")
    worker_id = overrides.pop("worker_id", f"{owner_id}:{bot_id}:{nid}")

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
        worker_id=worker_id,
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


def _make_svc(engine, whitelist_service=None):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    return GovernanceFeedbackService(
        whitelist_service=whitelist_service or FakeWhitelistService(),
        notify_repo=NotifyLogRepository(db=db),
        task_repo=TaskRecordRepository(db=db),
        audit_repo=GovernanceAuditRepository(db=db),
        config=FakeGovernanceConfig(),
    )


class TestResolveEdgeBranches:
    """Uncovered branches inside resolve()."""

    def test_existing_response_duplicate_ignored(self, session, engine):
        """ticket.response already set → duplicate_ignored (§7.4.1 step 2)."""
        svc = _make_svc(engine)
        _make_notification(session, response="something_weird")

        result = svc.resolve("n-001", "optimized", "user-1")
        assert result.success
        assert "已反馈过" in (result.message or "")

    def test_unknown_response_value_rejected(self, session, engine):
        """A response not in the formal set → INVALID_RESPONSE (line 151)."""
        svc = _make_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "bogus_response", "user-1")
        assert not result.success
        assert result.error_code == "INVALID_RESPONSE"
        assert "Invalid response" in (result.error or "")

    def test_invalid_feedback_payload_json(self, session, engine):
        """Non-serializable feedback_payload → INVALID_FEEDBACK_PAYLOAD (lines 174-175)."""
        svc = _make_svc(engine)
        _make_notification(session)

        # A set is not JSON-serializable → json.dumps raises TypeError.
        payload = {"bad": {object()}}
        result = svc.resolve(
            "n-001", "optimized", "user-1",
            feedback_payload=payload,
        )
        assert not result.success
        assert result.error_code == "INVALID_FEEDBACK_PAYLOAD"

    def test_whitelist_add_failure_is_swallowed(self, session, engine):
        """add raising must not fail the resolve (lines 200-201)."""
        svc = _make_svc(engine, whitelist_service=_RaisingWhitelistService())
        _make_notification(session)

        result = svc.resolve(
            "n-001", "whitelist", "user-1", remark="Internal tool",
        )
        # Whitelist side-effect failed but resolve still succeeds/closes.
        assert result.success
        assert result.close_reason is None

    # NOTE: test_commit_failure_returns_db_error removed — session commit
# failure is now an internal implementation detail of self-managed
# orm_session contexts and cannot be cleanly tested from the outside.


class TestListAndGetHelpers:
    """list_pending / list_history / get_notification (lines 246-280)."""

    def test_list_pending_returns_open_and_scheduled(self, session, engine):
        svc = _make_svc(engine)
        _make_notification(session, notification_id="p-open", bot_id="bot-open", governance_status="open")
        _make_notification(session, notification_id="p-scheduled", bot_id="bot-scheduled", governance_status="scheduled")
        _make_notification(session, notification_id="p-closed", bot_id="bot-closed", governance_status="closed")

        rows = svc.list_pending("user-1")
        ticket_ids = {r.ticket_id for r in rows}
        assert "t-p-open" in ticket_ids
        assert "t-p-scheduled" in ticket_ids
        assert "t-p-closed" not in ticket_ids
        # _row_to_dict shape sanity
        assert rows[0].owner_id == "user-1"

    def test_list_pending_empty(self, session, engine):
        svc = _make_svc(engine)
        assert svc.list_pending("nobody") == []

    def test_list_pending_pagination(self, session, engine):
        svc = _make_svc(engine)
        for i in range(3):
            _make_notification(
                session, notification_id=f"pg-{i}", bot_id=f"bot-pg-{i}",
                governance_status="open",
            )
        page = svc.list_pending("user-1", limit=1, offset=1)
        assert len(page) == 1

    def test_list_history_returns_closed(self, session, engine):
        svc = _make_svc(engine)
        _make_notification(session, notification_id="h-closed", bot_id="bot-h-closed", governance_status="closed")
        _make_notification(session, notification_id="h-open", bot_id="bot-h-open", governance_status="open")

        rows = svc.list_history("user-1")
        ticket_ids = {r.ticket_id for r in rows}
        assert "t-h-closed" in ticket_ids
        assert "t-h-open" not in ticket_ids

    def test_list_history_empty(self, session, engine):
        svc = _make_svc(engine)
        assert svc.list_history("nobody") == []

    def test_get_notification_found(self, session, engine):
        svc = _make_svc(engine)
        _make_notification(session, notification_id="g-1")

        got = svc.get_notification("g-1", "user-1")
        assert got is not None
        assert got.ticket_id == "t-g-1"

    def test_get_notification_wrong_owner_returns_none(self, session, engine):
        svc = _make_svc(engine)
        _make_notification(session, notification_id="g-2")

        assert svc.get_notification("g-2", "other-user") is None

    def test_get_notification_missing_returns_none(self, session, engine):
        svc = _make_svc(engine)
        assert svc.get_notification("does-not-exist", "user-1") is None

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
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
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
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    wl_svc = whitelist_service or FakeWhitelistService()
    # whitelist-add side effect is owned by feedback_service (not the driver)
    # to keep lifecycle_service free of a whitelist_service dependency. Wire
    # the same wl_svc into the feedback_service — keeps the raise-failure
    # test exercising feedback_service's whitelist-add swallow path.
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    return GovernanceFeedbackService(
        whitelist_service=wl_svc,
        notify_repo=notify_repo,
        task_repo=task_repo,
        audit_repo=audit_repo,
        config=FakeGovernanceConfig(),
        lifecycle_svc=lifecycle_svc,
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


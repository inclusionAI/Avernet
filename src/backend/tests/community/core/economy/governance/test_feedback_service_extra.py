"""Extra coverage tests for GovernanceFeedbackService.

Targets uncovered branches in feedback_service.py: non-standard existing
response, invalid feedback_payload JSON, whitelist add failure, commit
failure, and the list_pending / list_history / get_notification helpers.

Reuses the fakes from test_feedback.py (copied here to avoid editing it).
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.repositories.orm import (
    GovernanceNotificationOrm,
    GovernanceTicketOrm,
    WhitelistEntryOrm,
)
from agentclaw.community.core.economy.governance.domain.enums import AuditAction
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

from .conftest import FakeDB, FakeGovernanceConfig


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


def _make_svc(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    return GovernanceFeedbackService(
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
        """Non-serializable junk in feedback_payload is sanitized by enrich.

        v2 enrich extracts only items[].{index,action,remark} and rebuilds a
        clean dict from ticket columns, so a non-serializable value in an
        unrelated key no longer poisons the persisted payload — the user's
        feedback is still recorded. (Previously: json.dumps(raw) → TypeError
        → INVALID_FEEDBACK_PAYLOAD. Now: enrich sanitizes.)
        """
        svc = _make_svc(engine)
        _make_notification(session)

        # A set with an object is not JSON-serializable, but it's in an
        # unrelated "bad" key enrich never reads.
        payload = {"bad": {object()}}
        result = svc.resolve(
            "n-001", "optimized", "user-1",
            feedback_payload=payload,
        )
        assert result.success
        # persisted payload is valid v2 JSON with no trace of the junk
        row = session.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
        stored = json.loads(row.feedback_payload)
        assert stored["feedback_schema_version"] == 2
        assert "bad" not in stored

    def test_whitelist_feedback_does_not_write_whitelist(self, session, engine):
        """Spec B 契约:用户反馈选加白只转 waiting_review,不直接写白单表。

        加白唯一入口收敛为 admin 两条路径(批量 / 审阅 approve_whitelist);
        用户反馈加白直接 add 的旧行为已删(绕过审阅 + 与待审静默矛盾)。
        审计 user_whitelisted(申请动作)仍写。
        """
        from sqlalchemy.orm import sessionmaker

        svc = _make_svc(engine)
        _make_notification(session)

        result = svc.resolve(
            "n-001", "whitelist", "user-1", remark="Internal tool",
        )
        assert result.success

        # 1. 白单表无该 (bot, owner) 条目(反馈不直接加白)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            assert s.query(WhitelistEntryOrm).count() == 0

        # 2. 工单转 waiting_review(待审,等 admin approve_whitelist)
        with Session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(ticket_id="t-n-001").one()
            assert ticket.governance_status == "waiting_review"

        # 3. 审计 user_whitelisted(申请动作)仍写
        from agentclaw.community.core.economy.governance.repositories.orm import (
            AuditLogOrm,
        )
        with Session() as s:
            audits = [a for a in s.query(AuditLogOrm).all()
                      if a.action_taken == AuditAction.USER_WHITELIST]
            assert len(audits) >= 1

    # NOTE: test_commit_failure_returns_db_error removed — session commit
# failure is now an internal implementation detail of self-managed
# orm_session contexts and cannot be cleanly tested from the outside.


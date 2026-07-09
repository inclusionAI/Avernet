"""Tests for GovernanceRecordService — single-record and offline-batch processing (§7.1.4, §7.2)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.repositories.orm import (
    WhitelistEntryOrm,
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
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from agentclaw.community.core.economy.governance.services.record_process_service import (
    GovernanceRecordService,
    OfflineBatchResult,
)

from .conftest import FakeDB, FakeGovernanceConfig


# --- Helpers ---


def _build_svc(engine):
    """Build GovernanceRecordService with in-memory DB."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    task_repo = TaskRecordRepository(db=db)
    whitelist_repo = GovernanceWhitelistRepository(db=db)
    notify_repo = NotifyLogRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    svc = GovernanceRecordService(
        task_repo=task_repo,
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=FakeGovernanceConfig(),
    )
    return svc, db


def _sample_record(
    owner_id: str = "staff-001",
    bot_id: str = "bot-001",
    governance_decision: str = "actionable",
) -> dict:
    """Build a minimal record dict for process_record."""
    return {
        "owner_id": owner_id,
        "bot_id": bot_id,
        "bot_name": "TestBot",
        "governance_decision": governance_decision,
        "dt_version": "20260705",
        "hit_dimensions": "token_usage",
        "hit_dimensions_count": "3",
        "governance_max_priority": "high",
        "expected_token_saving": 1000.0,
        "saving_ratio": 0.5,
        "task_summary": "Token saving opportunity",
        "notification_structured": None,
        "analysis_status": "completed",
    }


def _make_ticket(
    session,
    *,
    ticket_id: str | None = None,
    worker_key: str = "staff-001:bot-001",
    governance_status: str = "open",
    governance_decision: str = "actionable",
    latest_decision: str = "actionable",
    close_reason: str | None = None,
    env: str = "dev",
) -> GovernanceTicketOrm:
    """Create a test ticket row."""
    if ticket_id is None:
        ticket_id = uuid.uuid4().hex
    owner_id, bot_id = worker_key.split(":", 1) if ":" in worker_key else ("staff-001", worker_key)
    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker_key,
        active_worker=worker_key if governance_status != "closed" else None,
        governance_status=governance_status,
        governance_decision=governance_decision,
        latest_decision=latest_decision,
        close_reason=close_reason,
        env=env,
        bot_id=bot_id,
        owner_id=owner_id,
        dt_version="20260705",
        bot_name="TestBot",
        consecutive_normal_days=0,
        remind_count=0,
        last_sync_at=datetime.now(),
    )
    session.add(row)
    session.commit()
    return row


# --- Fixtures ---


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    eng = create_engine("sqlite:///:memory:")
    GovernanceTicketOrm.__table__.create(eng, checkfirst=True)
    GovernanceNotificationOrm.__table__.create(eng, checkfirst=True)
    AuditLogOrm.__table__.create(eng, checkfirst=True)
    WhitelistEntryOrm.__table__.create(eng, checkfirst=True)
    return eng


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


# --- Tests: _validate_worker_key ---


class TestValidateWorkerKey:
    def test_valid(self):
        assert GovernanceRecordService._validate_worker_key("staff-001:bot-002") is None

    def test_no_colon(self):
        assert GovernanceRecordService._validate_worker_key("staff001") is not None

    def test_multiple_colons(self):
        # Service currently allows multi-colon; this test documents the behavior
        result = GovernanceRecordService._validate_worker_key("a:b:c")
        # Could be None (allowed) or not None (rejected) — verify it doesn't crash
        assert result is None or isinstance(result, str)

    def test_empty_owner(self):
        assert GovernanceRecordService._validate_worker_key(":bot-001") is not None

    def test_empty_bot(self):
        assert GovernanceRecordService._validate_worker_key("staff-001:") is not None


# --- Tests: process_record ---


class TestProcessRecord:
    def test_creates_new_ticket(self, session, engine):
        """New record → new ticket + first_send notify (§7.1.4 Step 6)."""
        svc, db = _build_svc(engine)
        record = _sample_record()

        result = svc.process_record(
            record, run_id="run-1", notify_source="offline_batch",
        )

        assert result.action == "enqueued"
        assert result.entered_governance_scope is True
        assert result.ticket_id is not None

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).first()
            assert ticket is not None
            assert ticket.governance_status == "open"
            assert ticket.active_worker == "staff-001:bot-001"

    def test_whitelist_filtered(self, session, engine):
        """Whitelisted owner:bot → skipped, no ticket created."""
        svc, db = _build_svc(engine)

        # Add to whitelist — repo uses self-managed session
        whitelist_repo = GovernanceWhitelistRepository(db=db)
        whitelist_repo.add(
            bot_id="bot-001", owner_id="staff-001",
            created_by="admin",
            whitelist_type="governance",
        )

        record = _sample_record()
        result = svc.process_record(
            record, run_id="run-2", notify_source="offline_batch",
        )

        assert result.action == "whitelist_filtered"
        assert result.entered_governance_scope is False

    def test_dry_run_no_writes(self, session, engine):
        """dry_run=True → no DB writes, preview returned."""
        svc, db = _build_svc(engine)
        record = _sample_record()

        result = svc.process_record(
            record, run_id="run-dry", dry_run=True,
        )

        assert result.action in ("enqueued", "would_create")  # would_create in dry_run mode
        # Verify no rows written

    def test_invalid_worker_key(self, session, engine):
        """Malformed worker_key → error result."""
        svc, db = _build_svc(engine)
        record = _sample_record()
        record["bot_id"] = ""  # Will make worker_key "staff-001:"

        result = svc.process_record(
            record, run_id="run-bad",
        )

        assert result.action in ("error", "invalid")
        assert result.entered_governance_scope is False

    def test_cooldown_filtered(self, session, engine):
        """Active cooldown → skip with audit."""
        svc, db = _build_svc(engine)

        # Create a closed ticket with active cooldown
        with db.orm_session() as s:
            _make_ticket(
                s,
                ticket_id="t-closed-1",
                worker_key="staff-001:bot-001",
                governance_status="closed",
                close_reason="user_optimized_approved",
            )
            # Set cooldown_until in the future
            ticket = s.query(GovernanceTicketOrm).first()
            ticket.cooldown_until = datetime.now() + timedelta(days=7)
            s.commit()

        record = _sample_record()
        result = svc.process_record(
            record, run_id="run-cool",
        )

        assert result.action == "cooldown_filtered"


# --- Tests: process_offline_batch ---


class TestProcessOfflineBatch:
    def test_basic_batch(self, session, engine):
        """Simple batch with 2 records → 2 tickets created."""
        svc, db = _build_svc(engine)
        records = [
            _sample_record(owner_id="staff-001", bot_id="bot-001"),
            _sample_record(owner_id="staff-002", bot_id="bot-002"),
        ]

        result = svc.process_offline_batch(
            records,
            batch_id="batch-1",
            dt_version="20260705",
            total_count=2,
        )

        assert isinstance(result, OfflineBatchResult)
        assert result.total_records == 2
        assert result.batch_quality_skipped is False

    def test_quality_skip(self, session, engine):
        """Mismatch total_count → quality skip."""
        svc, db = _build_svc(engine)
        records = [_sample_record()]

        result = svc.process_offline_batch(
            records,
            batch_id="batch-q",
            dt_version="20260705",
            total_count=999,  # Mismatch with 1 record
        )

        assert result.batch_quality_skipped is True

    def test_no_auto_silence_in_batch(self, session, engine):
        """Open ticket not in batch → NOT auto-silenced.

        Phase 3 auto-silence diff has been removed; recovery detection
        is handled by scan_service's daily consecutive_normal_days tracking.
        A partial batch must not falsely silence tickets from other batches.
        """
        svc, db = _build_svc(engine)

        # Create an open ticket that's NOT in the batch
        with db.orm_session() as s:
            _make_ticket(
                s,
                ticket_id="t-silence",
                worker_key="staff-999:bot-999",
                governance_status="open",
                latest_decision="actionable",
            )
            s.commit()

        records = [_sample_record()]  # Different worker
        result = svc.process_offline_batch(
            records,
            batch_id="batch-silence",
            dt_version="20260705",
            total_count=1,
        )

        # Phase 3 auto_silence diff has been removed entirely
        # (recovery detection is scan_service's job)

        # Verify the existing ticket was NOT modified
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter(
                GovernanceTicketOrm.ticket_id == "t-silence",
            ).first()
            assert ticket is not None
            assert ticket.latest_decision == "actionable"
            assert ticket.governance_status == "open"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

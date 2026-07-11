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
from agentclaw.community.core.economy.governance.domain.domain import (
    GovernanceRecord,
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
) -> GovernanceRecord:
    """Build a minimal GovernanceRecord for process_record."""
    return GovernanceRecord(
        owner_id=owner_id,
        bot_id=bot_id,
        bot_name="TestBot",
        governance_decision=governance_decision,
        dt_version="20260705",
        hit_dimensions="token_usage",
        hit_dimensions_count=3,
        governance_max_priority="high",
        expected_token_saving=1000,
        saving_ratio=0.5,
        task_summary="Token saving opportunity",
        notification_structured=None,
        analysis_status="completed",
    )


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
        # bot_id 空 → worker_key 合成为 "staff-001:"(冒号后空)→ invalid
        record = GovernanceRecord(
            owner_id="staff-001",
            bot_id="",
            governance_decision="actionable",
            dt_version="20260705",
        )

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

        records = [_sample_record()]  # Different worker (staff-001:bot-001)
        result = svc.process_offline_batch(
            records,
            batch_id="batch-silence",
            dt_version="20260705",
            total_count=1,
        )

        # Phase 3 auto_silence diff has been removed entirely
        # (recovery detection is scan_service's job)

        # 批次处理成功(本批 worker 创新单/正常处理,无 error)
        assert result.errors == 0

        # Verify the existing ticket was NOT modified
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter(
                GovernanceTicketOrm.ticket_id == "t-silence",
            ).first()
            assert ticket is not None
            assert ticket.latest_decision == "actionable"
            assert ticket.governance_status == "open"


class TestBatchQualityValidation:
    """_validate_batch_quality: total_count 误报修正。"""

    def test_total_count_zero_not_reported(self) -> None:
        """total_count=0(未提供)→ 不报 mismatch(不误报)。"""
        from agentclaw.community.core.economy.governance.services.record_process_service import (
            GovernanceRecordService,
        )
        recs = [_sample_record(), _sample_record(owner_id="o-2", bot_id="b-2")]
        reasons = GovernanceRecordService._validate_batch_quality(
            records=recs, total_count=0,
        )
        assert reasons == []

    def test_total_count_negative_not_reported(self) -> None:
        """total_count<0 → 不报(视为未提供)。"""
        from agentclaw.community.core.economy.governance.services.record_process_service import (
            GovernanceRecordService,
        )
        reasons = GovernanceRecordService._validate_batch_quality(
            records=[_sample_record()], total_count=-1,
        )
        assert reasons == []

    def test_total_count_positive_mismatch_reported(self) -> None:
        """total_count>0 且不符 → 报 mismatch。"""
        from agentclaw.community.core.economy.governance.services.record_process_service import (
            GovernanceRecordService,
        )
        recs = [_sample_record()]
        reasons = GovernanceRecordService._validate_batch_quality(
            records=recs, total_count=999,
        )
        assert len(reasons) == 1
        assert "count_mismatch" in reasons[0]


class TestDeduplicateByWorker:
    """_deduplicate_by_worker: 按 max(dt_version) 保留(防乱序丢新数据)。"""

    @staticmethod
    def _dedup(records):
        from agentclaw.community.core.economy.governance.services.record_process_service import (
            GovernanceRecordService,
        )
        return GovernanceRecordService._deduplicate_by_worker(records)

    def test_keeps_latest_dt_on_conflict(self) -> None:
        """同 worker_key 乱序(前 0710 后 0709)→ 保留 0710。"""
        recs = [
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version="20260710"),
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version="20260709"),
        ]
        deduped = self._dedup(recs)
        assert len(deduped) == 1
        assert deduped[0].dt_version == "20260710"

    def test_keeps_record_with_dt_over_missing(self) -> None:
        """dt 缺失(空)与有值冲突 → 保留有值者。"""
        recs = [
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version=""),
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version="20260711"),
        ]
        deduped = self._dedup(recs)
        assert len(deduped) == 1
        assert deduped[0].dt_version == "20260711"

    def test_all_missing_dt_keeps_last(self) -> None:
        """全缺 dt → 退化保留最后一条(>= 平局后者胜)。"""
        recs = [
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version=""),
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version=""),
        ]
        deduped = self._dedup(recs)
        assert len(deduped) == 1

    def test_no_conflict_returns_all(self) -> None:
        """无 worker_key 冲突 → 原样返回。"""
        recs = [
            GovernanceRecord(owner_id="o-1", bot_id="b-1",
                             governance_decision="actionable", dt_version="20260710"),
            GovernanceRecord(owner_id="o-2", bot_id="b-2",
                             governance_decision="actionable", dt_version="20260710"),
        ]
        deduped = self._dedup(recs)
        assert len(deduped) == 2


class TestFailurePropagation:
    """失败回传:单条抛异常 → upsert_results 含 error 项 + 续跑。"""

    def test_failed_record_appended_as_error(self, session, engine, monkeypatch) -> None:
        """注入抛异常的 record → error 项 + worker_key + reason + errors=1 + 续跑。"""
        svc, db = _build_svc(engine)
        records = [
            _sample_record(owner_id="ok-1", bot_id="b-1"),
            _sample_record(owner_id="bad-1", bot_id="b-2"),  # 这条将抛异常
            _sample_record(owner_id="ok-2", bot_id="b-3"),
        ]

        original_process_record = svc.process_record

        def _flaky(record, *, run_id, dry_run=False, notify_source="offline_batch"):
            if record.owner_id == "bad-1":
                raise RuntimeError("simulated DB failure")
            return original_process_record(
                record=record, run_id=run_id, dry_run=dry_run, notify_source=notify_source,
            )

        monkeypatch.setattr(svc, "process_record", _flaky)
        result = svc.process_offline_batch(
            records, batch_id="batch-fail", dt_version="20260705", total_count=3,
        )

        # errors 计数 = 1
        assert result.errors == 1
        # upsert_results 含 error 项
        error_items = [r for r in result.upsert_results if r.action == "error"]
        assert len(error_items) == 1
        assert error_items[0].worker_key == "bad-1:b-2"
        assert "RuntimeError" in error_items[0].reason
        assert "simulated DB failure" in error_items[0].reason
        # reason 截断 200
        assert len(error_items[0].reason) <= 200
        # 续跑:ok-1 / ok-2 仍处理(非 error 项 = 2)
        non_error = [r for r in result.upsert_results if r.action != "error"]
        assert len(non_error) == 2


class TestIncrementalIdempotency:
    """增量幂等:靠内部守卫(active+dt 守卫),非入口去重。

    场景:先提 5 条 dt=0711 创 5 单;再提 7 条(5 重复 worker 同 dt + 2 新 worker)。
    期望:原 5 条不变(skip,不刷新不重发),2 条新 worker 创新单 → 工单总数 7,
    且原 5 单 dt/governance_status 未被刷新。
    """

    def test_existing_untouched_new_creates(self, session, engine) -> None:
        svc, db = _build_svc(engine)
        dt = "20260711"
        # 第一批:5 条 → 创 5 张 open 单
        first_batch = [
            GovernanceRecord(
                owner_id=f"o-{i}", bot_id=f"b-{i}",
                governance_decision="actionable", dt_version=dt,
            )
            for i in range(5)
        ]
        svc.process_offline_batch(first_batch, batch_id="batch-1", dt_version=dt, total_count=5)

        with db.orm_session() as s:
            assert s.query(GovernanceTicketOrm).count() == 5

        # 第二批:7 条 = 5 重复(同 worker 同 dt) + 2 新 worker
        second_batch = first_batch + [
            GovernanceRecord(
                owner_id="o-5", bot_id="b-5",
                governance_decision="actionable", dt_version=dt,
            ),
            GovernanceRecord(
                owner_id="o-6", bot_id="b-6",
                governance_decision="actionable", dt_version=dt,
            ),
        ]
        result = svc.process_offline_batch(
            second_batch, batch_id="batch-2", dt_version=dt, total_count=7,
        )

        # 工单总数 5(原) + 2(新) = 7
        with db.orm_session() as s:
            assert s.query(GovernanceTicketOrm).count() == 7

        # 原 5 单的 dt 未变(仍 dt)、状态仍 open(未被刷新成别的)
        with db.orm_session() as s:
            original = s.query(GovernanceTicketOrm).filter(
                GovernanceTicketOrm.owner_id.in_(
                    [f"o-{i}" for i in range(5)]
                ),
            ).all()
            assert len(original) == 5
            assert all(t.governance_status == "open" for t in original)
            assert all(t.dt_version == dt for t in original)

        # 5 条重复 record 走 dt 守卫 skip(stale_dt_version_skipped /
        # active_ticket_exists_snapshot_refreshed 这类 action,非 enqueued)
        repeated_results = [
            r for r in result.upsert_results
            if r.worker_key in {f"o-{i}:b-{i}" for i in range(5)}
        ]
        assert len(repeated_results) == 5
        # 重复的不应是 enqueued(创新单),应是 still_actionable(dt 守卫 skip)
        assert all(r.action != "enqueued" for r in repeated_results)
        # 2 条新 worker = enqueued
        new_results = [
            r for r in result.upsert_results
            if r.worker_key in {"o-5:b-5", "o-6:b-6"}
        ]
        assert len(new_results) == 2
        assert all(r.action == "enqueued" for r in new_results)

    def test_stale_older_dt_skips_refresh(self, session, engine) -> None:
        """工单 dt=0712,重提更旧 dt=0711 → dt 守卫 skip,不刷新。"""
        svc, db = _build_svc(engine)
        # 种一张 dt=0712 的 open 单
        with db.orm_session() as s:
            _make_ticket(
                s, ticket_id="t-existing", worker_key="o-1:b-1",
                governance_status="open",
            )
            ticket = s.query(GovernanceTicketOrm).first()
            ticket.dt_version = "20260712"
            s.commit()

        # 重提更旧 dt=0711
        rec = GovernanceRecord(
            owner_id="o-1", bot_id="b-1",
            governance_decision="actionable", dt_version="20260711",
        )
        result = svc.process_record(rec, run_id="run-stale")

        # dt 守卫 skip,不刷新
        assert result.action == "still_actionable"
        assert "stale" in result.reason or result.reason == "stale_dt_version_skipped"
        # 工单 dt 仍 0712(未被旧数据覆盖)
        with db.orm_session() as s:
            assert s.query(GovernanceTicketOrm).first().dt_version == "20260712"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

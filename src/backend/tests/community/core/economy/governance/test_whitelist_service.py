"""End-to-end tests for GovernanceWhitelistService — SQLite-backed.

Exercises ``bulk_whitelist`` and ``delete_whitelist_entry`` through the
real service + real repos backed by in-memory SQLite.  No MagicMock —
all DB operations hit the real ORM layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
    GovernanceStatus,
)
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
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)

from .conftest import FakeDB, FakeGovernanceConfig


# --- Helpers ---


def _db(engine):
    """Build a FakeDB backed by in-memory SQLite."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(lambda: Session(bind=engine))


def _build_svc(engine):
    """Build GovernanceWhitelistService with real repos + SQLite."""
    db = _db(engine)
    whitelist_repo = GovernanceWhitelistRepository(db=db)
    notify_repo = NotifyLogRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    config = FakeGovernanceConfig()
    # whitelist_service needs lifecycle_svc (Task 8: bulk_whitelist closes
    # task_record subjects). lifecycle_service no longer depends on a whitelist
    # service (the accept_feedback whitelist-add is owned by feedback_service),
    # so the construction cycle is gone — build the driver directly.
    task_repo = TaskRecordRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    return GovernanceWhitelistService(
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=config,
        lifecycle_svc=lifecycle_svc,
        task_repo=task_repo,
    ), db


def _make_notification(session, *, notification_id, bot_id, owner_id,
                       governance_status="open", response=None, **overrides):
    """Insert a GovernanceNotificationOrm row for testing."""
    row = GovernanceNotificationOrm(
        notification_id=notification_id,
        bot_id=bot_id,
        bot_name=overrides.pop("bot_name", "TestBot"),
        owner_id=owner_id,
        worker_id=overrides.pop("worker_id", f"{owner_id}:{bot_id}"),
        dt_version=overrides.pop("dt_version", "20260629"),
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=governance_status,
        notify_status=overrides.pop("notify_status", "pending"),
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=1,
        send_attempt_count=1,
        response=response,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def _make_whitelist(session, *, bot_id, owner_id, whitelist_type="governance",
                    source="manual", **overrides):
    """Insert a WhitelistEntryOrm row directly."""
    row = WhitelistEntryOrm(
        bot_id=bot_id,
        owner_id=owner_id,
        whitelist_type=whitelist_type,
        source=source,
        reason=overrides.pop("reason", ""),
        created_by=overrides.pop("created_by", "admin"),
        env=overrides.pop("env", "dev"),
    )
    session.add(row)
    session.commit()
    return row


def _make_ticket(session, *, ticket_id, bot_id, owner_id, governance_status="open"):
    """Insert a GovernanceTicketOrm row linked to a notification via ticket_id."""
    worker = f"{owner_id}:{bot_id}"
    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker,
        active_worker=worker if governance_status != "closed" else None,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        dt_version="20260629",
        governance_decision="actionable",
        governance_status=governance_status,
        latest_decision="actionable",
        last_sync_at=datetime.now(),
    )
    session.add(row)
    session.commit()
    return row


# ── bulk_whitelist ────────────────────────────────────────────────


class TestBulkWhitelist:
    """bulk_whitelist: add to whitelist + close related notifications."""

    def test_whitelists_and_cancels_pending(self, session, engine):
        """Bots with open notifications → whitelisted + cancelled."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )
        _make_notification(
            session, notification_id="n-2",
            bot_id="bot-b", owner_id="owner-b", governance_status="open",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-a", "bot-b"], reason="cleanup", operator="admin",
        )

        assert result["whitelisted"] == 2
        assert result["cancelled"] == 2

        # Verify notifications are closed
        with db.orm_session() as s:
            notif_rows = s.query(GovernanceNotificationOrm).all()
            for n in notif_rows:
                assert n.notify_status == "cancelled"
                assert n.governance_status == "closed"
                assert n.close_reason == "admin_closed"
                assert n.cooldown_until is not None

        # Verify whitelist entries exist
        with db.orm_session() as s:
            wl = s.query(WhitelistEntryOrm).all()
            assert len(wl) == 2
            bot_ids = {r.bot_id for r in wl}
            assert bot_ids == {"bot-a", "bot-b"}

    def test_audit_written(self, session, engine):
        """bulk_whitelist writes per-entity audit rows + a batch summary.

        旧口径写 1 条孤儿审计行(bot_id=None, run_id='admin' 公共桶),按被治理
        实体查不到。新口径:逐 (bot_id, owner_id) 对写带实体的审计行 + 1 条批次
        摘要行,全部共享唯一 run_id(可聚合同一次加白)。摘要行 error_msg 含真实
        处置计数(whitelisted/skipped/cancelled/closed)。
        """
        svc, db = _build_svc(engine)
        audit_repo = GovernanceAuditRepository(db=db)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-x", owner_id="owner-x", governance_status="open",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-x"], reason="test", operator="admin-1",
        )

        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            wl_audits = [a for a in audits if a.action_taken == AuditAction.ADMIN_WHITELIST]
            actor_audits = [a for a in wl_audits if a.actor_id == "admin-1"]
            # Per-entity row carries the governed entity, not an orphan None row.
            entity_rows = [a for a in actor_audits if a.bot_id == "bot-x" and a.owner_id == "owner-x"]
            assert len(entity_rows) == 1, "expected one per-entity audit row for bot-x/owner-x"
            # One batch-summary row (bot_id=None / owner_id=None) carrying real counts.
            summary_rows = [a for a in actor_audits if a.bot_id is None and a.owner_id is None]
            assert len(summary_rows) == 1, "expected one batch-summary audit row"
            summary = summary_rows[0]
            assert "whitelisted=" in (summary.error_msg or "")
            assert f"whitelisted={result['whitelisted']}" in (summary.error_msg or "")
            assert f"cancelled={result['cancelled']}" in (summary.error_msg or "")
            audit_run_ids = {a.run_id for a in actor_audits}
            assert len(audit_run_ids) == 1, "all audit rows of one bulk call share one run_id"
            assert "admin" not in audit_run_ids or next(iter(audit_run_ids)) != "admin", \
                "run_id must be a unique batch id, not the legacy 'admin' public bucket"

        # Closing the loop with Task 1: the audit row must be retrievable by bot
        # (the original symptom — bulk-add audit "查不到" by governed entity).
        rows, total = audit_repo.list_by_subject(bot_id="bot-x")
        assert total >= 1
        assert any(r.bot_id == "bot-x" and r.owner_id == "owner-x" for r in rows)

    def test_no_matching_bots(self, session, engine):
        """No notifications for requested bots → whitelisted=0, cancelled=0."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-known", owner_id="owner-1", governance_status="open",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-unknown"], reason="x", operator="admin",
        )

        assert result["whitelisted"] == 0
        assert result["cancelled"] == 0

    def test_skips_closed_notifications(self, session, engine):
        """Already-closed notifications are not affected."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-open",
            bot_id="bot-x", owner_id="owner-x", governance_status="open",
        )
        _make_notification(
            session, notification_id="n-closed",
            bot_id="bot-x", owner_id="owner-x", governance_status="closed",
            notify_status="sent",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-x"], reason="x", operator="admin",
        )

        # Only the open notification is cancelled
        assert result["cancelled"] == 1

        with db.orm_session() as s:
            rows = {r.notification_id: r for r in s.query(GovernanceNotificationOrm).all()}
            assert rows["n-open"].governance_status == "closed"
            assert rows["n-closed"].governance_status == "closed"  # was already closed

    def test_cooldown_applied(self, session, engine):
        """Each cancelled notification gets cooldown_until = now + cooldown_days."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )

        before = datetime.now()
        result = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        after = datetime.now()

        assert result["cancelled"] == 1
        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            expected_min = before + timedelta(days=14)
            expected_max = after + timedelta(days=14)
            assert expected_min <= row.cooldown_until <= expected_max

    def test_idempotent_whitelist(self, session, engine):
        """Re-whitelisting same bots → skipped in whitelist, still cancels open."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )

        # First call: inserted
        result1 = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        assert result1["whitelisted"] == 1

        # Re-open notification (simulating a new cycle)
        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            row.governance_status = "open"
            row.notify_status = "pending"
            row.close_reason = None

        # Second call: skipped in whitelist, still cancels
        result2 = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        assert result2["whitelisted"] == 0  # skip — already in whitelist
        assert result2["cancelled"] == 1     # still cancels the open notification


# ── Task 8 口径对齐 ──────────────────────────────────────────────


class TestBulkWhitelistTicketAlignment:
    """Task 8: bulk_whitelist 取消通知投递的同时,按 ticket_id 集合关 task_record
    主体(逐条 domain guard、幂等)——修正"只关通知、工单留 open"脱钩。
    bot_id IN (...) 且 response IS NULL 口径精确:已反馈的通知/工单不动。"""

    def test_closes_task_record_subjects_for_unresponded(self, session, engine):
        """unresponded open 通知对应工单 → OBSERVED(whitelist_approved)。

        加白语义:批量加白把活跃单转 OBSERVED(持续观察画像,非 CLOSED)。
        通知侧 close_reason 仍 admin_closed(通知关停原因,独立列)。
        """
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-a", bot_id="bot-a", owner_id="owner-a",
            governance_status="open", ticket_id="t-a",
        )
        _make_ticket(session, ticket_id="t-a", bot_id="bot-a", owner_id="owner-a")

        result = svc.bulk_whitelist(["bot-a"], reason="cleanup", operator="admin")
        assert result["cancelled"] == 1

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).one()
            assert ticket.governance_status == GovernanceStatus.OBSERVED.value
            assert ticket.close_reason == CloseReason.WHITELIST_APPROVED.value
            # 通知侧 close_reason 独立(通知关停原因 ≠ 工单转态原因)
            notify = s.query(GovernanceNotificationOrm).one()
            assert notify.close_reason == CloseReason.ADMIN_CLOSED.value

    def test_preserves_responded_ticket_subject(self, session, engine):
        """已反馈通知(bot 命中但 response 非 None)不在 cancel scope,
        工单不动——按口径精确,不裸用全量关闭。"""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-responded", bot_id="bot-a", owner_id="owner-a",
            governance_status="muted", response="need_time", ticket_id="t-responded",
        )
        _make_ticket(
            session, ticket_id="t-responded", bot_id="bot-a", owner_id="owner-a",
            governance_status="scheduled",
        )

        result = svc.bulk_whitelist(["bot-a"], reason="cleanup", operator="admin")
        assert result["cancelled"] == 0  # 已反馈,不取消

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).one()
            assert ticket.governance_status == "scheduled"  # 精确口径:不动


# ── delete_whitelist_entry ──────────────────────────────────────


class TestDeleteWhitelistEntry:
    """delete_whitelist_entry: single remove by (bot_id, owner_id)."""

    def test_delete_existing(self, session, engine):
        """Delete an existing whitelist entry."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_whitelist(session, bot_id="bot-b", owner_id="user-1")

        result = svc.delete_whitelist_entry(
            bot_id="bot-a", owner_id="user-1",
            reason="cleanup", operator="admin-1",
        )

        assert result["deleted"] is True
        assert result["bot_id"] == "bot-a"
        assert result["owner_id"] == "user-1"

        with db.orm_session() as s:
            remaining = s.query(WhitelistEntryOrm).all()
            assert len(remaining) == 1
            assert remaining[0].bot_id == "bot-b"

    def test_delete_nonexistent(self, session, engine):
        """Deleting a non-existent entry → deleted=False."""
        svc, db = _build_svc(engine)

        result = svc.delete_whitelist_entry(
            bot_id="bot-x", owner_id="user-x",
            reason="test", operator="admin",
        )

        assert result["deleted"] is False

    def test_audit_written_on_delete(self, session, engine):
        """Real delete writes WHITELIST_REMOVED audit."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")

        svc.delete_whitelist_entry(
            bot_id="bot-a", owner_id="user-1",
            reason="cleanup", operator="admin-1",
        )

        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            wl_audits = [a for a in audits if a.action_taken == AuditAction.WHITELIST_REMOVED]
            assert len(wl_audits) >= 1
            assert wl_audits[0].actor_id == "admin-1"

    def test_delete_closes_observed_ticket(self, session, engine):
        """删白收尾:该 worker 现存 OBSERVED 观察单转 CLOSED(终态归档)。

        契约(白名单观察态删白路径):删白 → OBSERVED 单 OBSERVED→CLOSED,
        close_reason=WHITELIST_APPROVED;不设 cooldown;下次 off-batch 走
        正常 Step6 重建新 OPEN 单(由 test_whitelist_remove_restores 覆盖)。
        """
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_ticket(
            session, ticket_id="tkt-obs-1",
            bot_id="bot-a", owner_id="user-1",
            governance_status=GovernanceStatus.OBSERVED.value,
        )

        result = svc.delete_whitelist_entry(
            bot_id="bot-a", owner_id="user-1",
            reason="cleanup", operator="admin-1",
        )

        assert result["deleted"] is True
        assert result["observed_closed"] is True
        with db.orm_session() as s:
            t = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="tkt-obs-1",
            ).one()
            assert t.governance_status == GovernanceStatus.CLOSED.value
            assert t.close_reason == CloseReason.WHITELIST_APPROVED.value
            assert t.closed_at is not None
            assert t.cooldown_until is None  # 删白不设 cooldown

    def test_delete_no_observed_ticket_skips_close(self, session, engine):
        """删白时无 OBSERVED 单 → observed_closed=False(幂等跳过,不报错)。"""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        # 只有一条 closed 历史单(非 OBSERVED)→ 不该被删白收尾碰
        _make_ticket(
            session, ticket_id="tkt-closed-1",
            bot_id="bot-a", owner_id="user-1",
            governance_status=GovernanceStatus.CLOSED.value,
        )

        result = svc.delete_whitelist_entry(
            bot_id="bot-a", owner_id="user-1",
            reason="cleanup", operator="admin-1",
        )

        assert result["deleted"] is True
        assert result["observed_closed"] is False
        with db.orm_session() as s:
            t = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="tkt-closed-1",
            ).one()
            assert t.governance_status == GovernanceStatus.CLOSED.value  # 未被误改


class TestListAllWithTicketMeta:
    """list_all_with_ticket_meta: 白单 + 最近工单维度字段叠加。"""

    @staticmethod
    def _make_ticket_meta(
        session, *, ticket_id, bot_id, owner_id, gmt_create,
        token_baseline=100, expected_token_saving=50, hit_dimensions="ctx",
        saving_ratio=0.5, latest_decision="actionable",
        governance_status="closed",
    ):
        """插一条带治理快照字段的工单(worker_id=owner_id:bot_id)。"""
        worker = f"{owner_id}:{bot_id}"
        row = GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker,
            active_worker=worker if governance_status != "closed" else None,
            bot_id=bot_id,
            bot_name=f"Bot-{bot_id}",
            owner_id=owner_id,
            dt_version="20260629",
            governance_decision="actionable",
            governance_status=governance_status,
            latest_decision=latest_decision,
            hit_dimensions=hit_dimensions,
            expected_token_saving=expected_token_saving,
            saving_ratio=saving_ratio,
            token_baseline=token_baseline,
            last_sync_at=datetime.now(),
            gmt_create=gmt_create,
        )
        session.add(row)
        session.commit()
        return row

    def test_overlays_latest_ticket_fields(self, session, engine):
        """白单有对应工单 → 叠加最近一条工单维度字段。"""
        from agentclaw.community.core.economy.governance.repositories.orm import (
            GovernanceTicketOrm,
        )  # noqa: F401 — already imported above, kept for clarity
        svc, db = _build_svc(engine)
        now = datetime.now()
        _make_whitelist(session, bot_id="bot-a", owner_id="owner-a")
        # 该 worker 两条工单,gmt_create 新的应胜出
        self._make_ticket_meta(
            session, ticket_id="tkt-a-old", bot_id="bot-a", owner_id="owner-a",
            gmt_create=now - timedelta(days=2), token_baseline=80,
            expected_token_saving=20, saving_ratio=0.2,
        )
        self._make_ticket_meta(
            session, ticket_id="tkt-a-new", bot_id="bot-a", owner_id="owner-a",
            gmt_create=now, token_baseline=120,
            expected_token_saving=60, saving_ratio=0.5, hit_dimensions="ctx,mem",
        )

        items, total = svc.list_all_with_ticket_meta(
            whitelist_type="governance",
        )
        assert total == 1
        assert len(items) == 1
        item = items[0]
        # 白单元数据保留
        assert item["bot_id"] == "bot-a"
        assert item["owner_id"] == "owner-a"
        assert item["source"] == "manual"
        # 工单维度叠加 = 最近那条(tkt-a-new)
        assert item["bot_name"] == "Bot-bot-a"
        assert item["token_baseline"] == 120
        assert item["expected_token_saving"] == 60
        assert item["hit_dimensions"] == "ctx,mem"
        assert item["saving_ratio"] == 0.5
        assert item["latest_decision"] == "actionable"
        assert item["latest_ticket_gmt_create"] is not None

    def test_no_ticket_degrades_to_none(self, session, engine):
        """白单无对应工单 → 工单维度字段 None,条目不丢。"""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-b", owner_id="owner-b")

        items, total = svc.list_all_with_ticket_meta(
            whitelist_type="governance",
        )
        assert total == 1
        item = items[0]
        assert item["bot_id"] == "bot-b"
        assert item["source"] == "manual"
        # 无工单 → 工单维度全 None,条目保留
        assert item["bot_name"] is None
        assert item["owner_name"] is None
        assert item["token_baseline"] is None
        assert item["expected_token_saving"] is None
        assert item["hit_dimensions"] is None
        assert item["saving_ratio"] is None
        assert item["latest_decision"] is None
        assert item["latest_ticket_gmt_create"] is None

    def test_no_key_collision_whitelist_vs_ticket(self, session, engine):
        """白单元数据(source/reason)与工单维度字段无命名冲突,各用原名。"""
        svc, db = _build_svc(engine)
        now = datetime.now()
        _make_whitelist(
            session, bot_id="bot-c", owner_id="owner-c",
            source="admin", reason="manual override",
        )
        self._make_ticket_meta(
            session, ticket_id="tkt-c", bot_id="bot-c", owner_id="owner-c",
            gmt_create=now,
        )
        items, _ = svc.list_all_with_ticket_meta(whitelist_type="governance")
        item = items[0]
        # 白单 source/reason 原样保留,不被工单维度覆盖
        assert item["source"] == "admin"
        assert item["reason"] == "manual override"
        # 工单维度叠加键存在
        assert item["bot_name"] == "Bot-bot-c"
        assert item["token_baseline"] == 100
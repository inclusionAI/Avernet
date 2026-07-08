"""End-to-end tests for GovernanceWhitelistService — SQLite-backed.

Exercises ``bulk_whitelist`` and ``delete_whitelist_entries`` through the
real service + real repos backed by in-memory SQLite.  No MagicMock —
all DB operations hit the real ORM layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    BotWhitelist,
    GovernanceAudit,
    GovernanceNotifyLog,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
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
    return GovernanceWhitelistService(
        db=db,
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=config,
    ), db


def _make_notification(session, *, notification_id, bot_id, owner_id,
                       governance_status="open", response=None, **overrides):
    """Insert a GovernanceNotifyLog row for testing."""
    row = GovernanceNotifyLog(
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
    """Insert a BotWhitelist row directly."""
    row = BotWhitelist(
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
            notif_rows = s.query(GovernanceNotifyLog).all()
            for n in notif_rows:
                assert n.notify_status == "cancelled"
                assert n.governance_status == "closed"
                assert n.close_reason == "emergency_closed"
                assert n.cooldown_until is not None

        # Verify whitelist entries exist
        with db.orm_session() as s:
            wl = s.query(BotWhitelist).all()
            assert len(wl) == 2
            bot_ids = {r.bot_id for r in wl}
            assert bot_ids == {"bot-a", "bot-b"}

    def test_audit_written(self, session, engine):
        """bulk_whitelist writes a governance audit entry."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-x", owner_id="owner-x", governance_status="open",
        )

        svc.bulk_whitelist(
            bot_ids=["bot-x"], reason="test", operator="admin-1",
        )

        with db.orm_session() as s:
            audits = s.query(GovernanceAudit).all()
            wl_audits = [a for a in audits if a.action_taken == AuditAction.ADMIN_WHITELIST]
            assert len(wl_audits) >= 1
            assert wl_audits[0].actor_id == "admin-1"

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
            rows = {r.notification_id: r for r in s.query(GovernanceNotifyLog).all()}
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
            row = s.query(GovernanceNotifyLog).one()
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
            row = s.query(GovernanceNotifyLog).one()
            row.governance_status = "open"
            row.notify_status = "pending"
            row.close_reason = None

        # Second call: skipped in whitelist, still cancels
        result2 = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        assert result2["whitelisted"] == 0  # skip — already in whitelist
        assert result2["cancelled"] == 1     # still cancels the open notification


# ── delete_whitelist_entries ──────────────────────────────────────


class TestDeleteWhitelistEntries:
    """delete_whitelist_entries: batch remove by IDs or bot_owner_pairs."""

    def test_dry_run_returns_count_only(self, session, engine):
        """dry_run=true → returns count, doesn't delete."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_whitelist(session, bot_id="bot-b", owner_id="user-1")

        body = {
            "bot_owner_pairs": [{"bot_id": "bot-a", "owner_id": "user-1"}],
            "dry_run": True,
            "reason": "test",
        }
        result = svc.delete_whitelist_entries(body, operator="admin")

        assert result["dry_run"] is True
        assert result["would_delete"] == 1
        assert result["deleted"] == 0

        # Verify rows still exist
        with db.orm_session() as s:
            assert s.query(BotWhitelist).count() == 2

    def test_real_delete_by_bot_owner_pairs(self, session, engine):
        """Real delete by (bot_id, owner_id) pairs."""
        svc, db = _build_svc(engine)
        row_a = _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_whitelist(session, bot_id="bot-b", owner_id="user-1")

        body = {
            "bot_owner_pairs": [{"bot_id": "bot-a", "owner_id": "user-1"}],
            "dry_run": False,
            "reason": "cleanup",
        }
        result = svc.delete_whitelist_entries(body, operator="admin-1")

        assert result["deleted"] == 1
        assert result["dry_run"] is False

        with db.orm_session() as s:
            remaining = s.query(BotWhitelist).all()
            assert len(remaining) == 1
            assert remaining[0].bot_id == "bot-b"

    def test_real_delete_by_ids(self, session, engine):
        """Real delete by whitelist entry IDs."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_whitelist(session, bot_id="bot-b", owner_id="user-1")

        with db.orm_session() as s:
            row = next(r for r in s.query(BotWhitelist).all() if r.bot_id == "bot-a")
            target_id = row.id

        body = {
            "ids": [target_id],
            "dry_run": False,
            "reason": "cleanup",
        }
        result = svc.delete_whitelist_entries(body, operator="admin")

        assert result["deleted"] == 1
        with db.orm_session() as s:
            assert s.query(BotWhitelist).count() == 1

    def test_not_found_reported(self, session, engine):
        """Non-existent IDs/pairs → not_found list."""
        svc, db = _build_svc(engine)

        body = {
            "ids": [99999],
            "dry_run": True,
            "reason": "test",
        }
        result = svc.delete_whitelist_entries(body, operator="admin")

        assert result["would_delete"] == 0
        assert len(result["not_found"]) == 1

    def test_audit_written_on_real_delete(self, session, engine):
        """Real delete writes WHITELIST_REMOVED audit per affected pair."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_whitelist(session, bot_id="bot-b", owner_id="user-1")

        body = {
            "bot_owner_pairs": [
                {"bot_id": "bot-a", "owner_id": "user-1"},
                {"bot_id": "bot-b", "owner_id": "user-1"},
            ],
            "dry_run": False,
            "reason": "cleanup",
        }
        svc.delete_whitelist_entries(body, operator="admin-1")

        with db.orm_session() as s:
            audits = s.query(GovernanceAudit).all()
            wl_audits = [a for a in audits if a.action_taken == AuditAction.WHITELIST_REMOVED]
            assert len(wl_audits) == 2
            assert all(a.actor_id == "admin-1" for a in wl_audits)

    def test_empty_request_returns_zero(self, session, engine):
        """No ids/pairs → would_delete=0."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")

        body = {"ids": [], "bot_owner_pairs": [], "dry_run": True, "reason": "test"}
        result = svc.delete_whitelist_entries(body, operator="admin")

        assert result["would_delete"] == 0
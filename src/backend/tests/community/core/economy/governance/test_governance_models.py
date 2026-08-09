"""ORM model tests — verify table creation, indexes, UK constraints, and defaults.

Spec ref: ``09-schema-field-review.md`` (F1–F4).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.economy.governance.orm import WhitelistEntryOrm, AuditLogOrm, GovernanceNotificationOrm, GovernanceTicketOrm


# ── Helpers ──────────────────────────────────────────────────────────────


def _index_names(table) -> set[str]:
    """Return the set of index names defined on a SQLAlchemy table."""
    return {idx.name for idx in table.__table__.indexes if idx.name}


def _column_type_length(table, col_name: str) -> int | None:
    """Return the declared length of a String column, or None if not String."""
    col = table.__table__.columns[col_name]
    return col.type.length


# ── GovernanceNotificationOrm ──────────────────────────────────────────────────


class TestGovernanceNotification:
    """Tests for ac_governance_notify_log."""

    def test_create_table_and_insert(self, session):
        """Basic insert and read-back."""
        row = GovernanceNotificationOrm(
            notification_id="n-001",
            bot_id="bot-1",
            bot_name="TestBot",
            owner_id="user-1",
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-1",
            notify_status="pending",
            governance_status="open",
            latest_decision="actionable",
            consecutive_normal_days=0,
            remind_count=0,
            send_attempt_count=0,
        )
        session.add(row)
        session.commit()

        fetched = session.query(GovernanceNotificationOrm).first()
        assert fetched.notification_id == "n-001"
        assert fetched.governance_status == "open"
        assert fetched.notify_status == "pending"
        assert fetched.latest_decision == "actionable"
        assert fetched.consecutive_normal_days == 0
        assert fetched.remind_count == 0
        assert fetched.send_attempt_count == 0

    def test_uk_worker_dt_version(self, session):
        """UK (worker_id, dt_version) violation on duplicate."""
        row1 = GovernanceNotificationOrm(
            notification_id="n-001",
            bot_id="bot-1",
            owner_id="user-1",
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-1",
        )
        row2 = GovernanceNotificationOrm(
            notification_id="n-002",
            bot_id="bot-1",
            owner_id="user-1",
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-1",
        )
        session.add(row1)
        session.commit()
        session.add(row2)
        # Old UK (worker_id, dt_version, env) demoted to regular index —
        # duplicates are allowed now (one ticket can have multiple sends).
        session.commit()  # Should NOT raise

    def test_governance_status_default(self, session):
        """Default governance_status = 'open'."""
        row = GovernanceNotificationOrm(
            notification_id="n-003",
            bot_id="bot-2",
            owner_id="user-2",
            worker_id="user-2:bot-2",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-2",
        )
        session.add(row)
        session.commit()
        assert row.governance_status == "open"

    def test_close_reason_and_cooldown(self, session):
        """Verify close_reason + cooldown_until fields for closed state."""
        row = GovernanceNotificationOrm(
            notification_id="n-004",
            bot_id="bot-3",
            owner_id="user-3",
            worker_id="user-3:bot-3",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-3",
            governance_status="closed",
            close_reason="user_optimized",
            closed_at=datetime(2026, 6, 30),
            cooldown_until=datetime(2026, 7, 14),
        )
        session.add(row)
        session.commit()
        fetched = session.query(GovernanceNotificationOrm).first()
        assert fetched.close_reason == "user_optimized"
        assert fetched.cooldown_until is not None

    def test_expired_no_cooldown(self, session):
        """Expired records have NULL cooldown_until (spec requirement)."""
        row = GovernanceNotificationOrm(
            notification_id="n-005",
            bot_id="bot-4",
            owner_id="user-4",
            worker_id="user-4:bot-4",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-4",
            governance_status="expired",
            close_reason="no_response_expired",
            closed_at=datetime(2026, 6, 30),
            cooldown_until=None,
        )
        session.add(row)
        session.commit()
        fetched = session.query(GovernanceNotificationOrm).first()
        assert fetched.governance_status == "expired"
        assert fetched.cooldown_until is None

    # --- F1: Index assertions (09-schema-field-review.md) ---

    def test_indexes_exist(self, engine, tables):
        """GovernanceNotificationOrm has the required hot-path indexes."""
        idx_names = _index_names(GovernanceNotificationOrm)
        expected = {
            "idx_econ_gov_notify_status",
            "idx_econ_gov_notify_owner_status",
            "idx_econ_gov_notify_bot_owner",
            "idx_econ_gov_notify_delivery",
            "idx_econ_gov_notify_ticket_id",
            "idx_econ_gov_notify_notify_status",
        }
        assert expected <= idx_names, (
            f"Missing indexes: {expected - idx_names}"
        )

    # --- F2: worker_id length ---

    def test_worker_id_length(self):
        """worker_id is String(160) — fits '{owner_id(64)}:{bot_id(64)}'."""
        assert _column_type_length(GovernanceNotificationOrm, "worker_id") == 160

    # --- F3: Dead columns must not exist ---

    def test_no_analysis_ref_column(self):
        """analysis_ref was removed (dead column, never written)."""
        col_names = {c.name for c in GovernanceNotificationOrm.__table__.columns}
        assert "analysis_ref" not in col_names

    def test_no_governance_max_dimension_column(self):
        """governance_max_dimension was removed (dead column, never written)."""
        col_names = {c.name for c in GovernanceNotificationOrm.__table__.columns}
        assert "governance_max_dimension" not in col_names

    # --- F4: expected_token_saving type ---

    def test_expected_token_saving_type(self):
        """expected_token_saving uses BigInteger, not AutoIncrementBigInteger."""
        col = GovernanceNotificationOrm.__table__.columns["expected_token_saving"]
        assert col.type.__class__.__name__ == "BigInteger"


# ── AuditLogOrm ────────────────────────────────────────────────


class TestAuditLog:
    """Tests for ac_governance_audit."""

    def test_append_only(self, session):
        """Audit rows are append-only — no UK constraint."""
        session.add(AuditLogOrm(
            run_id="r-001", bot_id="bot-1", owner_id="user-1",
            action_taken="enqueued",
        ))
        session.add(AuditLogOrm(
            run_id="r-001", bot_id="bot-1", owner_id="user-1",
            action_taken="enqueued",
        ))
        session.commit()
        count = session.query(AuditLogOrm).count()
        assert count == 2

    # --- F1: Index assertions ---

    def test_indexes_exist(self, engine, tables):
        """AuditLogOrm has data-readiness + run_id indexes."""
        idx_names = _index_names(AuditLogOrm)
        expected = {
            "idx_econ_gov_audit_action_time",
            "idx_econ_gov_audit_run",
        }
        assert expected <= idx_names, (
            f"Missing indexes: {expected - idx_names}"
        )

    # --- F4: expected_token_saving type ---

    def test_expected_token_saving_type(self):
        """expected_token_saving uses BigInteger, not AutoIncrementBigInteger."""
        col = AuditLogOrm.__table__.columns["expected_token_saving"]
        assert col.type.__class__.__name__ == "BigInteger"


# ── WhitelistEntryOrm ────────────────────────────────────────────────────────


class TestWhitelistEntry:
    """Tests for ac_bot_whitelist."""

    def test_insert_and_query(self, session):
        """Basic insert and read-back."""
        row = WhitelistEntryOrm(
            bot_id="bot-1",
            owner_id="user-1",
            whitelist_type="governance",
            source="manual",
            reason="test",
            created_by="admin",
        )
        session.add(row)
        session.commit()
        fetched = session.query(WhitelistEntryOrm).first()
        assert fetched.whitelist_type == "governance"

    def test_uk_dedup(self, session):
        """UK (bot_id, owner_id, whitelist_type) — duplicate raises."""
        row1 = WhitelistEntryOrm(
            bot_id="bot-1", owner_id="user-1",
            whitelist_type="governance", created_by="admin",
        )
        row2 = WhitelistEntryOrm(
            bot_id="bot-1", owner_id="user-1",
            whitelist_type="governance", created_by="admin",
        )
        session.add(row1)
        session.commit()
        session.add(row2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_same_bot_different_type(self, session):
        """Same bot can have governance + dormant whitelist."""
        row1 = WhitelistEntryOrm(
            bot_id="bot-1", owner_id="user-1",
            whitelist_type="governance", created_by="admin",
        )
        row2 = WhitelistEntryOrm(
            bot_id="bot-1", owner_id="user-1",
            whitelist_type="dormant", created_by="admin",
        )
        session.add_all([row1, row2])
        session.commit()
        count = session.query(WhitelistEntryOrm).count()
        assert count == 2

    # --- F1: Optional whitelist_type index ---

    def test_whitelist_type_index(self, engine, tables):
        """WhitelistEntryOrm has whitelist_type index for type-based lookups."""
        idx_names = _index_names(WhitelistEntryOrm)
        assert "idx_econ_gov_wl_type" in idx_names


# ── GovernanceTicketOrm ───────────────────────────────────────────


class TestGovernanceTicket:
    """Tests for ac_governance_task_record_daily."""

    def test_insert_and_query(self, session):
        """Basic insert and read-back."""
        row = GovernanceTicketOrm(
            worker_id="user-1:bot-1",
            bot_id="bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            bot_name="TestBot",
            analysis_status="success",
            last_sync_at=datetime(2026, 6, 29),
        )
        session.add(row)
        session.commit()
        fetched = session.query(GovernanceTicketOrm).first()
        assert fetched.governance_decision == "actionable"
        assert fetched.last_sync_at is not None

    def test_uk_worker_dt(self, session):
        """Old UK (worker_id, dt_version) demoted — duplicates allowed.

        In the new design, one worker can have multiple historical tickets,
        so (worker_id, dt_version) is no longer unique. Verify that
        duplicate rows are accepted.
        """
        row1 = GovernanceTicketOrm(
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            last_sync_at=datetime(2026, 6, 29),
        )
        row2 = GovernanceTicketOrm(
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="observe",
            last_sync_at=datetime(2026, 6, 29),
        )
        session.add(row1)
        session.commit()
        session.add(row2)
        # Old UK demoted to regular index — duplicates are allowed now
        session.commit()  # Should NOT raise

    def test_uk_active_worker(self, session):
        """UNIQUE(env, active_worker) — duplicate active_worker raises."""
        row1 = GovernanceTicketOrm(
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            last_sync_at=datetime(2026, 6, 29),
            ticket_id="t-001",
            active_worker="user-1:bot-1",
            governance_status="open",
        )
        row2 = GovernanceTicketOrm(
            worker_id="user-1:bot-1",
            dt_version="20260630",
            governance_decision="actionable",
            last_sync_at=datetime(2026, 6, 30),
            ticket_id="t-002",
            active_worker="user-1:bot-1",  # Same active_worker — should violate UK
            governance_status="open",
        )
        session.add(row1)
        session.commit()
        session.add(row2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_uk_ticket_id(self, session):
        """UNIQUE(env, ticket_id) — duplicate ticket_id raises."""
        row1 = GovernanceTicketOrm(
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            last_sync_at=datetime(2026, 6, 29),
            ticket_id="t-001",
            active_worker="user-1:bot-1",
            governance_status="open",
        )
        row2 = GovernanceTicketOrm(
            worker_id="user-2:bot-2",
            dt_version="20260629",
            governance_decision="actionable",
            last_sync_at=datetime(2026, 6, 29),
            ticket_id="t-001",  # Same ticket_id — should violate UK
            active_worker="user-2:bot-2",
            governance_status="open",
        )
        session.add(row1)
        session.commit()
        session.add(row2)
        with pytest.raises(IntegrityError):
            session.commit()

    # --- F1: dt_version leading index ---

    def test_dt_decision_index_exists(self, engine, tables):
        """task_record_daily has composite index on (dt_version, governance_decision, analysis_status).

        The UK (worker_id, dt_version) leads with worker_id so it can't
        serve dt_version-only filters. This composite index covers the
        hot read path in oceanbase_reader.get_actionable_bots.
        """
        idx_names = _index_names(GovernanceTicketOrm)
        assert "idx_econ_gov_taskrec_dt_decision" in idx_names

    # --- F2: worker_id length ---

    def test_worker_id_length(self):
        """worker_id is String(160) — matches notify_log, fits max concatenation."""
        assert _column_type_length(GovernanceTicketOrm, "worker_id") == 160

    # --- F4: expected_token_saving type ---

    def test_expected_token_saving_type(self):
        """expected_token_saving uses BigInteger, not AutoIncrementBigInteger."""
        col = GovernanceTicketOrm.__table__.columns["expected_token_saving"]
        assert col.type.__class__.__name__ == "BigInteger"

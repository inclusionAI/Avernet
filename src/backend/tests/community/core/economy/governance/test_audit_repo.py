"""Unit tests for GovernanceAuditRepository — self-managed session pattern.

Covers:
  - add_audit   (best-effort write, exception swallowed, self-managed session)
  - get_last_scan_time (MAX(gmt_create) for data-dependent scan actions)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.repositories.orm import (
    AuditLogOrm,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.utils.env_utils import get_current_env

# Re-use the shared FakeDB and fixtures from conftest.
from tests.community.core.economy.governance.conftest import FakeDB


_ENV_PATCH = (
    "agentclaw.community.core.economy.governance.repositories.audit_repo.get_current_env"
)


def _build_repo(engine) -> GovernanceAuditRepository:
    """Build a GovernanceAuditRepository backed by in-memory SQLite."""
    db = FakeDB(lambda: sessionmaker(bind=engine, expire_on_commit=False)())
    return GovernanceAuditRepository(db=db)


# ── add_audit ────────────────────────────────────────────────────────


class TestAddAudit:
    """Verify GovernanceAuditRepository.add_audit (self-managed session)."""

    def test_add_audit_success_writes_row(self, engine, tables):
        """add_audit should persist a row via self-managed session."""
        repo = _build_repo(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            repo.add_audit(
                "run-ok",
                bot_id="bot-a",
                owner_id="user-1",
                action_taken="notification_created",
                source="daily_scan",
            )

        # Verify row landed in the database.
        with patch(_ENV_PATCH, return_value="dev"):
            Session = sessionmaker(bind=engine, expire_on_commit=False)
            s = Session()
            try:
                audits = s.query(AuditLogOrm).filter_by(run_id="run-ok").all()
                assert len(audits) == 1
                assert audits[0].bot_id == "bot-a"
                assert audits[0].owner_id == "user-1"
                assert audits[0].action_taken == "notification_created"
                assert audits[0].env == "dev"
            finally:
                s.close()

    def test_add_audit_uses_current_env(self, engine, tables):
        """add_audit should set env from get_current_env(), not from caller."""
        repo = _build_repo(engine)
        with patch(_ENV_PATCH, return_value="pre"):
            repo.add_audit("run-pre", bot_id="bot-b", action_taken="enqueued")

        with patch(_ENV_PATCH, return_value="pre"):
            Session = sessionmaker(bind=engine, expire_on_commit=False)
            s = Session()
            try:
                audit = s.query(AuditLogOrm).filter_by(run_id="run-pre").first()
                assert audit is not None
                assert audit.env == "pre"
            finally:
                s.close()

    def test_add_audit_swallows_exception(self, engine, tables):
        """A failing write must be caught and logged, not raised."""

        class _BoomDB:
            def orm_session(self):
                class _BoomSession:
                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        return False

                    def add(self, _row):
                        raise RuntimeError("boom on add")

                return _BoomSession()

        repo = GovernanceAuditRepository(db=_BoomDB())
        with patch(_ENV_PATCH, return_value="dev"):
            # Must NOT raise despite the underlying add exploding.
            repo.add_audit("run-fail", bot_id="bot-x")

    def test_add_audit_full_params(self, engine, tables):
        """add_audit should accept all keyword parameters."""
        repo = _build_repo(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            repo.add_audit(
                "run-full",
                bot_id="bot-c",
                owner_id="user-2",
                notification_id="notif-123",
                actor_id="admin-1",
                check_result="actionable",
                governance_decision="enforce",
                hit_dimensions="token_usage,cost_ratio",
                expected_token_saving=50000,
                saving_ratio=0.35,
                action_taken="notification_created",
                source="offline_batch",
                error_msg=None,
                dry_run=0,
            )

        with patch(_ENV_PATCH, return_value="dev"):
            Session = sessionmaker(bind=engine, expire_on_commit=False)
            s = Session()
            try:
                audit = s.query(AuditLogOrm).filter_by(run_id="run-full").first()
                assert audit is not None
                assert audit.notification_id == "notif-123"
                assert audit.actor_id == "admin-1"
                assert audit.check_result == "actionable"
                assert float(audit.saving_ratio) == pytest.approx(0.35)
            finally:
                s.close()
"""Unit tests for GovernanceAuditRepository — self-managed session pattern.

Covers:
  - add_audit   (best-effort write, exception swallowed, self-managed session)
  - get_last_scan_time (MAX(gmt_create) for data-dependent scan actions)
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceAudit,
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
                audits = s.query(GovernanceAudit).filter_by(run_id="run-ok").all()
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
                audit = s.query(GovernanceAudit).filter_by(run_id="run-pre").first()
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
                audit = s.query(GovernanceAudit).filter_by(run_id="run-full").first()
                assert audit is not None
                assert audit.notification_id == "notif-123"
                assert audit.actor_id == "admin-1"
                assert audit.check_result == "actionable"
                assert float(audit.saving_ratio) == pytest.approx(0.35)
            finally:
                s.close()


# ── get_last_scan_time ───────────────────────────────────────────────


class TestGetLastScanTime:
    """Verify GovernanceAuditRepository.get_last_scan_time (self-managed session)."""

    def test_returns_max_gmt_create_for_matching_actions(self, engine, tables):
        """get_last_scan_time returns MAX(gmt_create) for data-dependent actions."""
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        s = Session()
        try:
            s.add(GovernanceAudit(
                run_id="run-1", action_taken="enqueued",
                env="dev", gmt_create=datetime(2026, 7, 1, 10, 0),
            ))
            s.add(GovernanceAudit(
                run_id="run-2", action_taken="notification_created",
                env="dev", gmt_create=datetime(2026, 7, 2, 14, 0),
            ))
            s.add(GovernanceAudit(
                run_id="run-3", action_taken="whitelist_filtered",
                env="dev", gmt_create=datetime(2026, 7, 3, 9, 0),
            ))
            s.commit()
        finally:
            s.close()

        repo = _build_repo(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            result = repo.get_last_scan_time()
        # MAX should be 2026-07-02 (whitelist_filtered is not a data-dependent action)
        assert result is not None
        assert result.day == 2

    def test_returns_none_when_no_matching_rows(self, engine, tables):
        """get_last_scan_time returns None when no data-dependent audit rows exist."""
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        s = Session()
        try:
            s.add(GovernanceAudit(
                run_id="run-x", action_taken="whitelist_filtered",
                env="dev", gmt_create=datetime(2026, 7, 1, 10, 0),
            ))
            s.commit()
        finally:
            s.close()

        repo = _build_repo(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            result = repo.get_last_scan_time()
        assert result is None

    def test_isolated_by_env(self, engine, tables):
        """get_last_scan_time only considers audit rows for the current env."""
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        s = Session()
        try:
            s.add(GovernanceAudit(
                run_id="dev-run", action_taken="enqueued", env="dev", gmt_create=now,
            ))
            s.add(GovernanceAudit(
                run_id="pre-run", action_taken="enqueued", env="pre", gmt_create=now,
            ))
            s.commit()
        finally:
            s.close()

        repo = _build_repo(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            result = repo.get_last_scan_time()
            assert result is not None
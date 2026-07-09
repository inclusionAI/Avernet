"""Extra tests for GovernanceAdminService — targets pause/resume, is_paused,
bulk_whitelist, and error/guard branches not covered by test_admin_service.py.

Reuses the exact fakes from test_admin_service (imported, not redefined) so the
two files share one set of stand-ins and combine cleanly under coverage.
"""
from __future__ import annotations

import json
from datetime import datetime

from .test_admin_service import (  # noqa: E402  (relative import within test package)
    _build_svc,
    _make_notification,
)

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceAudit,
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)


# ── helper: create GovernanceTaskRecordDaily rows ────────────────────


def _make_task_record(
    session, *, bot_id, owner_id, active_worker=None,
    ticket_id=None, governance_status="open", **overrides,
):
    """Create a test GovernanceTaskRecordDaily row."""
    worker_id = overrides.pop("worker_id", f"{owner_id}:{bot_id}")
    dt_version = overrides.pop("dt_version", "20260629")
    row = GovernanceTaskRecordDaily(
        worker_id=worker_id,
        bot_id=bot_id,
        owner_id=owner_id,
        dt_version=dt_version,
        governance_decision=overrides.pop("governance_decision", "actionable"),
        active_worker=active_worker,
        ticket_id=ticket_id,
        governance_status=governance_status,
        last_sync_at=overrides.pop("last_sync_at", datetime.now()),
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


# ── is_paused ────────────────────────────────────────────────────


class TestIsPaused:
    """Cover both branches of is_paused (lines 66-73)."""

    def test_not_paused_when_empty(self, session, engine):
        """No cache entry → not paused."""
        svc, _, _ = _build_svc(engine)
        assert svc.is_paused() is False

    def test_paused_when_action_pause(self, session, engine):
        """Cache holds action=pause → paused (JSON string branch)."""
        svc, _, cache = _build_svc(engine)
        cache.set(svc._emergency_key, json.dumps({"action": "pause"}))
        assert svc.is_paused() is True

    def test_not_paused_for_other_action(self, session, engine):
        """Cache holds a non-pause action → not paused."""
        svc, _, cache = _build_svc(engine)
        cache.set(svc._emergency_key, json.dumps({"action": "resume"}))
        assert svc.is_paused() is False

    def test_handles_non_str_raw(self, session, engine):
        """Non-str cached value goes through the ``isinstance`` else branch."""
        svc, _, cache = _build_svc(engine)
        # Bypass _FakeCache.set typing by injecting a dict directly.
        cache._store[svc._emergency_key] = ({"action": "pause"}, 0)
        assert svc.is_paused() is True

    def test_swallows_read_error(self, session, engine):
        """Cache.get raising → warning logged, returns False (except branch)."""
        svc, _, cache = _build_svc(engine)

        def _boom(_key):
            raise RuntimeError("cache down")

        cache.get = _boom  # type: ignore[method-assign]
        assert svc.is_paused() is False


# ── pause ────────────────────────────────────────────────────────


class TestPause:
    """Cover pause (lines 109-123) — cache write + audit."""

    def test_pause_writes_cache_and_audit(self, session, engine):
        """pause sets the emergency key and records an audit row."""
        svc, db, cache = _build_svc(engine)

        svc.pause(reason="overload", operator="admin-1")

        raw = cache.get(svc._emergency_key)
        assert raw is not None
        payload = json.loads(raw)
        assert payload["action"] == "pause"
        assert payload["reason"] == "overload"
        assert payload["operator"] == "admin-1"
        assert payload["paused_at"]

        # TTL propagated to the cache layer.
        stored_value, ttl = cache._store[svc._emergency_key]
        assert ttl == 7 * 24 * 3600

        # is_paused now reflects the write.
        assert svc.is_paused() is True

        with db.orm_session() as s:
            audits = [
                a for a in s.query(GovernanceAudit).all()
                if a.action_taken == AuditAction.ADMIN_PAUSE
            ]
            assert len(audits) == 1
            assert audits[0].actor_id == "admin-1"


# ── resume ───────────────────────────────────────────────────────


class TestResume:
    """Cover resume (lines 129-139) — delete + audit, including error branch."""

    def test_resume_deletes_key_and_audits(self, session, engine):
        """resume clears the key and writes an audit row."""
        svc, db, cache = _build_svc(engine)
        svc.pause(reason="x", operator="admin")
        assert svc.is_paused() is True

        svc.resume(reason="recovered", operator="admin-2")

        assert cache.get(svc._emergency_key) is None
        assert svc.is_paused() is False

        with db.orm_session() as s:
            audits = [
                a for a in s.query(GovernanceAudit).all()
                if a.action_taken == AuditAction.ADMIN_RESUME
            ]
            assert len(audits) == 1
            assert audits[0].actor_id == "admin-2"

    def test_resume_idempotent_when_not_paused(self, session, engine):
        """Resuming with no active key is a no-op that still audits."""
        svc, db, _ = _build_svc(engine)

        svc.resume(reason="noop", operator="admin")

        with db.orm_session() as s:
            audits = [
                a for a in s.query(GovernanceAudit).all()
                if a.action_taken == AuditAction.ADMIN_RESUME
            ]
            assert len(audits) == 1

    def test_resume_swallows_delete_error(self, session, engine):
        """cache.delete raising → warning logged, audit still written (except branch)."""
        svc, db, cache = _build_svc(engine)

        def _boom(_key):
            raise RuntimeError("cache down")

        cache.delete = _boom  # type: ignore[method-assign]

        svc.resume(reason="degraded", operator="admin")

        with db.orm_session() as s:
            audits = [
                a for a in s.query(GovernanceAudit).all()
                if a.action_taken == AuditAction.ADMIN_RESUME
            ]
            assert len(audits) == 1


# ── bulk_whitelist ───────────────────────────────────────────────


class TestBulkWhitelist:
    """Cover bulk_whitelist — cancels pending notifications for specified bots."""

    def test_whitelists_and_cancels_pending(self, session, engine):
        """Bots with pending notifications → whitelisted + cancelled."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
            ticket_id="t-1",
        )
        _make_notification(
            session, notification_id="n-2",
            bot_id="bot-b", owner_id="owner-b", governance_status="open",
            ticket_id="t-2",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-a", "bot-b"], reason="cleanup", operator="admin",
        )

        # Two distinct (bot, owner) pairs → 2 whitelisted.
        assert result["whitelisted"] == 2
        assert result["cancelled"] == 2

        with db.orm_session() as s:
            notif_rows = s.query(GovernanceNotifyLog).all()
            for n in notif_rows:
                assert n.notify_status == "cancelled"
                assert n.governance_status == "closed"

    def test_no_matching_bots_skips_whitelist(self, session, engine):
        """Unknown bot ids → no owner pairs → whitelisted=0, cancelled=0."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-known", owner_id="owner-1", governance_status="open",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-unknown"], reason="x", operator="admin",
        )

        assert result["whitelisted"] == 0
        assert result["cancelled"] == 0

    def test_skips_already_closed_notifications(self, session, engine):
        """Already-closed/sent notifications are not affected by bulk_whitelist."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-open",
            bot_id="bot-x", owner_id="owner-x", governance_status="open",
            ticket_id="t-open",
        )
        _make_notification(
            session, notification_id="n-closed",
            bot_id="bot-x", owner_id="owner-x", governance_status="closed",
            ticket_id="t-closed", notify_status="sent",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-x"], reason="x", operator="admin",
        )

        # Only the open/pending notification is cancelled.
        assert result["cancelled"] == 1


# ── error branches: commit failures & audit failure ──────────────


class _RaisingSession:
    """Wraps a real session but raises on commit to hit rollback branches."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def commit(self):
        raise RuntimeError("commit boom")


class _RaisingCommitDB:
    """DB whose first N orm_session() commits raise, to trigger rollback paths."""

    def __init__(self, real_db, fail_first: int):
        self._real_db = real_db
        self._remaining = fail_first

    def orm_session(self):
        from contextlib import contextmanager

        ctx = self._real_db.orm_session()

        @contextmanager
        def _wrap():
            with ctx as real:
                if self._remaining > 0:
                    self._remaining -= 1
                    yield _RaisingSession(real)
                else:
                    yield real

        return _wrap()


class TestErrorBranches:
    """Cover rollback branches in cancel_pending / bulk_whitelist and audit failure."""

    def test_cancel_pending_rollback_on_commit_error(self, session, engine):
        """cancel_pending commit failure → rollback, cancelled reset to 0."""
        svc, db, _ = _build_svc(engine)
        _make_task_record(
            session, bot_id="bot-1", owner_id="owner-1",
            active_worker="owner-1:bot-1", ticket_id="t-1",
            governance_status="open",
        )

        # Fail only the first orm_session commit (the cancel loop); audit uses a
        # later session and should still succeed.
        svc._db = _RaisingCommitDB(db, fail_first=1)

        result = svc.cancel_pending(reason="x", operator="admin")
        assert result["cancelled"] == 0

    def test_bulk_whitelist_rollback_on_commit_error(self, session, engine):
        """bulk_whitelist cancel-commit failure → rollback branch.

        Since bulk_whitelist delegates to GovernanceWhitelistService, we
        patch the whitelist service's _db to inject commit failure.
        """
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )
        _make_task_record(
            session, bot_id="bot-a", owner_id="owner-a",
            active_worker="owner-a:bot-a", ticket_id="t-1",
            governance_status="open",
        )

        # bulk_whitelist now runs inside GovernanceWhitelistService.
        # The service opens sessions for:
        #   1) list_distinct_bot_owner (read via notify_repo — self-managed)
        #   2) batch_add (whitelist_repo — self-managed)
        #   3) close tickets loop → commit (the target failure)
        # Fail the whitelist service's orm_session commits.
        svc._whitelist_service._db = _RaisingCommitDB(db, fail_first=1)

        result = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="x", operator="admin",
        )
        assert "cancelled" in result

    def test_audit_write_failure_is_swallowed(self, session, engine):
        """_write_emergency_audit swallows exceptions."""
        svc, db, cache = _build_svc(engine)

        class _AlwaysRaiseCommitDB:
            def orm_session(self_inner):
                from contextlib import contextmanager

                ctx = db.orm_session()

                @contextmanager
                def _wrap():
                    with ctx as real:
                        yield _RaisingSession(real)

                return _wrap()

        svc._db = _AlwaysRaiseCommitDB()
        # resume only touches cache + audit; audit commit raises but is swallowed.
        svc.resume(reason="x", operator="admin")  # must not raise


# ── _read_pause_info non-str + error branches ────────────────────


class TestReadPauseInfo:
    """Cover _read_pause_info dict branch and swallowed error."""

    def test_get_state_reads_dict_pause_info(self, session, engine):
        """Non-str cached pause info flows through the dict branch of _read_pause_info."""
        svc, _, cache = _build_svc(engine)
        cache._store[svc._emergency_key] = (
            {"action": "pause", "reason": "r", "operator": "op",
             "paused_at": "2026-01-01"},
            0,
        )
        state = svc.get_state()
        assert state["paused"] is True
        assert state["reason"] == "r"
        assert state["operator"] == "op"

    def test_get_state_swallows_cache_error(self, session, engine):
        """Cache.get raising in _read_pause_info → empty info, not paused."""
        svc, _, cache = _build_svc(engine)

        def _boom(_key):
            raise RuntimeError("cache down")

        cache.get = _boom  # type: ignore[method-assign]
        state = svc.get_state()
        assert state["paused"] is False
        assert state["reason"] is None

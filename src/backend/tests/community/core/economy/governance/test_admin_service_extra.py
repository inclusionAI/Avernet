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
)

from agentclaw.community.core.economy.governance.domain.enums import AuditAction
from agentclaw.community.core.economy.governance.repositories.orm import (
    AuditLogOrm,
    GovernanceTicketOrm,
)


# ── helper: create GovernanceTicketOrm rows ────────────────────


def _make_task_record(
    session, *, bot_id, owner_id, active_worker=None,
    ticket_id=None, governance_status="open", **overrides,
):
    """Create a test GovernanceTicketOrm row."""
    worker_id = overrides.pop("worker_id", f"{owner_id}:{bot_id}")
    dt_version = overrides.pop("dt_version", "20260629")
    row = GovernanceTicketOrm(
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
        cache.set(svc._brake_key, json.dumps({"action": "pause"}))
        assert svc.is_paused() is True

    def test_not_paused_for_other_action(self, session, engine):
        """Cache holds a non-pause action → not paused."""
        svc, _, cache = _build_svc(engine)
        cache.set(svc._brake_key, json.dumps({"action": "resume"}))
        assert svc.is_paused() is False

    def test_handles_non_str_raw(self, session, engine):
        """Non-str cached value goes through the ``isinstance`` else branch."""
        svc, _, cache = _build_svc(engine)
        # Bypass _FakeCache.set typing by injecting a dict directly.
        cache._store[svc._brake_key] = ({"action": "pause"}, 0)
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
        """pause sets the brake key and records an audit row."""
        svc, db, cache = _build_svc(engine)

        svc.pause(reason="overload", operator="admin-1")

        raw = cache.get(svc._brake_key)
        assert raw is not None
        payload = json.loads(raw)
        assert payload["action"] == "pause"
        assert payload["reason"] == "overload"
        assert payload["operator"] == "admin-1"
        assert payload["paused_at"]

        # TTL propagated to the cache layer.
        stored_value, ttl = cache._store[svc._brake_key]
        assert ttl == 7 * 24 * 3600

        # is_paused now reflects the write.
        assert svc.is_paused() is True

        with db.orm_session() as s:
            audits = [
                a for a in s.query(AuditLogOrm).all()
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

        assert cache.get(svc._brake_key) is None
        assert svc.is_paused() is False

        with db.orm_session() as s:
            audits = [
                a for a in s.query(AuditLogOrm).all()
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
                a for a in s.query(AuditLogOrm).all()
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
                a for a in s.query(AuditLogOrm).all()
                if a.action_taken == AuditAction.ADMIN_RESUME
            ]
            assert len(audits) == 1


# ── _read_pause_info non-str + error branches ────────────────────


class TestReadPauseInfo:
    """Cover _read_pause_info dict branch and swallowed error."""

    def test_get_state_reads_dict_pause_info(self, session, engine):
        """Non-str cached pause info flows through the dict branch of _read_pause_info."""
        svc, _, cache = _build_svc(engine)
        cache._store[svc._brake_key] = (
            {"action": "pause", "reason": "r", "operator": "op",
             "paused_at": "2026-01-01"},
            0,
        )
        state = svc.get_state()
        assert state.paused is True
        assert state.reason == "r"
        assert state.operator == "op"

    def test_get_state_swallows_cache_error(self, session, engine):
        """Cache.get raising in _read_pause_info → empty info, not paused."""
        svc, _, cache = _build_svc(engine)

        def _boom(_key):
            raise RuntimeError("cache down")

        cache.get = _boom  # type: ignore[method-assign]
        state = svc.get_state()
        assert state.paused is False
        assert state.reason is None

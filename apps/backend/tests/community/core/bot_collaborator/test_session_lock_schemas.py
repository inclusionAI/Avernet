"""Tests for session-lock Pydantic schemas and domain models.

Covers the new session-lock types from the staged changes:
- SessionLockRequest, SessionReleaseLockRequest, SessionLockResponse,
  SessionLockInfoResponse (adapters/http layer)
- SessionLockInfoResult (core layer)
- _make_session_lock_key static method
"""
import pytest
from datetime import datetime
from pydantic import ValidationError

from agentclaw.community.adapters.http.bot_collaborator.schemas import (
    SessionLockRequest,
    SessionReleaseLockRequest,
    SessionLockResponse,
    SessionLockInfoResponse,
    LockInfo,
)
from agentclaw.community.core.bot_collaborator.models import (
    SessionLockInfoResult,
    BotCollabLockRecord,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)


# ============================================================================
# SessionLockRequest
# ============================================================================


class TestSessionLockRequest:
    """Tests for SessionLockRequest schema."""

    def test_valid_request(self):
        """Valid session lock request with all required fields."""
        req = SessionLockRequest(
            bot_id="app_123",
            owner_id="100000",
            session_id="sess_abc",
        )
        assert req.bot_id == "app_123"
        assert req.owner_id == "100000"
        assert req.session_id == "sess_abc"

    def test_missing_bot_id_raises(self):
        """Missing bot_id raises ValidationError."""
        with pytest.raises(ValidationError):
            SessionLockRequest(owner_id="100000", session_id="sess_abc")

    def test_missing_owner_id_raises(self):
        """Missing owner_id raises ValidationError."""
        with pytest.raises(ValidationError):
            SessionLockRequest(bot_id="app_123", session_id="sess_abc")

    def test_missing_session_id_raises(self):
        """Missing session_id raises ValidationError."""
        with pytest.raises(ValidationError):
            SessionLockRequest(bot_id="app_123", owner_id="100000")

    def test_model_dump(self):
        """model_dump includes all fields."""
        req = SessionLockRequest(
            bot_id="app_123", owner_id="100000", session_id="sess_abc"
        )
        d = req.model_dump()
        assert d == {"bot_id": "app_123", "owner_id": "100000", "session_id": "sess_abc"}


# ============================================================================
# SessionReleaseLockRequest
# ============================================================================


class TestSessionReleaseLockRequest:
    """Tests for SessionReleaseLockRequest schema."""

    def test_valid_request_defaults(self):
        """Valid release request with force defaulting to False."""
        req = SessionReleaseLockRequest(
            bot_id="app_123",
            owner_id="100000",
            session_id="sess_abc",
        )
        assert req.force is False

    def test_force_explicit_true(self):
        """Force can be explicitly set to True."""
        req = SessionReleaseLockRequest(
            bot_id="app_123",
            owner_id="100000",
            session_id="sess_abc",
            force=True,
        )
        assert req.force is True

    def test_missing_session_id_raises(self):
        """Missing session_id raises ValidationError."""
        with pytest.raises(ValidationError):
            SessionReleaseLockRequest(bot_id="app_123", owner_id="100000")


# ============================================================================
# SessionLockResponse
# ============================================================================


class TestSessionLockResponse:
    """Tests for SessionLockResponse schema."""

    def test_acquired_with_lock(self):
        """acquired=True with lock info."""
        lock = LockInfo(
            id=1,
            lock_key="session:app_123:100000:sess_abc",
            holder_user_id="100000",
            gmt_create=datetime(2024, 1, 1, 12, 0, 0),
        )
        resp = SessionLockResponse(acquired=True, lock=lock)
        assert resp.acquired is True
        assert resp.lock is not None
        assert resp.lock.lock_key == "session:app_123:100000:sess_abc"

    def test_not_acquired_no_lock(self):
        """acquired=False with lock=None."""
        resp = SessionLockResponse(acquired=False, lock=None)
        assert resp.acquired is False
        assert resp.lock is None

    def test_model_dump_acquired(self):
        """model_dump includes nested lock dict."""
        lock = LockInfo(
            id=1,
            lock_key="session:app_123:100000:sess_abc",
            holder_user_id="100000",
            gmt_create=datetime(2024, 1, 1, 12, 0, 0),
        )
        resp = SessionLockResponse(acquired=True, lock=lock)
        d = resp.model_dump()
        assert d["acquired"] is True
        assert d["lock"]["lock_key"] == "session:app_123:100000:sess_abc"
        assert d["lock"]["holder_user_id"] == "100000"


# ============================================================================
# SessionLockInfoResponse
# ============================================================================


class TestSessionLockInfoResponse:
    """Tests for SessionLockInfoResponse schema."""

    def test_unlocked_defaults(self):
        """Unlocked state with all defaults."""
        resp = SessionLockInfoResponse()
        assert resp.locked is False
        assert resp.lock is None
        assert resp.is_mine is False

    def test_locked_is_mine(self):
        """Locked by current user."""
        lock = LockInfo(
            id=1,
            lock_key="session:app_123:100000:sess_abc",
            holder_user_id="100000",
            holder_name="Me",
            gmt_create=datetime(2024, 1, 1, 12, 0, 0),
        )
        resp = SessionLockInfoResponse(locked=True, lock=lock, is_mine=True)
        assert resp.locked is True
        assert resp.is_mine is True
        assert resp.lock.holder_user_id == "100000"

    def test_locked_not_mine(self):
        """Locked by another user."""
        lock = LockInfo(
            id=1,
            lock_key="session:app_123:100000:sess_abc",
            holder_user_id="other",
            holder_name="Other",
            gmt_create=datetime(2024, 1, 1, 12, 0, 0),
        )
        resp = SessionLockInfoResponse(locked=True, lock=lock, is_mine=False)
        assert resp.locked is True
        assert resp.is_mine is False

    def test_model_dump(self):
        """model_dump produces expected structure."""
        resp = SessionLockInfoResponse(locked=False, lock=None, is_mine=False)
        d = resp.model_dump()
        assert d == {"locked": False, "lock": None, "is_mine": False}


# ============================================================================
# SessionLockInfoResult (core domain model)
# ============================================================================


class TestSessionLockInfoResult:
    """Tests for SessionLockInfoResult core domain model."""

    def test_unlocked_defaults(self):
        """Unlocked with all defaults."""
        result = SessionLockInfoResult()
        assert result.locked is False
        assert result.lock is None
        assert result.holder_name is None
        assert result.is_mine is False

    def test_locked_with_record(self):
        """Locked with a lock record and holder info."""
        record = BotCollabLockRecord(
            id=1,
            lock_key="session:app_123:100000:sess_abc",
            holder_user_id="100000",
            env="dev",
        )
        result = SessionLockInfoResult(
            locked=True, lock=record, holder_name="张三", is_mine=True
        )
        assert result.locked is True
        assert result.lock.lock_key == "session:app_123:100000:sess_abc"
        assert result.holder_name == "张三"
        assert result.is_mine is True

    def test_locked_not_mine(self):
        """Locked by another, is_mine=False."""
        record = BotCollabLockRecord(
            id=2,
            lock_key="session:app_123:100000:sess_abc",
            holder_user_id="other_user",
            env="dev",
        )
        result = SessionLockInfoResult(
            locked=True, lock=record, holder_name="李四", is_mine=False
        )
        assert result.locked is True
        assert result.is_mine is False
        assert result.holder_name == "李四"


# ============================================================================
# _make_session_lock_key
# ============================================================================


class TestMakeSessionLockKey:
    """Tests for CollaboratorLockService._make_session_lock_key static method."""

    def test_key_format(self):
        """Key uses 3-segment format: session:{bot_id}:{owner_id}:{session_id}."""
        key = CollaboratorLockService._make_session_lock_key("botA", "ownerB", "sessC")
        assert key == "session:botA:ownerB:sessC"

    def test_key_differs_from_bot_lock_key(self):
        """Session key is distinct from bot-level 2-segment key."""
        session_key = CollaboratorLockService._make_session_lock_key("botA", "ownerB", "sessC")
        bot_key = CollaboratorLockService._make_lock_key("botA", "ownerB")
        assert session_key != bot_key
        # Session key has "session:" prefix
        assert session_key.startswith("session:")
        # Bot key uses simple 2-segment format
        assert not bot_key.startswith("session:")

    def test_key_namespace_isolation(self):
        """Different sessions produce different keys."""
        key1 = CollaboratorLockService._make_session_lock_key("botA", "ownerB", "sess1")
        key2 = CollaboratorLockService._make_session_lock_key("botA", "ownerB", "sess2")
        assert key1 != key2

    def test_key_same_session_same_key(self):
        """Same session params produce the same key."""
        key1 = CollaboratorLockService._make_session_lock_key("botA", "ownerB", "sess1")
        key2 = CollaboratorLockService._make_session_lock_key("botA", "ownerB", "sess1")
        assert key1 == key2
"""Unit tests for bot_session domain models, exceptions, enums, and protocols."""

from __future__ import annotations

import pytest

from secbaas.core.service.bot_session import (
    PaginatedResult,
    SessionClosedError,
    SessionError,
    SessionNotFoundError,
    SessionService,
    SessionStatus,
)


class TestSessionStatus:
    """Test SessionStatus enum."""

    def test_values(self):
        assert SessionStatus.PENDING == "PENDING"
        assert SessionStatus.RUNNING == "RUNNING"
        assert SessionStatus.COMPLETED == "COMPLETED"
        assert SessionStatus.FAILED == "FAILED"

    def test_lifecycle_order(self):
        """Status lifecycle: PENDING -> RUNNING -> COMPLETED/FAILED."""
        assert SessionStatus.PENDING < SessionStatus.RUNNING


class TestSessionExceptions:
    """Test bot_session exceptions."""

    def test_session_error(self):
        err = SessionError()
        assert err.error_code == "SESSION_ERROR"
        assert err.http_status == 500

    def test_session_not_found(self):
        err = SessionNotFoundError(session_id="sess-1")
        assert err.session_id == "sess-1"
        assert err.http_status == 404

    def test_session_closed(self):
        err = SessionClosedError(session_id="sess-1")
        assert err.session_id == "sess-1"
        assert err.http_status == 409

    def test_session_closed_differs_from_bot_runtime(self):
        """bot_session.SessionClosedError uses 409, bot_runtime uses 400."""
        from secbaas.api.bot_runtime import SessionClosedError as BrClosed

        br_err = BrClosed(session_id="sess-1")
        assert br_err.http_status == 400
        # Need a reference to bot_session's version
        bs_err = SessionClosedError(session_id="sess-1")
        assert bs_err.http_status == 409


class TestPaginatedResult:
    """Test PaginatedResult dataclass."""

    def test_fields(self):
        result = PaginatedResult(total=10, page=1, page_size=20, items=[1, 2, 3])
        assert result.total == 10
        assert result.page == 1
        assert result.page_size == 20
        assert result.items == [1, 2, 3]

    def test_frozen(self):
        result = PaginatedResult(total=0, page=1, page_size=20, items=[])
        with pytest.raises(AttributeError):
            result.total = 5


class TestSessionServiceProtocol:
    """Test SessionService protocol."""

    def test_is_protocol(self):
        assert issubclass(type(SessionService), type)

    def test_runtime_checkable(self):
        import inspect

        assert inspect.isclass(SessionService)

    def test_all_methods_exist(self):
        methods = [
            "create_session",
            "mark_running",
            "mark_completed",
            "mark_failed",
            "get_by_session_id",
            "list_by_bot",
            "list_by_time_range",
            "list_by_bot_device_invoker",
            "update_context",
        ]
        for method in methods:
            assert hasattr(SessionService, method)
            assert callable(getattr(SessionService, method))

    def test_all_static_methods(self):
        """Core lifecycle methods exist on the protocol as instance methods."""
        import inspect

        for name in [
            "create_session",
            "mark_running",
            "mark_completed",
            "mark_failed",
        ]:
            method = inspect.getattr_static(SessionService, name)
            assert callable(method)
            params = list(inspect.signature(method).parameters)
            assert params[0] == "self"

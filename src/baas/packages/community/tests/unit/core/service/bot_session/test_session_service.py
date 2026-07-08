"""Tests for DefaultDefaultSessionService.

Covers:
- Full lifecycle: create -> running -> completed/failed
- All 9 protocol methods
- Edge cases: empty context, None params, no-op updates, empty results
- Protocol contract: DefaultSessionService satisfies SessionService Protocol
- PaginatedResult construction
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.core.repository.bot_session import BotSessionRecord
from secbaas.core.service.bot_session import (
    DefaultSessionService,
    PaginatedResult,
    SessionClosedError,
    SessionError,
    SessionNotFoundError,
    SessionService,
    SessionStatus,
)


@pytest.fixture
def mock_repo():
    """Fixture providing mock repository."""
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    """Fixture providing DefaultSessionService instance with mock repo."""
    return DefaultSessionService(repository=mock_repo)


@pytest.fixture
def sample_session_record():
    """Fixture providing sample session record."""
    return BotSessionRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        bot_uuid="bot-123",
        invoker="user-1",
        session_id="SESSION-test-001",
        req={"command": "echo hello"},
        result={"output": "hello"},
        err_msg=None,
        context={"trace_id": "trace-123"},
        status=SessionStatus.COMPLETED.value,
        device_uuid="device-456",
        env="dev",
        tenant="test-tenant",
    )


# ============== Protocol Contract ==============


class TestSessionServiceProtocol:
    """Verify DefaultSessionService satisfies the SessionService Protocol."""

    def test_isinstance_check_passes(self, service):
        """DefaultSessionService must be runtime-checkable as SessionService."""
        assert isinstance(service, SessionService)

    def test_protocol_has_all_expected_methods(self):
        """SessionService Protocol must declare all 9 expected static methods."""
        methods = {
            "create_session",
            "mark_running",
            "mark_completed",
            "mark_failed",
            "get_by_session_id",
            "list_by_bot",
            "list_by_time_range",
            "list_by_bot_device_invoker",
            "update_context",
        }
        protocol_methods = {
            name for name in dir(SessionService) if not name.startswith("_")
        }
        assert methods.issubset(protocol_methods), (
            f"Missing: {methods - protocol_methods}"
        )


# ============== API Type Tests ==============


class TestSessionStatus:
    """SessionStatus enum contract."""

    def test_all_statuses_defined(self):
        assert SessionStatus.PENDING.value == "PENDING"
        assert SessionStatus.RUNNING.value == "RUNNING"
        assert SessionStatus.COMPLETED.value == "COMPLETED"
        assert SessionStatus.FAILED.value == "FAILED"

    def test_statuses_are_strings(self):
        assert isinstance(SessionStatus.PENDING, str)


class TestSessionExceptions:
    """SessionError hierarchy contract."""

    def test_session_error_base(self):
        err = SessionError("something went wrong")
        assert err.message == "something went wrong"
        assert err.error_code == "SESSION_ERROR"

    def test_session_not_found_error(self):
        err = SessionNotFoundError("SESSION-xxx")
        assert err.session_id == "SESSION-xxx"
        assert str(err) == "Session not found: SESSION-xxx"

    def test_session_closed_error(self):
        err = SessionClosedError("SESSION-xxx")
        assert err.session_id == "SESSION-xxx"
        assert str(err) == "Session closed: SESSION-xxx"


class TestPaginatedResult:
    """PaginatedResult dataclass contract."""

    def test_construction(self):
        result = PaginatedResult(total=10, page=1, page_size=20, items=["a", "b"])
        assert result.total == 10
        assert result.page == 1
        assert result.page_size == 20
        assert result.items == ["a", "b"]

    def test_frozen_dataclass(self):
        result = PaginatedResult(total=0, page=1, page_size=10, items=[])
        with pytest.raises(AttributeError):
            result.total = 5  # type: ignore[misc]

    def test_empty_items(self):
        result = PaginatedResult(total=0, page=1, page_size=10, items=[])
        assert result.items == []


# ============== Service Lifecycle ==============


class TestCreateSession:
    """DefaultSessionService.create_session tests."""

    def test_generates_valid_session_id(self, service, mock_repo):
        mock_repo.insert_session.return_value = 1

        session_id = service.create_session(
            bot_uuid="bot-123",
            invoker="user-1",
            req={"command": "test"},
            device_uuid="device-456",
            tenant="test-tenant",
        )

        assert session_id.startswith("SESSION-")
        assert len(session_id) == 8 + 32  # "SESSION-" + 32 hex chars
        mock_repo.insert_session.assert_called_once()
        call_kwargs = mock_repo.insert_session.call_args.kwargs
        assert call_kwargs["status"] == SessionStatus.PENDING.value
        assert call_kwargs["bot_uuid"] == "bot-123"
        assert call_kwargs["tenant"] == "test-tenant"


def test_create_session_stores_trace_id(service, mock_repo):
    """Test that trace_id is stored in context field."""
    mock_repo.insert_session.return_value = 1

    service.create_session(
        bot_uuid="bot-123",
        invoker="user-1",
        req={"command": "test"},
        device_uuid="device-456",
        tenant="test-tenant",
        trace_id="trace-abc-123",
    )

    call_kwargs = mock_repo.insert_session.call_args.kwargs
    assert call_kwargs["context"]["trace_id"] == "trace-abc-123"


def test_create_session_without_trace_id(service, mock_repo):
    """Test that session can be created without trace_id."""
    mock_repo.insert_session.return_value = 1

    session_id = service.create_session(
        bot_uuid="bot-123",
        invoker="user-1",
        req={"command": "test"},
        device_uuid="device-456",
        tenant="test-tenant",
    )

    assert session_id is not None
    call_kwargs = mock_repo.insert_session.call_args.kwargs
    assert call_kwargs["context"] is None


def test_mark_running_updates_status(service, mock_repo):
    """Test that mark_running changes status to RUNNING."""
    service.mark_running("SESSION-test")

    mock_repo.update_status.assert_called_once_with(
        session_id="SESSION-test",
        status=SessionStatus.RUNNING.value,
    )


def test_mark_completed_stores_result_and_status(service, mock_repo):
    """Test that mark_completed stores result with COMPLETED status."""
    result = {
        "output": "test output",
        "duration_ms": 1234,
        "device_status": "SUCCESS",
    }

    service.mark_completed(
        session_id="SESSION-test",
        result=result,
    )

    mock_repo.update_context.assert_called_once()
    call_kwargs = mock_repo.update_context.call_args.kwargs
    assert call_kwargs["result"]["device_status"] == "SUCCESS"
    assert call_kwargs["result"]["output"] == "test output"
    # No error message on success
    assert call_kwargs.get("err_msg") is None
    # Status is updated separately
    mock_repo.update_status.assert_called_once_with(
        session_id="SESSION-test",
        status=SessionStatus.COMPLETED.value,
    )


def test_mark_completed_without_device_status(service, mock_repo):
    """Test that mark_completed works without device_status."""
    result = {"output": "test"}

    service.mark_completed(
        session_id="SESSION-test",
        result=result,
    )

    call_kwargs = mock_repo.update_context.call_args.kwargs
    assert call_kwargs["result"]["output"] == "test"


def test_mark_failed_stores_err_msg_separately(service, mock_repo):
    """Test that mark_failed stores err_msg separately from result."""
    service.mark_failed(
        session_id="SESSION-test",
        err_msg="Device execution failed: timeout after 30s",
        result={"device_status": "TIMEOUT"},
    )

    mock_repo.update_context.assert_called_once()
    call_kwargs = mock_repo.update_context.call_args.kwargs
    assert call_kwargs["err_msg"] == "Device execution failed: timeout after 30s"
    # Result contains device_status in the result dict
    assert call_kwargs["result"]["device_status"] == "TIMEOUT"
    # Status is updated separately
    mock_repo.update_status.assert_called_once_with(
        session_id="SESSION-test",
        status=SessionStatus.FAILED.value,
    )


def test_mark_failed_without_device_status(service, mock_repo):
    """Test that mark_failed works without device_status."""
    service.mark_failed(
        session_id="SESSION-test",
        err_msg="Container not found",
    )

    call_kwargs = mock_repo.update_context.call_args.kwargs
    assert call_kwargs["err_msg"] == "Container not found"
    # Result is None when not provided
    assert call_kwargs["result"] is None


def test_sync_session_lifecycle_success(service, mock_repo):
    """Test complete sync session lifecycle: PENDING -> RUNNING -> COMPLETED."""
    mock_repo.insert_session.return_value = 1

    # Step 1: Create session
    session_id = service.create_session(
        bot_uuid="bot-123",
        invoker="user-1",
        req={"command": "echo hello"},
        device_uuid="device-456",
        tenant="test-tenant",
    )

    # Verify PENDING status at creation
    call_kwargs = mock_repo.insert_session.call_args.kwargs
    assert call_kwargs["status"] == SessionStatus.PENDING.value

    # Step 2: Mark running (sync: immediate)
    service.mark_running(session_id)

    # Step 3: Mark completed
    service.mark_completed(
        session_id=session_id,
        result={"output": "hello", "duration_ms": 100, "device_status": "SUCCESS"},
    )

    # Verify final state - status update is called
    mock_repo.update_status.assert_called_with(
        session_id=session_id,
        status=SessionStatus.COMPLETED.value,
    )


def test_async_session_lifecycle_failure(service, mock_repo):
    """Test async lifecycle with failure: PENDING -> RUNNING -> FAILED."""
    mock_repo.insert_session.return_value = 1

    # Step 1: Create session (PENDING)
    session_id = service.create_session(
        bot_uuid="bot-123",
        invoker="user-1",
        req={"command": "risky-op"},
        device_uuid="device-456",
        tenant="test-tenant",
    )

    # Step 2: Background task starts (RUNNING)
    service.mark_running(session_id)

    # Step 3: Background task fails
    service.mark_failed(
        session_id=session_id,
        err_msg="Execution failed: division by zero",
        result={"device_status": "ERROR"},
    )

    # Verify FAILED state with err_msg
    mock_repo.update_context.assert_called()
    context_call = mock_repo.update_context.call_args.kwargs
    assert context_call["err_msg"] == "Execution failed: division by zero"
    mock_repo.update_status.assert_called_with(
        session_id=session_id,
        status=SessionStatus.FAILED.value,
    )


def test_get_by_session_id_found(service, mock_repo, sample_session_record):
    """Test get_by_session_id returns record when found."""
    mock_repo.get_by_session_id.return_value = sample_session_record

    result = service.get_by_session_id("SESSION-test-001")

    assert result is not None
    assert result.session_id == "SESSION-test-001"
    assert result.status == SessionStatus.COMPLETED.value


def test_get_by_session_id_not_found(service, mock_repo):
    """Test get_by_session_id returns None when not found."""
    mock_repo.get_by_session_id.return_value = None

    result = service.get_by_session_id("SESSION-nonexistent")

    assert result is None


def test_list_by_time_range_filters_by_bot_uuid(service, mock_repo):
    """Test list_by_time_range delegates to repository."""
    record1 = BotSessionRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        bot_uuid="bot-1",
        invoker="user-1",
        session_id="SESSION-1",
        req={},
        result={},
        err_msg=None,
        context={"trace_id": "trace-target"},
        status=SessionStatus.COMPLETED.value,
        device_uuid="d1",
        env="dev",
        tenant="test-tenant",
    )
    mock_repo.list_by_time_range.return_value = [record1]

    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 2)

    results = service.list_by_time_range(
        start_time=start,
        end_time=end,
        bot_uuid="bot-1",
    )

    assert len(results) == 1
    assert results[0].session_id == "SESSION-1"
    mock_repo.list_by_time_range.assert_called_once_with(
        start_time=start,
        end_time=end,
        bot_uuid="bot-1",
    )


def test_list_by_bot_returns_paginated_result(service, mock_repo):
    """Test list_by_bot returns PaginatedResult with correct structure."""
    mock_repo.list_by_bot_uuid.return_value = (100, [])  # total, items

    result = service.list_by_bot(
        bot_uuid="bot-123",
        page=2,
        page_size=20,
    )

    assert isinstance(result, PaginatedResult)
    assert result.total == 100
    assert result.page == 2
    assert result.page_size == 20
    assert result.items == []


def test_list_by_time_range_delegates_to_repo(service, mock_repo):
    """Test list_by_time_range delegates to repository."""
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 2)
    mock_repo.list_by_time_range.return_value = []

    result = service.list_by_time_range(
        start_time=start,
        end_time=end,
        bot_uuid="bot-123",
    )

    mock_repo.list_by_time_range.assert_called_once_with(
        start_time=start,
        end_time=end,
        bot_uuid="bot-123",
    )
    assert result == []


# ============== Edge Cases ==============


class TestEdgeCases:
    """Edge cases covering empty/null params, no-op updates, empty results."""

    def test_create_session_without_trace_id_has_empty_context(
        self, service, mock_repo
    ):
        """When trace_id is None, context should be None (not empty dict)."""
        mock_repo.insert_session.return_value = 1

        service.create_session(
            bot_uuid="bot-123",
            invoker="user-1",
            req={},
            device_uuid="device-456",
            tenant="test-tenant",
        )

        call_kwargs = mock_repo.insert_session.call_args.kwargs
        # trace_id not provided -> context should be None
        assert call_kwargs.get("context") is None

    def test_create_session_with_trace_id_sets_context(self, service, mock_repo):
        """When trace_id is provided, context should contain it."""
        mock_repo.insert_session.return_value = 1

        service.create_session(
            bot_uuid="bot-123",
            invoker="user-1",
            req={},
            device_uuid="device-456",
            tenant="test-tenant",
            trace_id="trace-abc",
        )

        call_kwargs = mock_repo.insert_session.call_args.kwargs
        assert call_kwargs["context"] == {"trace_id": "trace-abc"}

    def test_create_session_with_empty_req(self, service, mock_repo):
        """req={} is valid and should be passed through."""
        mock_repo.insert_session.return_value = 1

        session_id = service.create_session(
            bot_uuid="bot-123",
            invoker="user-1",
            req={},
            device_uuid="device-456",
            tenant="test-tenant",
        )

        assert session_id.startswith("SESSION-")

    def test_mark_running_without_context(self, service, mock_repo):
        """When context is None, only update_status should be called."""
        service.mark_running("SESSION-test-001")

        mock_repo.update_status.assert_called_once_with(
            session_id="SESSION-test-001",
            status=SessionStatus.RUNNING.value,
        )
        # update_context should NOT be called when context is None
        mock_repo.update_context.assert_not_called()

    def test_mark_running_with_context_calls_update_context(self, service, mock_repo):
        """When context is given, both update_status and update_context called."""
        service.mark_running(
            "SESSION-test-001",
            context={"foo": "bar"},
        )

        mock_repo.update_status.assert_called_once()
        mock_repo.update_context.assert_called_once_with(
            session_id="SESSION-test-001",
            context={"foo": "bar"},
        )

    def test_mark_completed_with_only_result(self, service, mock_repo):
        """mark_completed with just result should work."""
        service.mark_completed(
            "SESSION-test-001",
            result={"output": "ok"},
        )

        mock_repo.update_context.assert_called_once_with(
            session_id="SESSION-test-001",
            result={"output": "ok"},
            context=None,
            err_msg=None,
        )
        mock_repo.update_status.assert_called_once()

    def test_mark_completed_with_all_params(self, service, mock_repo):
        """mark_completed with result, context, err_msg."""
        service.mark_completed(
            "SESSION-test-001",
            result={"output": "ok"},
            context={"step": "done"},
            err_msg="partial warning",
        )

        mock_repo.update_context.assert_called_once_with(
            session_id="SESSION-test-001",
            result={"output": "ok"},
            context={"step": "done"},
            err_msg="partial warning",
        )

    def test_mark_failed_without_result_or_context(self, service, mock_repo):
        """mark_failed with just err_msg."""
        service.mark_failed(
            "SESSION-test-001",
            err_msg="timeout",
        )

        mock_repo.update_context.assert_called_once_with(
            session_id="SESSION-test-001",
            result=None,
            context=None,
            err_msg="timeout",
        )
        mock_repo.update_status.assert_called_once_with(
            session_id="SESSION-test-001",
            status=SessionStatus.FAILED.value,
        )

    def test_update_context_noop_does_not_call_repo(self, service, mock_repo):
        """When all params are None, update_context should return early."""
        service.update_context("SESSION-test-001")

        mock_repo.update_context.assert_not_called()

    def test_list_by_bot_empty_result(self, service, mock_repo):
        """list_by_bot with no results returns PaginatedResult with empty items."""
        mock_repo.list_by_bot_uuid.return_value = (0, [])

        result = service.list_by_bot(
            bot_uuid="bot-123",
            page=1,
            page_size=50,
        )

        assert isinstance(result, PaginatedResult)
        assert result.total == 0
        assert result.items == []

    def test_get_by_session_id_not_found_returns_none(self, service, mock_repo):
        """get_by_session_id should return None when not found."""
        mock_repo.get_by_session_id.return_value = None

        result = service.get_by_session_id("SESSION-nonexistent")
        assert result is None

    def test_get_by_session_id_found(self, service, mock_repo, sample_session_record):
        """get_by_session_id should return the record when found."""
        mock_repo.get_by_session_id.return_value = sample_session_record

        result = service.get_by_session_id("SESSION-test-001")
        assert result is not None
        assert result.session_id == "SESSION-test-001"
        assert result.status == SessionStatus.COMPLETED.value

    def test_list_by_time_range_with_bot_filter(self, service, mock_repo):
        """list_by_time_range should pass bot_uuid filter to repo."""
        mock_repo.list_by_time_range.return_value = []

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)
        service.list_by_time_range(
            start_time=start,
            end_time=end,
            bot_uuid="bot-123",
        )

        mock_repo.list_by_time_range.assert_called_once_with(
            start_time=start,
            end_time=end,
            bot_uuid="bot-123",
        )

    def test_list_by_time_range_without_bot_filter(self, service, mock_repo):
        """list_by_time_range without bot_uuid should pass None."""
        mock_repo.list_by_time_range.return_value = []

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)
        service.list_by_time_range(
            start_time=start,
            end_time=end,
        )

        mock_repo.list_by_time_range.assert_called_once_with(
            start_time=start,
            end_time=end,
            bot_uuid=None,
        )

    def test_list_by_bot_device_invoker_with_device(self, service, mock_repo):
        """list_by_bot_device_invoker with device_uuid."""
        mock_repo.list_by_bot_device_invoker.return_value = []

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)
        service.list_by_bot_device_invoker(
            bot_uuid="bot-123",
            invoker="user-1",
            start_time=start,
            end_time=end,
            device_uuid="device-456",
        )

        mock_repo.list_by_bot_device_invoker.assert_called_once_with(
            bot_uuid="bot-123",
            device_uuid="device-456",
            invoker="user-1",
            start_time=start,
            end_time=end,
        )

    def test_list_by_bot_device_invoker_without_device(self, service, mock_repo):
        """list_by_bot_device_invoker without device_uuid passes None."""
        mock_repo.list_by_bot_device_invoker.return_value = []

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)
        service.list_by_bot_device_invoker(
            bot_uuid="bot-123",
            invoker="user-1",
            start_time=start,
            end_time=end,
        )

        mock_repo.list_by_bot_device_invoker.assert_called_once_with(
            bot_uuid="bot-123",
            device_uuid=None,
            invoker="user-1",
            start_time=start,
            end_time=end,
        )

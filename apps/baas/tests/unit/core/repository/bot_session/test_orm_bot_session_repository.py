"""OrmBotSessionRepository unit tests.

Uses pytest + MagicMock SQLAlchemy ORM session pattern matching the existing
test_orm_bot_run_repository.py.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.bot_session import (
    BotSessionRecord,
    OrmBotSessionRepository,
)

# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture(autouse=True)
def mock_get_current_env(monkeypatch):
    """Mock get_current_env to return 'dev' for all tests."""
    monkeypatch.setenv("SERVER_ENV", "dev")


@pytest.fixture
def repository(mock_database):
    """Create OrmBotSessionRepository with mocked database."""
    return OrmBotSessionRepository(mock_database)


# ==================== Model Helpers ====================

NOW = datetime(2026, 5, 23, 12, 0, 0)


def _make_mock_model(**kwargs):
    """Create a mock BotSessionModel with to_record() support.

    By default all fields are populated with sensible defaults.  Override
    via keyword arguments.
    """
    defaults: dict = {
        "id": 1,
        "gmt_create": NOW,
        "gmt_modified": NOW,
        "bot_uuid": "bot-001",
        "invoker": "user-001",
        "session_id": "sess-001",
        "req": None,
        "result": None,
        "err_msg": None,
        "context": None,
        "status": "PENDING",
        "device_uuid": "dev-001",
        "env": "dev",
        "tenant": "my_tenant",
    }
    defaults.update(kwargs)

    class FakeModel:
        pass

    model = FakeModel()
    for k, v in defaults.items():
        object.__setattr__(model, k, v)

    def _to_record():
        return BotSessionRecord(
            id=model.id,
            gmt_create=model.gmt_create,
            gmt_modified=model.gmt_modified,
            bot_uuid=model.bot_uuid,
            invoker=model.invoker,
            session_id=model.session_id,
            req=model.req,
            result=model.result,
            err_msg=model.err_msg,
            context=model.context,
            status=model.status,
            device_uuid=model.device_uuid,
            env=model.env,
            tenant=model.tenant,
        )

    model.to_record = MagicMock(side_effect=_to_record)
    return model


# ==================== Insert Tests ====================


class TestInsertSession:
    """Tests for OrmBotSessionRepository.insert_session()."""

    def test_insert_returns_lastrowid(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()

        model = _make_mock_model(id=42, session_id="sess-001")

        # Capture the model that gets added and set its id
        def _capture_add(model_arg):
            model_arg.id = model.id

        mock_session.add.side_effect = _capture_add

        result = repository.insert_session(
            bot_uuid="bot-001",
            invoker="user-001",
            session_id="sess-001",
            req={"cmd": "ping"},
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="dev-001",
            tenant="my_tenant",
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 1)

        repository.insert_session(
            bot_uuid="bot-002",
            invoker="user-002",
            session_id="sess-002",
            req={"action": "test"},
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="dev-002",
            tenant="my_tenant",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.bot_uuid == "bot-002"
        assert added_model.invoker == "user-002"
        assert added_model.session_id == "sess-002"
        assert added_model.status == "PENDING"
        assert added_model.device_uuid == "dev-002"
        assert added_model.env == "dev"
        assert added_model.tenant == "my_tenant"
        # req is JSON-serialized
        assert '"action": "test"' in added_model.req
        assert added_model.result is None
        assert added_model.err_msg is None
        assert added_model.context is None

    def test_insert_with_all_fields(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 1)

        repository.insert_session(
            bot_uuid="bot-003",
            invoker="user-003",
            session_id="sess-003",
            req={"cmd": "full"},
            result={"out": "init"},
            err_msg=None,
            context={"trace": "xyz"},
            status="RUNNING",
            device_uuid="dev-003",
            tenant="t3",
        )

        added_model = mock_session.add.call_args[0][0]
        assert '"cmd": "full"' in added_model.req
        assert '"out": "init"' in added_model.result
        assert '"trace": "xyz"' in added_model.context
        assert added_model.status == "RUNNING"

    def test_insert_none_req_context_works(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 99)

        result = repository.insert_session(
            bot_uuid="bot-004",
            invoker="user-004",
            session_id="sess-004",
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="dev-004",
            tenant="t4",
        )

        assert result == 99
        added_model = mock_session.add.call_args[0][0]
        assert added_model.req is None
        assert added_model.result is None
        assert added_model.context is None


# ==================== GetById Tests ====================


class TestGetById:
    """Tests for OrmBotSessionRepository.get_by_id()."""

    def test_found(self, repository, mock_session):
        mock_model = _make_mock_model(id=1, session_id="sess-001")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(1)

        assert result is not None
        assert isinstance(result, BotSessionRecord)
        assert result.id == 1
        assert result.session_id == "sess-001"
        mock_model.to_record.assert_called_once()
        mock_session.query.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999)

        assert result is None

    def test_with_json_fields(self, repository, mock_session):
        mock_model = _make_mock_model(
            id=2,
            req={"in": "data"},
            result={"out": "data"},
            context={"ctx": "val"},
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(2)

        assert result.req == {"in": "data"}
        assert result.result == {"out": "data"}
        assert result.context == {"ctx": "val"}


# ==================== GetBySessionId Tests ====================


class TestGetBySessionId:
    """Tests for OrmBotSessionRepository.get_by_session_id()."""

    def test_found(self, repository, mock_session):
        mock_model = _make_mock_model(id=5, session_id="sess-abc", status="SUCCESS")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_session_id("sess-abc")

        assert result is not None
        assert result.id == 5
        assert result.session_id == "sess-abc"
        assert result.status == "SUCCESS"

        # Verify filter was called with session_id + env
        query = mock_session.query.return_value
        query.filter.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_session_id("nonexistent")

        assert result is None


# ==================== UpdateResult Tests ====================


class TestUpdateResult:
    """Tests for OrmBotSessionRepository.update_result()."""

    def test_update_with_result_and_status(self, repository, mock_session):
        repository.update_result(
            session_id="sess-001",
            result={"output": "done"},
            err_msg=None,
            status="SUCCESS",
        )

        update_call = mock_session.query.return_value.filter.return_value.update
        update_call.assert_called_once()
        call_kwargs = update_call.call_args
        update_dict = call_kwargs[0][0]
        assert '"output": "done"' in update_dict["result"]
        assert update_dict["err_msg"] is None
        assert update_dict["status"] == "SUCCESS"
        assert "gmt_modified" in update_dict
        assert call_kwargs[1]["synchronize_session"] is False

    def test_update_with_error(self, repository, mock_session):
        repository.update_result(
            session_id="sess-002",
            result=None,
            err_msg="Something went wrong",
            status="FAILED",
        )

        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        assert update_dict["result"] is None
        assert update_dict["err_msg"] == "Something went wrong"
        assert update_dict["status"] == "FAILED"

    def test_update_with_none_result(self, repository, mock_session):
        repository.update_result(
            session_id="sess-003",
            result=None,
            err_msg=None,
            status="CANCELLED",
        )

        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        assert update_dict["result"] is None


# ==================== UpdateStatus Tests ====================


class TestUpdateStatus:
    """Tests for OrmBotSessionRepository.update_status()."""

    def test_update_status_to_running(self, repository, mock_session):
        repository.update_status(session_id="sess-001", status="RUNNING")

        update_call = mock_session.query.return_value.filter.return_value.update
        update_call.assert_called_once()
        update_dict = update_call.call_args[0][0]
        assert update_dict["status"] == "RUNNING"
        assert "gmt_modified" in update_dict
        assert update_call.call_args[1]["synchronize_session"] is False

    def test_update_status_to_failed(self, repository, mock_session):
        repository.update_status(session_id="sess-002", status="FAILED")

        update_dict = (
            mock_session.query.return_value.filter.return_value.update.call_args[0][0]
        )
        assert update_dict["status"] == "FAILED"


# ==================== UpdateContext Tests ====================


class TestUpdateContext:
    """Tests for OrmBotSessionRepository.update_context()."""

    def test_update_only_context(self, repository, mock_session):
        # First query: fetch existing context (returns None context)
        mock_existing = MagicMock()
        mock_existing.context = None
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_existing
        )

        repository.update_context(
            session_id="sess-001",
            context={"new_key": "new_val"},
            result=None,
            err_msg=None,
        )

        # Two query calls: 1) SELECT context, 2) UPDATE
        assert mock_session.query.call_count == 2

        # Second query is the UPDATE
        update_call = mock_session.query.return_value.filter.return_value.update
        update_call.assert_called_once()
        update_dict = update_call.call_args[0][0]
        assert "context" in update_dict
        assert "gmt_modified" in update_dict
        merged = json.loads(update_dict["context"])
        assert merged == {"new_key": "new_val"}

    def test_update_context_merges_with_existing(self, repository, mock_session):
        mock_existing = MagicMock()
        mock_existing.context = '{"old_key": "old_val"}'
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_existing
        )

        repository.update_context(
            session_id="sess-002",
            context={"new_key": "new_val"},
        )

        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        merged = json.loads(update_dict["context"])
        assert merged == {"old_key": "old_val", "new_key": "new_val"}

    def test_update_context_with_existing_dict(self, repository, mock_session):
        mock_existing = MagicMock()
        mock_existing.context = {"old_key": "old_val"}
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_existing
        )

        repository.update_context(
            session_id="sess-003",
            context={"new_key": "new_val"},
        )

        update_dict = (
            mock_session.query.return_value.filter.return_value.update.call_args[0][0]
        )
        merged = json.loads(update_dict["context"])
        assert merged == {"old_key": "old_val", "new_key": "new_val"}

    def test_update_context_no_existing_row(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repository.update_context(
            session_id="sess-004",
            context={"fresh_key": "fresh_val"},
        )

        update_dict = (
            mock_session.query.return_value.filter.return_value.update.call_args[0][0]
        )
        merged = json.loads(update_dict["context"])
        assert merged == {"fresh_key": "fresh_val"}

    def test_update_context_with_result_and_err_msg(self, repository, mock_session):
        mock_existing = MagicMock()
        mock_existing.context = None
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_existing
        )

        repository.update_context(
            session_id="sess-005",
            context={"ctx": "val"},
            result={"out": "done"},
            err_msg=None,
        )

        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        assert '"out": "done"' in update_dict["result"]
        assert "err_msg" not in update_dict  # None → not added
        assert "context" in update_dict

    def test_update_context_only_result(self, repository, mock_session):
        # No context provided → no SELECT for existing context
        repository.update_context(
            session_id="sess-006",
            result={"out": "done"},
            err_msg=None,
        )

        # Only one query call (UPDATE, no SELECT for context)
        assert mock_session.query.call_count == 1
        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        assert '"out": "done"' in update_dict["result"]
        assert "context" not in update_dict
        assert "err_msg" not in update_dict

    def test_update_context_only_err_msg(self, repository, mock_session):
        repository.update_context(
            session_id="sess-007",
            err_msg="error occurred",
        )

        assert mock_session.query.call_count == 1
        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        assert update_dict["err_msg"] == "error occurred"

    def test_update_context_with_all_fields(self, repository, mock_session):
        mock_existing = MagicMock()
        mock_existing.context = '{"old": "data"}'
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_existing
        )

        repository.update_context(
            session_id="sess-008",
            context={"new": "ctx"},
            result={"out": "done"},
            err_msg="partial error",
        )

        assert mock_session.query.call_count == 2
        update_call = mock_session.query.return_value.filter.return_value.update
        update_dict = update_call.call_args[0][0]
        assert '"out": "done"' in update_dict["result"]
        assert update_dict["err_msg"] == "partial error"
        assert "context" in update_dict

    def test_update_context_with_explicit_none_context_no_merge(
        self, repository, mock_session
    ):
        """When context is explicitly None, it should not trigger merge logic."""
        repository.update_context(
            session_id="sess-010",
            context=None,
            result={"out": "data"},
        )

        # Only one query (UPDATE with result only, no SELECT for context)
        assert mock_session.query.call_count == 1
        update_dict = (
            mock_session.query.return_value.filter.return_value.update.call_args[0][0]
        )
        assert "context" not in update_dict


# ==================== ListByBotUuid Tests ====================


class TestListByBotUuid:
    """Tests for OrmBotSessionRepository.list_by_bot_uuid()."""

    def test_returns_paginated_results(self, repository, mock_session):
        mock_model_1 = _make_mock_model(id=1, session_id="sess-1")
        mock_model_2 = _make_mock_model(id=2, session_id="sess-2")

        # The ORM source captures query().filter() into a local 'query' var,
        # then chains with_entities().scalar() and order_by().offset().limit().all()
        filtered_query = mock_session.query.return_value.filter.return_value

        with_entities_mock = MagicMock()
        with_entities_mock.scalar.return_value = 25
        filtered_query.with_entities.return_value = with_entities_mock

        filtered_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            mock_model_1,
            mock_model_2,
        ]

        total, items = repository.list_by_bot_uuid(
            bot_uuid="bot-001", page=1, page_size=10
        )

        assert total == 25
        assert len(items) == 2
        assert items[0].id == 1
        assert items[1].id == 2
        mock_model_1.to_record.assert_called_once()
        mock_model_2.to_record.assert_called_once()

        filtered_query.order_by.return_value.offset.assert_called_once_with(0)
        filtered_query.order_by.return_value.offset.return_value.limit.assert_called_once_with(
            10
        )

    def test_pagination_page_2(self, repository, mock_session):
        mock_model = _make_mock_model(id=11)

        filtered_query = mock_session.query.return_value.filter.return_value
        with_entities_mock = MagicMock()
        with_entities_mock.scalar.return_value = 100
        filtered_query.with_entities.return_value = with_entities_mock
        filtered_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            mock_model
        ]

        total, items = repository.list_by_bot_uuid(
            bot_uuid="bot-002", page=2, page_size=10
        )

        assert total == 100
        assert len(items) == 1
        filtered_query.order_by.return_value.offset.assert_called_once_with(10)

    def test_empty_results(self, repository, mock_session):
        filtered_query = mock_session.query.return_value.filter.return_value
        with_entities_mock = MagicMock()
        with_entities_mock.scalar.return_value = 0
        filtered_query.with_entities.return_value = with_entities_mock
        filtered_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_by_bot_uuid(bot_uuid="bot-003")

        assert total == 0
        assert items == []

    def test_default_page_and_size(self, repository, mock_session):
        filtered_query = mock_session.query.return_value.filter.return_value
        with_entities_mock = MagicMock()
        with_entities_mock.scalar.return_value = 5
        filtered_query.with_entities.return_value = with_entities_mock
        filtered_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        repository.list_by_bot_uuid(bot_uuid="bot-004")

        filtered_query.order_by.return_value.offset.assert_called_once_with(0)
        filtered_query.order_by.return_value.offset.return_value.limit.assert_called_once_with(
            20
        )


# ==================== ListBySessionIds Tests ====================


class TestListBySessionIds:
    """Tests for OrmBotSessionRepository.list_by_session_ids()."""

    def test_returns_matching_sessions(self, repository, mock_session):
        mock_model_1 = _make_mock_model(id=1, session_id="sess-1")
        mock_model_2 = _make_mock_model(id=2, session_id="sess-2")
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_model_1,
            mock_model_2,
        ]

        result = repository.list_by_session_ids(["sess-1", "sess-2"])

        assert len(result) == 2
        assert result[0].session_id == "sess-1"
        assert result[1].session_id == "sess-2"
        mock_model_1.to_record.assert_called_once()
        mock_model_2.to_record.assert_called_once()

    def test_single_session_id(self, repository, mock_session):
        mock_model = _make_mock_model(id=3, session_id="sess-3")
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_model
        ]

        result = repository.list_by_session_ids(["sess-3"])

        assert len(result) == 1
        assert result[0].session_id == "sess-3"

    def test_empty_list_returns_empty(self, repository, mock_session):
        result = repository.list_by_session_ids([])

        assert result == []
        # Should NOT call query for empty list
        mock_session.query.assert_not_called()

    def test_returns_empty_when_no_matches(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_by_session_ids(["nonexistent"])

        assert result == []


# ==================== ListByTimeRange Tests ====================


class TestListByTimeRange:
    """Tests for OrmBotSessionRepository.list_by_time_range()."""

    def test_without_bot_filter(self, repository, mock_session):
        mock_model_1 = _make_mock_model(id=1, session_id="sess-1")
        mock_model_2 = _make_mock_model(id=2, session_id="sess-2")
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_model_1,
            mock_model_2,
        ]
        start = datetime(2026, 5, 1, tzinfo=UTC)
        end = datetime(2026, 5, 23, tzinfo=UTC)

        result = repository.list_by_time_range(start_time=start, end_time=end)

        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].id == 2
        mock_model_1.to_record.assert_called_once()
        mock_model_2.to_record.assert_called_once()

    def test_with_bot_filter(self, repository, mock_session):
        mock_model = _make_mock_model(id=1)
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_model
        ]
        start = datetime(2026, 5, 1, tzinfo=UTC)
        end = datetime(2026, 5, 23, tzinfo=UTC)

        result = repository.list_by_time_range(
            start_time=start, end_time=end, bot_uuid="bot-001"
        )

        assert len(result) == 1
        assert result[0].id == 1

    def test_empty_range(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        start = datetime(2026, 5, 1, tzinfo=UTC)
        end = datetime(2026, 5, 1, tzinfo=UTC)

        result = repository.list_by_time_range(start_time=start, end_time=end)

        assert result == []


# ==================== ListByBotDeviceInvoker Tests ====================


class TestListByBotDeviceInvoker:
    """Tests for OrmBotSessionRepository.list_by_bot_device_invoker()."""

    def test_with_device_uuid(self, repository, mock_session):
        mock_model = _make_mock_model(id=1, session_id="sess-1")
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_model
        ]
        start = datetime(2026, 5, 1, tzinfo=UTC)
        end = datetime(2026, 5, 23, tzinfo=UTC)

        result = repository.list_by_bot_device_invoker(
            bot_uuid="bot-001",
            device_uuid="dev-001",
            invoker="user-001",
            start_time=start,
            end_time=end,
        )

        assert len(result) == 1
        assert result[0].id == 1
        mock_model.to_record.assert_called_once()

    def test_without_device_uuid(self, repository, mock_session):
        mock_model_1 = _make_mock_model(id=1, device_uuid="dev-002")
        mock_model_2 = _make_mock_model(id=2, device_uuid="dev-003")
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_model_1,
            mock_model_2,
        ]
        start = datetime(2026, 5, 1, tzinfo=UTC)
        end = datetime(2026, 5, 23, tzinfo=UTC)

        result = repository.list_by_bot_device_invoker(
            bot_uuid="bot-002",
            device_uuid=None,
            invoker="user-002",
            start_time=start,
            end_time=end,
        )

        assert len(result) == 2

    def test_empty_results(self, repository, mock_session):
        # No device_uuid → one .filter() chain, ending with .order_by().all()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        start = datetime(2026, 5, 1, tzinfo=UTC)
        end = datetime(2026, 5, 23, tzinfo=UTC)

        result = repository.list_by_bot_device_invoker(
            bot_uuid="bot-003",
            device_uuid="dev-001",
            invoker="user-003",
            start_time=start,
            end_time=end,
        )

        assert result == []


# ==================== CountActiveSessionsByDevice Tests ====================


class TestCountActiveSessionsByDevice:
    """Tests for OrmBotSessionRepository.count_active_sessions_by_device()."""

    def test_returns_count(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 5

        count = repository.count_active_sessions_by_device(
            device_uuid="dev-001", tenant="my_tenant"
        )

        assert count == 5
        mock_session.query.assert_called_once()

    def test_returns_zero(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        count = repository.count_active_sessions_by_device(
            device_uuid="dev-002", tenant="t2"
        )

        assert count == 0

    def test_returns_zero_when_scalar_is_none(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        count = repository.count_active_sessions_by_device(
            device_uuid="dev-003", tenant="t3"
        )

        assert count == 0


# ==================== Integration/Edge Case Tests ====================


class TestWithOrmSessionIntegration:
    """Tests verifying the @with_orm_session decorator lifecycle."""

    def test_decorator_opens_and_closes_session(
        self, mock_database, mock_session, monkeypatch
    ):
        monkeypatch.setenv("SERVER_ENV", "dev")
        repo = OrmBotSessionRepository(mock_database)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            _make_mock_model(id=1)
        )

        repo.get_by_id(1)

        mock_database.orm_session.assert_called_once()

    def test_session_is_cleaned_up_after_method(
        self, mock_database, mock_session, monkeypatch
    ):
        monkeypatch.setenv("SERVER_ENV", "dev")
        repo = OrmBotSessionRepository(mock_database)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            _make_mock_model(id=1)
        )

        repo.get_by_id(1)

        orm_ctx = mock_database.orm_session.return_value
        orm_ctx.__enter__.assert_called_once()
        orm_ctx.__exit__.assert_called_once()


class TestRepositoryInit:
    """Tests for repository initialization."""

    def test_database_is_stored(self, mock_database):
        repo = OrmBotSessionRepository(mock_database)
        assert repo._database is mock_database


class TestBotSessionRecordDataclass:
    """Tests for BotSessionRecord dataclass."""

    def test_create_record(self):
        record = BotSessionRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            bot_uuid="bot-001",
            invoker="user-001",
            session_id="sess-001",
            req={"cmd": "test"},
            result=None,
            err_msg=None,
            context={"key": "val"},
            status="PENDING",
            device_uuid="dev-001",
            env="dev",
            tenant="my_tenant",
        )

        assert record.id == 1
        assert record.bot_uuid == "bot-001"
        assert record.status == "PENDING"

    def test_record_uses_slots(self):
        record = BotSessionRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            bot_uuid="b",
            invoker="u",
            session_id="s",
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="d",
            env="dev",
            tenant="t",
        )

        with pytest.raises(AttributeError):
            _ = record.__dict__


class TestMethodRoundTrips:
    """Tests covering multiple methods in sequence on the same repository."""

    def test_insert_then_get_by_id(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 42)

        mock_model = _make_mock_model(id=42, session_id="sess-abc")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        new_id = repository.insert_session(
            bot_uuid="bot-x",
            invoker="user-x",
            session_id="sess-abc",
            req={"a": 1},
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="dev-x",
            tenant="t",
        )
        assert new_id == 42

        session = repository.get_by_id(42)
        assert session is not None
        assert session.id == 42

    def test_insert_then_get_by_session_id(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 1)

        mock_model = _make_mock_model(id=1, session_id="sess-xyz")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repository.insert_session(
            bot_uuid="bot-y",
            invoker="user-y",
            session_id="sess-xyz",
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="dev-y",
            tenant="t",
        )

        result = repository.get_by_session_id("sess-xyz")
        assert result is not None
        assert result.session_id == "sess-xyz"
        assert result.id == 1

    def test_insert_then_update_result_then_get(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 99)

        mock_model = _make_mock_model(id=99, session_id="sess-upd", status="SUCCESS")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repository.insert_session(
            bot_uuid="bot-z",
            invoker="user-z",
            session_id="sess-upd",
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid="dev-z",
            tenant="t",
        )
        repository.update_result(
            session_id="sess-upd",
            result={"ok": True},
            status="SUCCESS",
        )
        session = repository.get_by_id(99)
        assert session is not None
        assert session.status == "SUCCESS"

    def test_update_status_then_count_active(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 3

        repository.update_status(session_id="sess-status", status="RUNNING")
        count = repository.count_active_sessions_by_device(
            device_uuid="dev-001", tenant="my_tenant"
        )

        assert count == 3

"""OrmBotRunRepository unit tests.

Uses pytest + MagicMock pattern matching the existing
test_zdas_bot_run_repository.py.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.bot_run import OrmBotRunRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def repository(mock_database):
    return OrmBotRunRepository(database=mock_database)


# ---------------------------------------------------------------------------
# TestInsertRun
# ---------------------------------------------------------------------------


class TestInsertRun:
    def test_insert_returns_run_id(self, repository, mock_session):
        result = repository.insert_run(
            run_id="run-abc-123",
            bot_id="bot-001",
            api_key_prefix="ak_test",
            message_long="User message here",
            metadata={"source": "web"},
        )

        assert result == "run-abc-123"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repository, mock_session):
        repository.insert_run(
            run_id="run-abc-123",
            bot_id="bot-001",
            api_key_prefix="ak_test",
            message_long="User message here",
            metadata={"source": "web"},
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.run_id == "run-abc-123"
        assert added_model.bot_id == "bot-001"
        assert added_model.api_key_prefix == "ak_test"
        assert added_model.message == "User message here"  # truncated to 256
        assert added_model.message_long == "User message here"
        assert added_model.metadata_ == json.dumps(
            {"source": "web"}, ensure_ascii=False
        )
        assert added_model.status == "PENDING"

    def test_insert_truncates_long_message(self, repository, mock_session):
        long_message = "x" * 300
        repository.insert_run(
            run_id="run-long",
            bot_id="bot-001",
            api_key_prefix="ak_",
            message_long=long_message,
            metadata=None,
        )

        added_model = mock_session.add.call_args[0][0]
        assert len(added_model.message) == 256
        assert added_model.message == long_message[:256]
        assert added_model.message_long == long_message

    def test_insert_with_none_metadata(self, repository, mock_session):
        repository.insert_run(
            run_id="run-minimal",
            bot_id="bot-002",
            api_key_prefix="ak_",
            message_long="minimal",
            metadata=None,
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.metadata_ is None

    def test_insert_with_empty_message(self, repository, mock_session):
        repository.insert_run(
            run_id="run-empty",
            bot_id="bot-003",
            api_key_prefix="ak_",
            message_long="",
            metadata=None,
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.message == ""
        assert added_model.message_long == ""


# ---------------------------------------------------------------------------
# TestGetByRunId
# ---------------------------------------------------------------------------


class TestGetByRunId:
    def test_found(self, repository, mock_session):
        mock_model = MagicMock()
        mock_record = MagicMock()
        mock_model.to_record.return_value = mock_record
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_run_id("run-xyz")

        assert result is mock_record
        mock_model.to_record.assert_called_once()
        mock_session.query.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_run_id("nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# TestUpdateStatus
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    def test_update_to_completed(self, repository, mock_session):
        repository.update_status("run-001", "COMPLETED")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "COMPLETED"
        assert "gmt_modified" in update_dict

    def test_update_to_failed(self, repository, mock_session):
        repository.update_status("run-002", "FAILED")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "FAILED"

    def test_update_to_running(self, repository, mock_session):
        repository.update_status("run-003", "RUNNING")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "RUNNING"


# ---------------------------------------------------------------------------
# TestUpdateResult
# ---------------------------------------------------------------------------


class TestUpdateResult:
    def test_sets_completed_with_extra(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        repository.update_result(
            "run-001",
            "Response content here",
            {"tokens": 150, "model": "gpt-4"},
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "COMPLETED"
        assert update_dict["result_content"] == "Response content here"
        assert update_dict["result_content_long"] == "Response content here"
        assert update_dict["result_extra"] == json.dumps(
            {"tokens": 150, "model": "gpt-4"}, ensure_ascii=False
        )
        assert "completed_at" in update_dict
        assert "gmt_modified" in update_dict

    def test_sets_completed_with_none_extra(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        repository.update_result("run-002", "Done", None)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["result_extra"] is None

    def test_truncates_long_content(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        long_content = "y" * 300
        repository.update_result("run-003", long_content, None)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert len(update_dict["result_content"]) == 256
        assert update_dict["result_content"] == long_content[:256]
        assert update_dict["result_content_long"] == long_content

    def test_empty_content(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        repository.update_result("run-004", "", None)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["result_content"] == ""
        assert update_dict["result_content_long"] == ""

    def test_skips_when_already_terminal(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0
        repository.update_result("run-005", "late result", None)

        mock_session.query.return_value.filter.return_value.update.assert_called_once()


# ---------------------------------------------------------------------------
# TestUpdateError
# ---------------------------------------------------------------------------


class TestUpdateError:
    def test_sets_failed_with_error(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        repository.update_error("run-001", "Connection timeout")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "FAILED"
        assert update_dict["error"] == "Connection timeout"
        assert "completed_at" in update_dict
        assert "gmt_modified" in update_dict

    def test_sets_failed_with_empty_error(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        repository.update_error("run-002", "")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "FAILED"
        assert update_dict["error"] == ""

    def test_skips_when_already_terminal(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0
        repository.update_error("run-003", "late error")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()


# ---------------------------------------------------------------------------
# TestUpdateAborted
# ---------------------------------------------------------------------------


class TestUpdateAborted:
    def test_sets_aborted(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        repository.update_aborted("run-001")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "ABORTED"
        assert "completed_at" in update_dict
        assert "gmt_modified" in update_dict

    def test_raises_conflict_when_already_terminal(self, repository, mock_session):
        """rowcount==0 (terminal or not found) -> BotRunStatusConflictError."""
        from secbaas.community.api.bot_runtime import BotRunStatusConflictError

        mock_session.query.return_value.filter.return_value.update.return_value = 0
        with pytest.raises(BotRunStatusConflictError):
            repository.update_aborted("run-terminal")

    def test_raises_conflict_when_not_found(self, repository, mock_session):
        from secbaas.community.api.bot_runtime import BotRunStatusConflictError

        mock_session.query.return_value.filter.return_value.update.return_value = 0
        with pytest.raises(BotRunStatusConflictError) as exc:
            repository.update_aborted("nonexistent")
        assert exc.value.run_id == "nonexistent"
        assert exc.value.http_status == 409

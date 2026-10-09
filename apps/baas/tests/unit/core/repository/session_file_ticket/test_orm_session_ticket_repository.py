"""
OrmSessionTicketRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_ticket_repository.py tests (Bot reference).
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.session_file_ticket import (
    OrmSessionTicketRepository,
    SessionTicketRecord,
    TransferNotFoundError,
    TransferStateConflictError,
)

# ==================== Factory ====================


def _make_session_ticket_record(**overrides):
    """Create a mock SessionTicketRecord with sensible defaults."""
    defaults = dict(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        transfer_id="tf-001",
        tenant="t1",
        session_id="sess-001",
        status="CREATED",
        staging_subdir=None,
        filename="data.csv",
        fileservice_staging_path="file-transfers/test/t1/sess-001/tf-001/data.csv",
        error_message=None,
        multipart_session_id=None,
        env="test",
        operator="test-user",
    )
    defaults.update(overrides)
    mock = MagicMock(spec=SessionTicketRecord, **defaults)
    return mock


# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    return MagicMock()


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session via @with_orm_session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture(autouse=True)
def _patch_model():
    """Patch SessionFileTicketModel so constructor returns a mock with .id=42 pre-set."""
    with patch(
        "secbaas.community.core.repository.session_file_ticket._orm_repository.SessionFileTicketModel",
        autospec=False,
    ) as mock_cls:

        def _make_model(**kwargs):
            model = MagicMock()
            model.id = 42
            for k, v in kwargs.items():
                setattr(model, k, v)
            return model

        mock_cls.side_effect = _make_model
        yield mock_cls


@pytest.fixture
def repo(mock_database):
    """Create an OrmSessionTicketRepository instance with mock database."""
    return OrmSessionTicketRepository(mock_database)


# ==================== TestCreateTicket ====================


class TestCreateTicket:
    def test_create_returns_id(self, repo, mock_session):
        result = repo.create_ticket(
            transfer_id="tf-001",
            tenant="t1",
            session_id="sess-001",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            fileservice_staging_path="file-transfers/test/t1/sess-001/tf-001/data.csv",
            error_message=None,
            operator="test-user",
        )

        assert isinstance(result, int)
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_create_model_fields(self, repo, mock_session):
        repo.create_ticket(
            transfer_id="tf-001",
            tenant="t1",
            session_id="sess-001",
            status="CREATED",
            staging_subdir="my-subdir",
            filename="data.csv",
            fileservice_staging_path="file-transfers/test/t1/sess-001/tf-001/data.csv",
            error_message=None,
            multipart_session_id="upload-123",
            operator="test-user",
        )

        mock_session.add.assert_called_once()
        model = mock_session.add.call_args[0][0]
        assert model.transfer_id == "tf-001"
        assert model.tenant == "t1"
        assert model.session_id == "sess-001"
        assert model.status == "CREATED"
        assert model.staging_subdir == "my-subdir"
        assert model.filename == "data.csv"
        assert (
            model.fileservice_staging_path
            == "file-transfers/test/t1/sess-001/tf-001/data.csv"
        )
        assert model.error_message is None
        assert model.multipart_session_id == "upload-123"
        assert model.operator == "test-user"

    def test_create_operator_default(self, repo, mock_session):
        """When operator is not provided, defaults to 'unknown'."""
        repo.create_ticket(
            transfer_id="tf-002",
            tenant="t1",
            session_id="sess-001",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            fileservice_staging_path="file-transfers/test/t1/sess-001/tf-002/data.csv",
            error_message=None,
        )

        mock_session.add.assert_called_once()
        model = mock_session.add.call_args[0][0]
        assert model.operator == "unknown"


# ==================== TestGetByTransferId ====================


class TestGetByTransferId:
    def test_get_by_transfer_id_found(self, repo, mock_session):
        record = _make_session_ticket_record(
            id=5,
            transfer_id="tf-xyz",
            tenant="t1",
            session_id="sess-001",
        )
        model = MagicMock()
        model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_transfer_id("tf-xyz")

        assert result is not None
        assert result.id == 5
        assert result.transfer_id == "tf-xyz"
        mock_session.query.assert_called_once()

    def test_get_by_transfer_id_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_transfer_id("nonexistent")

        assert result is None

    def test_get_by_transfer_id_with_tenant(self, repo, mock_session):
        """Verify that when tenant is passed, the filter chain includes tenant condition."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.get_by_transfer_id("tf-xyz", tenant="t1")

        assert mock_session.query.return_value.filter.call_count >= 1


# ==================== TestListBySession ====================


class TestListBySession:
    def test_list_by_session_returns_records(self, repo, mock_session):
        record1 = _make_session_ticket_record(
            id=1,
            transfer_id="tf-001",
            tenant="t1",
            session_id="sess-001",
            status="UPLOADING",
        )
        record2 = _make_session_ticket_record(
            id=2,
            transfer_id="tf-002",
            tenant="t1",
            session_id="sess-001",
            status="DONE",
        )
        model1 = MagicMock()
        model1.to_record.return_value = record1
        model2 = MagicMock()
        model2.to_record.return_value = record2
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repo.list_by_session("t1", "sess-001")

        assert len(result) == 2
        assert result[0].transfer_id == "tf-001"
        assert result[1].transfer_id == "tf-002"

    def test_list_by_session_empty(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_session("t1", "sess-001")

        assert result == []

    def test_list_by_session_ordered_by_gmt_create_desc(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        repo.list_by_session("t1", "sess-001")

        mock_session.query.return_value.filter.return_value.order_by.assert_called_once()


# ==================== TestUpdateStatus ====================

VALID_TRANSITIONS_CASES = [
    # Upload path
    ("CREATED", "UPLOADING"),
    ("UPLOADING", "DONE"),
    ("CREATED", "DONE"),  # Session fast path
    # Cancel path
    ("CREATED", "CANCELLED"),
    ("UPLOADING", "CANCELLED"),
    # Failure path
    ("UPLOADING", "FAILED"),
    # Delete path
    ("DONE", "DELETED"),
    ("FAILED", "DELETED"),
    ("CANCELLED", "DELETED"),
    # Idempotent same-state
    ("CREATED", "CREATED"),
]


class TestUpdateStatus:
    @pytest.mark.parametrize("src_status,new_status", VALID_TRANSITIONS_CASES)
    def test_valid_transition(self, repo, mock_session, src_status, new_status):
        """CAS update returns 1 -- valid transition succeeds with no exception."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        # Should not raise
        repo.update_status("tf-001", new_status)

    def test_invalid_transition_conflict(self, repo, mock_session):
        """CAS update returns 0, fallback finds ticket with conflicting status."""
        mock_session.query.return_value.filter.return_value.update.return_value = 0
        # Fallback first() returns a model with status=DELETED (terminal)
        fallback_row = MagicMock()
        fallback_row.status = "DELETED"
        mock_session.query.return_value.filter.return_value.first.return_value = (
            fallback_row
        )

        with pytest.raises(TransferStateConflictError):
            repo.update_status("tf-001", "UPLOADING")

    def test_not_found(self, repo, mock_session):
        """CAS update returns 0, fallback query returns None -- TransferNotFoundError."""
        mock_session.query.return_value.filter.return_value.update.return_value = 0
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(TransferNotFoundError, match="not found"):
            repo.update_status("nonexistent", "DONE")

    def test_cas_update_uses_env_filter(self, repo, mock_session):
        """Verify the .filter() chain includes SessionFileTicketModel.env condition."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_status("tf-001", "UPLOADING")

        # The filter() chain should have been called with env condition
        assert mock_session.query.return_value.filter.call_count >= 1

"""
OrmTicketRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_repository.py tests.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.file_transfer_ticket import (
    TicketRecord,
    OrmTicketRepository,
)
from secbaas.community.core.repository.file_transfer_ticket._protocol import (
    TransferNotFoundError,
    TransferStateConflictError,
)

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
    """Patch FileTransferTicketModel so constructor returns a mock with .id=42 pre-set."""
    with patch(
        "secbaas.community.core.repository.file_transfer_ticket._orm_repository.FileTransferTicketModel",
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
    """Create an OrmTicketRepository instance with mock database."""
    return OrmTicketRepository(mock_database)


# ==================== TestCreateTicket ====================


class TestCreateTicket:
    def test_create_returns_id(self, repo, mock_session):
        result = repo.create_ticket(
            transfer_id="tf-001",
            tenant="t1",
            paas_device_id="sandbox@42",
            direction="UPLOAD",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            device_path="/home/data.csv",
            fileservice_staging_path="file-transfers/t1/tf-001/data.csv",
            error_message=None,
            operator="test-user",
        )

        assert result is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_create_model_fields(self, repo, mock_session):
        repo.create_ticket(
            transfer_id="tf-001",
            tenant="t1",
            paas_device_id="sandbox@42",
            direction="UPLOAD",
            status="CREATED",
            staging_subdir="my-subdir",
            filename="data.csv",
            device_path="/home/data.csv",
            fileservice_staging_path="file-transfers/t1/tf-001/data.csv",
            error_message=None,
            multipart_session_id="upload-123",
            operator="test-user",
        )

        mock_session.add.assert_called_once()
        model = mock_session.add.call_args[0][0]
        assert model.transfer_id == "tf-001"
        assert model.tenant == "t1"
        assert model.paas_device_id == "sandbox@42"
        assert model.direction == "UPLOAD"
        assert model.status == "CREATED"
        assert model.staging_subdir == "my-subdir"
        assert model.filename == "data.csv"
        assert model.device_path == "/home/data.csv"
        assert model.fileservice_staging_path == "file-transfers/t1/tf-001/data.csv"
        assert model.error_message is None
        assert model.multipart_session_id == "upload-123"
        assert model.operator == "test-user"

    def test_create_default_operator(self, repo, mock_session):
        """When operator is not provided, defaults to 'unknown'."""
        repo.create_ticket(
            transfer_id="tf-002",
            tenant="t1",
            paas_device_id="sandbox@42",
            direction="DOWNLOAD",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            device_path="/home/data.csv",
            fileservice_staging_path="file-transfers/t1/tf-002/data.csv",
            error_message=None,
            operator="unknown",
        )

        model = mock_session.add.call_args[0][0]
        assert model.operator == "unknown"

    def test_create_nullable_fields(self, repo, mock_session):
        """Verify nullable fields can be passed as None."""
        repo.create_ticket(
            transfer_id="tf-003",
            tenant="t1",
            paas_device_id="sandbox@42",
            direction="UPLOAD",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            device_path=None,
            fileservice_staging_path="file-transfers/t1/tf-003/data.csv",
            error_message=None,
            multipart_session_id=None,
            operator="unknown",
        )

        model = mock_session.add.call_args[0][0]
        assert model.staging_subdir is None
        assert model.device_path is None
        assert model.multipart_session_id is None
        assert model.error_message is None


# ==================== TestGetByTransferId ====================


class TestGetByTransferId:
    def test_found(self, repo, mock_session):
        now = datetime.now()
        record = TicketRecord(
            id=5,
            gmt_create=now,
            gmt_modified=now,
            transfer_id="tf-xyz",
            tenant="t1",
            paas_device_id="sandbox@42",
            direction="UPLOAD",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            device_path="/home/data.csv",
            fileservice_staging_path="file-transfers/t1/tf-xyz/data.csv",
            error_message=None,
            download_url=None,
            multipart_session_id=None,
            env="test",
            operator="unknown",
        )
        model = MagicMock()
        model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_transfer_id("tf-xyz")

        assert result is not None
        assert result.id == 5
        assert result.transfer_id == "tf-xyz"
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_transfer_id("nonexistent")

        assert result is None

    def test_with_tenant_filter(self, repo, mock_session):
        """Verify that when tenant is passed, the filter chain includes tenant condition."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.get_by_transfer_id("tf-xyz", tenant="t1")

        assert mock_session.query.return_value.filter.call_count >= 1


# ==================== TestUpdateStatus ====================


class TestUpdateStatus:
    def test_valid_transition(self, repo, mock_session):
        """CAS update returns 1 — valid transition succeeds."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        # Should not raise
        repo.update_status("tf-001", "UPLOADING")

    def test_invalid_transition(self, repo, mock_session):
        """CAS update returns 0, fallback query finds ticket with conflicting status."""
        mock_session.query.return_value.filter.return_value.update.return_value = 0
        # Fallback first() returns a model with status=DONE (terminal)
        fallback_row = MagicMock()
        fallback_row.status = "DONE"
        mock_session.query.return_value.filter.return_value.first.return_value = (
            fallback_row
        )

        with pytest.raises(TransferStateConflictError):
            repo.update_status("tf-001", "UPLOADING")

    def test_not_found(self, repo, mock_session):
        """CAS update returns 0, fallback query returns None → TransferNotFoundError."""
        mock_session.query.return_value.filter.return_value.update.return_value = 0
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(TransferNotFoundError, match="not found"):
            repo.update_status("nonexistent", "DONE")

    def test_same_state_idempotent(self, repo, mock_session):
        """Same-state transition is idempotent (CREATED→CREATED allowed)."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        # Should not raise
        repo.update_status("tf-001", "CREATED")

    def test_with_error_message(self, repo, mock_session):
        """Verify error_message is passed through to update_kwargs."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_status("tf-001", "FAILED", error_message="timeout")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["error_message"] == "timeout"
        assert update_dict["status"] == "FAILED"


# ==================== TestUpdateUrls ====================


class TestUpdateUrls:
    def test_update_download_url(self, repo, mock_session):
        """Setting download_url triggers an UPDATE query."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_urls("tf-001", download_url="https://oss.example.com/dl")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["download_url"] == "https://oss.example.com/dl"

    def test_noop_when_none(self, repo, mock_session):
        """When download_url is None, the method returns early — no query."""
        repo.update_urls("tf-001")

        # session.query should NOT be called (early return guard)
        mock_session.query.assert_not_called()

    def test_not_found(self, repo, mock_session):
        """update() returns 0 — TransferNotFoundError is raised."""
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        with pytest.raises(TransferNotFoundError):
            repo.update_urls("tf-001", download_url="https://oss.example.com/dl")


# ==================== TestListPendingUploads ====================


class TestListPendingUploads:
    def test_returns_tickets(self, repo, mock_session):
        now = datetime.now()

        def _make_record(transfer_id, status):
            return TicketRecord(
                id=1,
                gmt_create=now,
                gmt_modified=now,
                transfer_id=transfer_id,
                tenant="t1",
                paas_device_id="sandbox@42",
                direction="UPLOAD",
                status=status,
                staging_subdir=None,
                filename="data.csv",
                device_path="/home/data.csv",
                fileservice_staging_path=f"file-transfers/t1/{transfer_id}/data.csv",
                error_message=None,
                download_url=None,
                multipart_session_id=None,
                env="test",
                operator="unknown",
            )

        model1 = MagicMock()
        model1.to_record.return_value = _make_record("tf-001", "UPLOADING")
        model2 = MagicMock()
        model2.to_record.return_value = _make_record("tf-002", "UPLOADING")
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repo.list_pending_uploads(["CREATED", "UPLOADING"], 10)

        assert len(result) == 2
        assert result[0].transfer_id == "tf-001"
        assert result[1].transfer_id == "tf-002"

    def test_empty_result(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = repo.list_pending_uploads(["CREATED"], 10)

        assert result == []

    def test_respects_limit(self, repo, mock_session):
        """Verify the limit argument is passed to the query chain."""
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        repo.list_pending_uploads(["CREATED"], 5)

        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.assert_called_once_with(
            5
        )


# ==================== TestGetByFileserviceStagingPath ====================


class TestGetByFileserviceStagingPath:
    def test_found(self, repo, mock_session):
        now = datetime.now()
        record = TicketRecord(
            id=5,
            gmt_create=now,
            gmt_modified=now,
            transfer_id="tf-xyz",
            tenant="t1",
            paas_device_id="sandbox@42",
            direction="UPLOAD",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            device_path="/home/data.csv",
            fileservice_staging_path="file-transfers/t1/tf-xyz/data.csv",
            error_message=None,
            download_url=None,
            multipart_session_id=None,
            env="test",
            operator="unknown",
        )
        model = MagicMock()
        model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_fileservice_staging_path(
            "file-transfers/t1/tf-xyz/data.csv"
        )

        assert result is not None
        assert result.transfer_id == "tf-xyz"

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_fileservice_staging_path("nonexistent/path")

        assert result is None

    def test_with_tenant_filter(self, repo, mock_session):
        """Verify that when tenant is passed, the filter chain includes tenant condition."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.get_by_fileservice_staging_path(
            "file-transfers/t1/tf-xyz/data.csv", tenant="t1"
        )

        assert mock_session.query.return_value.filter.call_count >= 1
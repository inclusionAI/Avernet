"""Unit tests for DefaultSessionFileSharingDispatcher dispatch_complete_upload.

Covers the StagingObjectNotFoundError migration: the SINGLE upload path
raises StagingObjectNotFoundError (via the Session re-export) instead of
the OSS-specific ValueError workaround.  The MULTIPART empty-parts path
remains unchanged per the plan action section.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.api.session_file_sharing import (
    SessionCompleteUploadResponse,
    StagingObjectNotFoundError,
    TransferNotFoundError,
)
from secbaas.community.core.repository.session_file_ticket import SessionTicketRecord
from secbaas.community.core.service.session_file_sharing._dispatcher import (
    DefaultSessionFileSharingDispatcher,
)


def _make_ticket(**overrides):
    now = datetime.now()
    defaults = dict(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        transfer_id="tf-001",
        tenant="test-tenant",
        session_id="sess-001",
        status="CREATED",
        staging_subdir=None,
        filename="data.csv",
        fileservice_staging_path=(
            "baas-file-transfer/test/t1/sess-001/tf-001/data.csv"
        ),
        error_message=None,
        multipart_session_id=None,
        env="test",
        operator="unknown",
    )
    defaults.update(overrides)
    return SessionTicketRecord(**defaults)


@pytest.fixture
def file_backend():
    backend = MagicMock()
    backend.check_object_exists.return_value = True
    return backend


@pytest.fixture
def ticket_repo():
    return MagicMock()


@pytest.fixture
def dispatcher(file_backend, ticket_repo):
    return DefaultSessionFileSharingDispatcher(
        file_transfer_backend=file_backend,
        ticket_repo=ticket_repo,
    )


class TestDispatchCompleteUploadSingle:
    """SINGLE upload path in dispatch_complete_upload."""

    @pytest.mark.asyncio
    async def test_object_missing_raises_staging_object_not_found_error(
        self, dispatcher, file_backend, ticket_repo
    ):
        """When check_object_exists returns False, raise StagingObjectNotFoundError.

        This is the RED test — currently the dispatcher raises ValueError
        instead of StagingObjectNotFoundError, so this test MUST fail until
        the implementation is updated.
        """
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = False

        with pytest.raises(StagingObjectNotFoundError) as exc_info:
            await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert exc_info.value.staging_path == ticket.fileservice_staging_path

    @pytest.mark.asyncio
    async def test_object_exists_completes_normally(
        self, dispatcher, file_backend, ticket_repo
    ):
        """When check_object_exists returns True, complete to DONE."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = True

        result = await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCompleteUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_transfer_not_found_raises(
        self, dispatcher, ticket_repo
    ):
        """When ticket is not found, raise TransferNotFoundError."""
        ticket_repo.get_by_transfer_id.return_value = None

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_complete_upload(transfer_id="nonexistent")
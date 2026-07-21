"""Unit tests for DefaultBotFileTransferDispatcher."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import (
    BotNotFoundError,
    CancelUploadResponse,
    CompleteUploadResponse,
    GetDownloadUrlResponse,
    GetUploadUrlResponse,
    OssObjectNotFoundError,
    ShareLinkResponse,
    StagingDeleteResponse,
    StagingListResponse,
    TransferNotFoundError,
    TransferNotTerminalError,
    TransferStateConflictError,
)
from secbaas.community.core.repository.file_transfer_ticket import TicketRecord
from secbaas.community.core.service.bot_runtime.dispatcher._file_transfer_dispatcher import (
    MULTIPART_THRESHOLD,
    DefaultBotFileTransferDispatcher,
)
from secbaas.community.spi.file_transfer import (
    MultipartSession,
    ObjectListing,
    PartInfo,
)


def _make_ticket(**overrides):
    now = datetime.now()
    defaults = dict(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        transfer_id="tf-001",
        tenant="test-tenant",
        paas_device_id="sandbox@42",
        direction="UPLOAD",
        status="CREATED",
        staging_subdir=None,
        filename="data.csv",
        device_path="/home/data.csv",
        fileservice_staging_path="file-transfers/t1/tf-001/data.csv",
        error_message=None,
        download_url=None,
        upload_url=None,
        multipart_session_id=None,
        env="test",
    )
    defaults.update(overrides)
    return TicketRecord(**defaults)


@pytest.fixture
def bot_repo():
    return MagicMock()


@pytest.fixture
def device_repo():
    return MagicMock()


@pytest.fixture
def paas_facade():
    facade = MagicMock()
    facade.push_file = AsyncMock()
    return facade


@pytest.fixture
def file_backend():
    backend = MagicMock()
    backend.generate_upload_url.return_value = "https://oss.example.com/put?token=abc"
    backend.generate_download_url.return_value = "https://oss.example.com/get?token=abc"
    backend.check_object_exists.return_value = True
    backend.build_staging_path.return_value = "file-transfers/t1/tf-001/data.csv"
    backend.build_staging_prefix.return_value = "file-transfers/t1/"
    backend.list_objects.return_value = ObjectListing(
        items=[], truncated=False, next_marker=None
    )
    backend.initiate_multipart_upload.return_value = MultipartSession(
        session_id="mp-session-1",
        part_count=2,
        parts=[
            PartInfo(part_number=1, upload_url="https://oss.example.com/part1"),
            PartInfo(part_number=2, upload_url="https://oss.example.com/part2"),
        ],
    )
    backend.list_parts.return_value = [
        PartInfo(part_number=1, upload_url="", etag="etag-1"),
    ]
    return backend


@pytest.fixture
def ticket_repo():
    return MagicMock()


@pytest.fixture
def dispatcher(bot_repo, device_repo, paas_facade, file_backend, ticket_repo):
    return DefaultBotFileTransferDispatcher(
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
        file_transfer_backend=file_backend,
        ticket_repo=ticket_repo,
    )


def _setup_resolve_bot_device(dispatcher, bot_repo, device_repo):
    mock_bot = MagicMock()
    mock_bot.id = 1
    mock_bot.bot_uuid = "bot-001"
    mock_bot.tenant = "test-tenant"
    bot_repo.get_by_bot_uuid.return_value = mock_bot
    mock_device = MagicMock()
    mock_device.id = 1
    mock_device.device_uuid = "dev-001"
    mock_device.provider_device_id = "sandbox@42"
    mock_device.status = "ACTIVE"
    device_repo.list_by_bot_id.return_value = [mock_device]


# ── dispatch_get_upload_url ──────────────────────────────────────────


class TestDispatchGetUploadUrl:
    @pytest.mark.asyncio
    async def test_single_upload(self, dispatcher, bot_repo, device_repo, ticket_repo):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        result = await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            filename="data.csv",
            expire_seconds=3600,
            file_size=100,
        )
        assert isinstance(result, GetUploadUrlResponse)
        assert result.type == "SINGLE"
        assert result.upload_url is not None
        ticket_repo.create_ticket.assert_called_once()

    @pytest.mark.asyncio
    async def test_retention_mode(self, dispatcher, ticket_repo):
        result = await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path=None,
            filename="retention.csv",
            file_size=100,
        )
        assert result.type == "SINGLE"
        ticket_repo.create_ticket.assert_called_once()
        assert ticket_repo.create_ticket.call_args.kwargs["paas_device_id"] == ""

    @pytest.mark.asyncio
    async def test_multipart_upload(
        self, dispatcher, bot_repo, device_repo, ticket_repo, file_backend
    ):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        result = await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/bigfile.bin",
            filename="bigfile.bin",
            file_size=MULTIPART_THRESHOLD,
        )
        assert result.type == "MULTIPART"
        assert result.upload_session_id == "mp-session-1"
        file_backend.initiate_multipart_upload.assert_called_once()
        ticket_repo.create_ticket.assert_called_once()

    @pytest.mark.asyncio
    async def test_multipart_custom_part_size(self, dispatcher, bot_repo, device_repo):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        result = await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/bigfile.bin",
            filename="bigfile.bin",
            file_size=200_000_000,
            part_size=20_000_000,
        )
        assert result.type == "MULTIPART"
        assert result.part_count == 10
        assert result.part_size == 20_000_000

    @pytest.mark.asyncio
    async def test_negative_file_size_raises(self, dispatcher, bot_repo, device_repo):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        with pytest.raises(ValueError, match="file_size must be non-negative"):
            await dispatcher.dispatch_get_upload_url(
                bot_uuid="bot-001",
                tenant="t1",
                device_path="/x",
                file_size=-1,
            )

    @pytest.mark.asyncio
    async def test_negative_part_size_raises(self, dispatcher, bot_repo, device_repo):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        with pytest.raises(ValueError, match="part_size must be positive"):
            await dispatcher.dispatch_get_upload_url(
                bot_uuid="bot-001",
                tenant="t1",
                device_path="/x",
                file_size=MULTIPART_THRESHOLD,
                part_size=-1,
            )

    @pytest.mark.asyncio
    async def test_staging_subdir_path_traversal_rejected(
        self, dispatcher, bot_repo, device_repo
    ):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        with pytest.raises(ValueError, match="path traversal"):
            await dispatcher.dispatch_get_upload_url(
                bot_uuid="bot-001",
                tenant="t1",
                device_path="/x",
                staging_subdir="../etc",
            )

    @pytest.mark.asyncio
    async def test_staging_subdir_stripped(
        self, dispatcher, bot_repo, device_repo, file_backend
    ):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/x",
            staging_subdir="/subdir/",
            file_size=100,
        )
        assert file_backend.build_staging_path.call_args.kwargs["subdir"] == "subdir"

    @pytest.mark.asyncio
    async def test_device_path_traversal_rejected(
        self, dispatcher, bot_repo, device_repo
    ):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        with pytest.raises(ValueError, match="path traversal"):
            await dispatcher.dispatch_get_upload_url(
                bot_uuid="bot-001",
                tenant="t1",
                device_path="/home/../etc/passwd",
                file_size=100,
            )


# ── dispatch_get_download_url ────────────────────────────────────────


class TestDispatchGetDownloadUrl:
    @pytest.mark.asyncio
    async def test_download_url(
        self, dispatcher, bot_repo, device_repo, ticket_repo, paas_facade
    ):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        result = await dispatcher.dispatch_get_download_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
        )
        assert isinstance(result, GetDownloadUrlResponse)
        assert result.transfer_id is not None
        ticket_repo.create_ticket.assert_called_once()
        paas_facade.push_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_device_path_traversal_rejected(
        self, dispatcher, bot_repo, device_repo
    ):
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        with pytest.raises(ValueError, match="path traversal"):
            await dispatcher.dispatch_get_download_url(
                bot_uuid="bot-001",
                tenant="t1",
                device_path="/home/../etc/passwd",
            )


# ── dispatch_get_transfer_status (sync) ──────────────────────────────


class TestDispatchGetTransferStatus:
    @pytest.mark.asyncio
    async def test_status_done(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="DONE", download_url="https://oss.example.com/dl")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_get_transfer_status("tf-001", tenant="t1")
        assert result.status == "DONE"
        assert result.download_url == "https://oss.example.com/dl"

    @pytest.mark.asyncio
    async def test_status_created(self, dispatcher, ticket_repo):
        ticket = _make_ticket(
            status="CREATED", upload_url="https://oss.example.com/put"
        )
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_get_transfer_status("tf-001", tenant="t1")
        assert result.status == "CREATED"
        assert result.upload_url == "https://oss.example.com/put"

    @pytest.mark.asyncio
    async def test_status_failed(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="FAILED", error_message="timeout")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_get_transfer_status("tf-001", tenant="t1")
        assert result.status == "FAILED"
        assert result.error_message == "timeout"

    @pytest.mark.asyncio
    async def test_not_found(self, dispatcher, ticket_repo):
        ticket_repo.get_by_transfer_id.return_value = None
        with pytest.raises(TransferNotFoundError, match="tf-001"):
            await dispatcher.dispatch_get_transfer_status("tf-001")

    @pytest.mark.asyncio
    async def test_bot_ownership_validated(self, dispatcher, bot_repo, ticket_repo):
        """When bot_uuid is passed, validates bot exists under tenant."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        bot_repo.list_by_bot_uuid.return_value = [MagicMock()]  # bot exists

        result = await dispatcher.dispatch_get_transfer_status(
            "tf-001", tenant="t1", bot_uuid="bot-001"
        )
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_bot_not_found_when_validating(self, dispatcher, bot_repo):
        """When bot_uuid is passed and bot not found, raises BotNotFoundError."""
        bot_repo.list_by_bot_uuid.return_value = []  # no bots found

        with pytest.raises(BotNotFoundError, match="bot-001"):
            await dispatcher.dispatch_get_transfer_status(
                "tf-001", tenant="t1", bot_uuid="bot-001"
            )


# ── dispatch_complete_upload ─────────────────────────────────────────


class TestDispatchCompleteUpload:
    @pytest.mark.asyncio
    async def test_single_complete(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = True
        result = await dispatcher.dispatch_complete_upload("tf-001")
        assert isinstance(result, CompleteUploadResponse)
        assert result.status == "UPLOAD_COMPLETED"
        ticket_repo.update_status.assert_called_once_with("tf-001", "UPLOAD_COMPLETED")

    @pytest.mark.asyncio
    async def test_single_missing_object(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = False
        with pytest.raises(OssObjectNotFoundError):
            await dispatcher.dispatch_complete_upload("tf-001")

    @pytest.mark.asyncio
    async def test_multipart_complete(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="CREATED", multipart_session_id="mp-1")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_complete_upload("tf-001")
        assert result.status == "UPLOAD_COMPLETED"
        file_backend.list_parts.assert_called_once()
        file_backend.complete_multipart_upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_multipart_empty_parts_raises(
        self, dispatcher, ticket_repo, file_backend
    ):
        ticket = _make_ticket(status="CREATED", multipart_session_id="mp-1")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.list_parts.return_value = []
        with pytest.raises(ValueError, match="No parts uploaded"):
            await dispatcher.dispatch_complete_upload("tf-001")

    @pytest.mark.asyncio
    async def test_not_found(self, dispatcher, ticket_repo):
        ticket_repo.get_by_transfer_id.return_value = None
        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_complete_upload("tf-001")

    @pytest.mark.asyncio
    async def test_idempotent_already_completed(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="UPLOAD_COMPLETED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_complete_upload("tf-001")
        assert result.status == "UPLOAD_COMPLETED"

    @pytest.mark.asyncio
    async def test_idempotent_done(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_complete_upload("tf-001")
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_terminal_state_raises(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="CANCELLED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        with pytest.raises(ValueError, match="terminal state"):
            await dispatcher.dispatch_complete_upload("tf-001")

    @pytest.mark.asyncio
    async def test_failed_state_raises(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="FAILED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        with pytest.raises(ValueError, match="terminal state"):
            await dispatcher.dispatch_complete_upload("tf-001")

    @pytest.mark.asyncio
    async def test_cas_conflict_recovered(self, dispatcher, ticket_repo, file_backend):
        """CAS conflict on update_status: re-read returns DONE → success."""
        ticket_created = _make_ticket(status="CREATED")
        ticket_done = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.side_effect = [
            ticket_created,  # first read: status=CREATED
            ticket_done,  # CAS re-read: already reached DONE
        ]
        file_backend.check_object_exists.return_value = True

        def _raise_cas(*args, **kwargs):
            raise TransferStateConflictError("conflict")

        ticket_repo.update_status.side_effect = _raise_cas

        result = await dispatcher.dispatch_complete_upload("tf-001")
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_cas_conflict_unrecoverable(
        self, dispatcher, ticket_repo, file_backend
    ):
        """CAS conflict with unrecoverable state → re-raises TransferStateConflictError."""
        ticket_created = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.side_effect = [
            ticket_created,  # first read: status=CREATED
            ticket_created,  # CAS re-read: still CREATED (unrecoverable)
        ]
        file_backend.check_object_exists.return_value = True

        def _raise_cas(*args, **kwargs):
            raise TransferStateConflictError("conflict")

        ticket_repo.update_status.side_effect = _raise_cas

        with pytest.raises(TransferStateConflictError):
            await dispatcher.dispatch_complete_upload("tf-001")


# ── dispatch_cancel_upload ───────────────────────────────────────────


class TestDispatchCancelUpload:
    @pytest.mark.asyncio
    async def test_cancel_with_multipart(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="CREATED", multipart_session_id="mp-1")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_cancel_upload("tf-001")
        assert isinstance(result, CancelUploadResponse)
        assert result.status == "CANCELLED"
        file_backend.abort_multipart_upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_single(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_cancel_upload("tf-001")
        assert result.status == "CANCELLED"
        file_backend.abort_multipart_upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_found(self, dispatcher, ticket_repo):
        ticket_repo.get_by_transfer_id.return_value = None
        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_cancel_upload("tf-001")

    @pytest.mark.asyncio
    async def test_idempotent_cancelled(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="CANCELLED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_cancel_upload("tf-001")
        assert result.status == "CANCELLED"

    @pytest.mark.asyncio
    async def test_idempotent_done(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_cancel_upload("tf-001")
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_upload_completed_cannot_cancel(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="UPLOAD_COMPLETED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        with pytest.raises(ValueError, match="already completed"):
            await dispatcher.dispatch_cancel_upload("tf-001")

    @pytest.mark.asyncio
    async def test_cancel_multipart_nosuchupload_recovery(
        self, dispatcher, ticket_repo, file_backend
    ):
        """NoSuchUpload on abort_multipart_upload → recover and cancel ticket."""
        ticket = _make_ticket(status="CREATED", multipart_session_id="mp-1")
        ticket_repo.get_by_transfer_id.return_value = ticket

        def _raise_nosuchupload(*args, **kwargs):
            raise RuntimeError("NoSuchUpload: session does not exist")

        file_backend.abort_multipart_upload.side_effect = _raise_nosuchupload

        result = await dispatcher.dispatch_cancel_upload("tf-001")
        assert result.status == "CANCELLED"
        # Should still call update_status("CANCELLED") after recovering
        ticket_repo.update_status.assert_called_once_with("tf-001", "CANCELLED")

    @pytest.mark.asyncio
    async def test_cancel_multipart_abort_other_error_raises(
        self, dispatcher, ticket_repo, file_backend
    ):
        """Non-NoSuchUpload error on abort_multipart_upload → re-raised."""
        ticket = _make_ticket(status="CREATED", multipart_session_id="mp-1")
        ticket_repo.get_by_transfer_id.return_value = ticket

        def _raise_other(*args, **kwargs):
            raise RuntimeError("AccessDenied: permission error")

        file_backend.abort_multipart_upload.side_effect = _raise_other

        with pytest.raises(RuntimeError, match="AccessDenied"):
            await dispatcher.dispatch_cancel_upload("tf-001")

    @pytest.mark.asyncio
    async def test_cancel_cas_conflict_recovered(self, dispatcher, ticket_repo):
        """CAS conflict on cancel update_status: re-read returns DONE → success."""
        ticket_created = _make_ticket(status="CREATED")
        ticket_done = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.side_effect = [
            ticket_created,  # first read: status=CREATED
            ticket_done,  # CAS re-read: already reached DONE
        ]

        def _raise_cas(*args, **kwargs):
            raise TransferStateConflictError("conflict")

        ticket_repo.update_status.side_effect = _raise_cas

        result = await dispatcher.dispatch_cancel_upload("tf-001")
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_cancel_cas_conflict_upload_completed(self, dispatcher, ticket_repo):
        """CAS conflict on cancel: re-read returns UPLOAD_COMPLETED → ValueError."""
        ticket_created = _make_ticket(status="CREATED")
        ticket_completed = _make_ticket(status="UPLOAD_COMPLETED")
        ticket_repo.get_by_transfer_id.side_effect = [
            ticket_created,  # first read: status=CREATED
            ticket_completed,  # CAS re-read: UPLOAD_COMPLETED
        ]

        def _raise_cas(*args, **kwargs):
            raise TransferStateConflictError("conflict")

        ticket_repo.update_status.side_effect = _raise_cas

        with pytest.raises(ValueError, match="already completed"):
            await dispatcher.dispatch_cancel_upload("tf-001")

    @pytest.mark.asyncio
    async def test_cancel_cas_conflict_unrecoverable(self, dispatcher, ticket_repo):
        """CAS conflict on cancel with unrecoverable state → re-raises error."""
        ticket_created = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.side_effect = [
            ticket_created,  # first read: status=CREATED
            ticket_created,  # CAS re-read: still CREATED (unrecoverable)
        ]

        def _raise_cas(*args, **kwargs):
            raise TransferStateConflictError("conflict")

        ticket_repo.update_status.side_effect = _raise_cas

        with pytest.raises(TransferStateConflictError):
            await dispatcher.dispatch_cancel_upload("tf-001")


# ── dispatch_list_staging ────────────────────────────────────────────


class TestDispatchListStaging:
    @pytest.mark.asyncio
    async def test_with_tenant(self, dispatcher, file_backend):
        result = await dispatcher.dispatch_list_staging(
            prefix="", limit=10, tenant="t1"
        )
        assert isinstance(result, StagingListResponse)
        file_backend.build_staging_prefix.assert_called_once_with(
            tenant="t1", subdir=None
        )

    @pytest.mark.asyncio
    async def test_with_prefix_subdir(self, dispatcher, file_backend):
        await dispatcher.dispatch_list_staging(
            prefix="my-subdir", limit=10, tenant="t1"
        )
        file_backend.build_staging_prefix.assert_called_once_with(
            tenant="t1", subdir="my-subdir"
        )

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, dispatcher):
        with pytest.raises(ValueError, match="path traversal"):
            await dispatcher.dispatch_list_staging(prefix="../etc")

    @pytest.mark.asyncio
    async def test_strips_legacy_prefix(self, dispatcher, file_backend):
        await dispatcher.dispatch_list_staging(
            prefix="file-transfers/sub", limit=10, tenant="t1"
        )
        file_backend.build_staging_prefix.assert_called_once_with(
            tenant="t1", subdir="sub"
        )

    @pytest.mark.asyncio
    async def test_strips_baas_prefix(self, dispatcher, file_backend):
        await dispatcher.dispatch_list_staging(
            prefix="baas-file-transfer/sub", limit=10, tenant="t1"
        )
        file_backend.build_staging_prefix.assert_called_once_with(
            tenant="t1", subdir="sub"
        )

    @pytest.mark.asyncio
    async def test_legacy_prefix_exact_match(self, dispatcher, file_backend):
        await dispatcher.dispatch_list_staging(
            prefix="file-transfers", limit=10, tenant="t1"
        )
        file_backend.build_staging_prefix.assert_called_once_with(
            tenant="t1", subdir=None
        )


# ── dispatch_delete_staging ──────────────────────────────────────────


class TestDispatchDeleteStaging:
    @pytest.mark.asyncio
    async def test_delete_done_ticket(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_fileservice_staging_path.return_value = ticket
        result = await dispatcher.dispatch_delete_staging(key="oss-key", tenant="t1")
        assert isinstance(result, StagingDeleteResponse)
        assert result.previous_status == "DONE"
        assert result.new_status == "DELETED"
        file_backend.delete_object.assert_called_once_with("oss-key")

    @pytest.mark.asyncio
    async def test_not_found(self, dispatcher, ticket_repo):
        ticket_repo.get_by_fileservice_staging_path.return_value = None
        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_delete_staging(key="oss-key")

    @pytest.mark.asyncio
    async def test_non_terminal(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_fileservice_staging_path.return_value = ticket
        with pytest.raises(TransferNotTerminalError):
            await dispatcher.dispatch_delete_staging(key="oss-key")

    @pytest.mark.asyncio
    async def test_already_deleted(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="DELETED")
        ticket_repo.get_by_fileservice_staging_path.return_value = ticket
        result = await dispatcher.dispatch_delete_staging(key="oss-key")
        assert result.previous_status == "DELETED"
        assert result.new_status == "DELETED"


# ── dispatch_generate_share_link ──────────────────────────────────────


class TestDispatchGenerateShareLink:
    @pytest.mark.asyncio
    async def test_done(self, dispatcher, ticket_repo, file_backend):
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_generate_share_link("tf-001", tenant="t1")
        assert isinstance(result, ShareLinkResponse)
        assert result.transfer_id == "tf-001"
        file_backend.generate_download_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_found(self, dispatcher, ticket_repo):
        ticket_repo.get_by_transfer_id.return_value = None
        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_generate_share_link("tf-001")

    @pytest.mark.asyncio
    async def test_not_done(self, dispatcher, ticket_repo):
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.return_value = ticket
        with pytest.raises(ValueError, match="requires ticket status DONE"):
            await dispatcher.dispatch_generate_share_link("tf-001")

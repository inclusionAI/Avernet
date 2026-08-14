"""Unit tests for operator field — normalization, passthrough, and status response."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import (
    GetDownloadUrlRequest,
    GetUploadUrlRequest,
)
from secbaas.community.core.repository.file_transfer_ticket import TicketRecord
from secbaas.community.core.service.bot_runtime.dispatcher._file_transfer_dispatcher import (
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
        multipart_session_id=None,
        env="test",
        operator="unknown",
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


# ── Test 1: operator=None normalizes to "unknown" (D-04) ─────────────


class TestOperatorNoneNormalization:
    @pytest.mark.asyncio
    async def test_upload_operator_none(
        self, dispatcher, bot_repo, device_repo, ticket_repo
    ):
        """operator=None in dispatch_get_upload_url → create_ticket called with 'unknown'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            filename="data.csv",
            file_size=100,
            operator=None,
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "unknown"

    @pytest.mark.asyncio
    async def test_download_operator_none(
        self, dispatcher, bot_repo, device_repo, ticket_repo, paas_facade
    ):
        """operator=None in dispatch_get_download_url → create_ticket called with 'unknown'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_download_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            operator=None,
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "unknown"


# ── Test 2: operator="" normalizes to "unknown" (D-04) ───────────────


class TestOperatorEmptyStringNormalization:
    @pytest.mark.asyncio
    async def test_upload_operator_empty_string(
        self, dispatcher, bot_repo, device_repo, ticket_repo
    ):
        """operator="" in dispatch_get_upload_url → create_ticket called with 'unknown'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            filename="data.csv",
            file_size=100,
            operator="",
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "unknown"

    @pytest.mark.asyncio
    async def test_download_operator_empty_string(
        self, dispatcher, bot_repo, device_repo, ticket_repo, paas_facade
    ):
        """operator="" in dispatch_get_download_url → create_ticket called with 'unknown'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_download_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            operator="",
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "unknown"


# ── Test 3: operator="   " normalizes to "unknown" (D-04) ────────────


class TestOperatorWhitespaceNormalization:
    @pytest.mark.asyncio
    async def test_upload_operator_whitespace_only(
        self, dispatcher, bot_repo, device_repo, ticket_repo
    ):
        """operator="   " in dispatch_get_upload_url → .strip() → 'unknown'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            filename="data.csv",
            file_size=100,
            operator="   ",
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "unknown"


# ── Test 4: operator="user123" passes through unchanged ───────────────


class TestOperatorPassthrough:
    @pytest.mark.asyncio
    async def test_upload_operator_passthrough(
        self, dispatcher, bot_repo, device_repo, ticket_repo
    ):
        """operator='user123' in upload → create_ticket called with 'user123'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_upload_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            filename="data.csv",
            file_size=100,
            operator="user123",
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "user123"

    @pytest.mark.asyncio
    async def test_download_operator_passthrough(
        self, dispatcher, bot_repo, device_repo, ticket_repo, paas_facade
    ):
        """operator='bob' in download → create_ticket called with 'bob'."""
        _setup_resolve_bot_device(dispatcher, bot_repo, device_repo)
        await dispatcher.dispatch_get_download_url(
            bot_uuid="bot-001",
            tenant="t1",
            device_path="/home/data.csv",
            operator="bob",
        )
        assert ticket_repo.create_ticket.call_args.kwargs["operator"] == "bob"


# ── Test 5: GetTransferStatusResponse includes operator field (D-05) ──


class TestTransferStatusOperator:
    @pytest.mark.asyncio
    async def test_status_response_includes_operator(self, dispatcher, ticket_repo):
        """dispatch_get_transfer_status returns operator from TicketRecord."""
        ticket = _make_ticket(status="DONE", operator="alice")
        ticket_repo.get_by_transfer_id.return_value = ticket
        result = await dispatcher.dispatch_get_transfer_status("tf-001", tenant="t1")
        assert result.operator == "alice"


# ── Test 6: Pydantic model default value (D-03) ──────────────────────


class TestPydanticModelDefaults:
    def test_upload_request_default_operator(self):
        """GetUploadUrlRequest without operator → defaults to 'unknown'."""
        req = GetUploadUrlRequest(device_path="/home/data.csv")
        assert req.operator == "unknown"

    def test_download_request_default_operator(self):
        """GetDownloadUrlRequest without operator → defaults to 'unknown'."""
        req = GetDownloadUrlRequest(device_path="/home/data.csv")
        assert req.operator == "unknown"

    def test_upload_request_explicit_operator(self):
        """GetUploadUrlRequest with explicit operator → preserves value."""
        req = GetUploadUrlRequest(device_path="/home/data.csv", operator="mybot")
        assert req.operator == "mybot"

    def test_download_request_explicit_operator(self):
        """GetDownloadUrlRequest with explicit operator → preserves value."""
        req = GetDownloadUrlRequest(device_path="/home/data.csv", operator="mybot")
        assert req.operator == "mybot"

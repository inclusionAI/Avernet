"""Fixtures for file_transfer E2E tests.

Parent conftest (tests/e2e/conftest.py) provides:
- api (APITestHelper) — scope=function
- http_client — scope=function
- app_base_url — scope=session
- test_tenant — scope=session
- test_template_uuid — scope=session
- unique_id / unique_request_id — scope=function
- create_test_bot / activate_test_bot / find_existing_bot / cleanup_bot
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.core.repository.file_transfer_ticket import TicketRecord, TicketRepository
from secbaas.core.service.paas._facade import PaasServiceFacade
from secbaas.core.service.paas._mock_paas_service import MockPaasService
from secbaas.core.service.scheduler import FileTransferPoller, FileTransferPollerConfig
from tests.utils.stub_file_transfer_backend import StubFileTransferBackend


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def stub_oss_backend() -> StubFileTransferBackend:
    """In-memory OSS simulator for E2E file transfer tests.

    All fixture-scoped (function, the default) so _storage is reset
    between tests, preventing cross-test state leakage.
    """
    return StubFileTransferBackend()


@pytest.fixture
def mock_ticket_repo() -> MagicMock:
    """MagicMock for TicketRepository — configure return_value per test."""
    return MagicMock(spec=TicketRepository)


@pytest.fixture
def mock_paas_service() -> MockPaasService:
    """MockPaasService with configurable _pull_should_fail / _push_should_fail."""
    return MockPaasService()


@pytest.fixture
def poller_config() -> FileTransferPollerConfig:
    """Default poller configuration used across E2E tests."""
    return FileTransferPollerConfig(
        enabled=True,
        lock_expire_seconds=300,
        cron_interval_seconds=10,
        upload_timeout_seconds=3600,
        max_concurrent_tickets=5,
    )


@pytest.fixture
def mock_lock_service() -> MagicMock:
    """Lock service mock that always grants the lock.

    The returned lock context has acquired=True and a fixed lock_holder
    so the poller can acquire and later release the lock normally.
    """
    svc = MagicMock()
    lock_ctx = MagicMock()
    lock_ctx.acquired = True
    lock_ctx.lock_holder = "e2e-test-holder"
    svc.acquire_lock.return_value = lock_ctx
    return svc


@pytest.fixture
def poller(
    poller_config: FileTransferPollerConfig,
    mock_lock_service: MagicMock,
    mock_ticket_repo: MagicMock,
    stub_oss_backend: StubFileTransferBackend,
    mock_paas_service: MockPaasService,
) -> FileTransferPoller:
    """FileTransferPoller with all mocked dependencies for direct await poller.run()."""
    paas_facade = MagicMock(spec=PaasServiceFacade)
    paas_facade.pull_file = AsyncMock()
    paas_facade.push_file = AsyncMock()
    return FileTransferPoller(
        config=poller_config,
        lock_service=mock_lock_service,
        ticket_repo=mock_ticket_repo,
        file_backend=stub_oss_backend,
        paas_facade=paas_facade,
    )


# ── Ticket fixtures for v1.5 E2E tests ───────────────────────────────


@pytest.fixture
def multipart_ticket() -> MagicMock:
    """Ticket mock with multipart_session_id set for multipart E2E tests."""
    return _make_ticket_record(
        transfer_id="stub-mp-test-transfer",
        direction="UPLOAD",
        status="CREATED",
        fileservice_staging_path="file-transfers/stub-mp-test-transfer/test.bin",
        multipart_session_id="stub-mp-test-transfer",
    )


@pytest.fixture
def done_ticket() -> MagicMock:
    """Ticket mock with status DONE for share-link and staging delete tests."""
    return _make_ticket_record(
        transfer_id="stub-done-test-transfer",
        direction="UPLOAD",
        status="DONE",
        fileservice_staging_path="file-transfers/stub-done-test-transfer/data.bin",
    )


# ── Helper functions ────────────────────────────────────────────────


def _make_ticket_record(*,
    transfer_id: str,
    direction: str = "UPLOAD",
    status: str = "CREATED",
    device_path: str | None = "/home/bot/test.txt",
    gmt_create: datetime | None = None,
    fileservice_staging_path: str | None = None,
    multipart_session_id: str | None = None,
    **overrides,
) -> MagicMock:
    """Create a MagicMock(spec=TicketRecord) with sensible defaults.

    Args:
        transfer_id: Unique transfer identifier.
        direction: ``"UPLOAD"`` or ``"DOWNLOAD"``.
        status: Ticket status (``"CREATED"``, ``"DONE"``, etc.).
        device_path: Absolute path on the device target.
        gmt_create: Creation timestamp (default: 30 s ago).
        fileservice_staging_path: OSS staging path (default: derived from transfer_id).
        multipart_session_id: OSS multipart upload session ID (default: None).
        **overrides: Any TicketRecord field to override.

    Returns:
        A MagicMock that passes isinstance(..., TicketRecord) checks
        and supports attribute access for all 17 TicketRecord fields.
    """
    now = datetime.now()
    return MagicMock(spec=TicketRecord, **{
        "id": 1,
        "gmt_create": gmt_create or (now - timedelta(seconds=30)),
        "gmt_modified": now,
        "transfer_id": transfer_id,
        "tenant": "team_claw",
        "paas_device_id": "mock-sandbox-abc@42",
        "direction": direction,
        "status": status,
        "staging_subdir": None,
        "filename": "test.txt",
        "device_path": device_path,
        "fileservice_staging_path": fileservice_staging_path
            or f"file-transfers/{transfer_id}/test.txt",
        "error_message": None,
        "download_url": None,
        "upload_url": None,
        "multipart_session_id": multipart_session_id,
        "env": "test",
        **overrides,
    })
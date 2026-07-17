"""Unit tests for FileTransferPoller.

Tests:
- run() method: disabled, dry_run, empty tickets, normal processing
- _process_single_ticket: normal, retention, timeout, lock failure, errors
- _process_download_ticket: normal, timeout, lock failure, OSS detection
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.repository.file_transfer_ticket import TicketRecord
from secbaas.community.core.service.scheduler._tasks._file_transfer_poller import (
    FileTransferPoller,
    FileTransferPollerConfig,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_config(**overrides):
    defaults = dict(
        enabled=True,
        lock_expire_seconds=300,
        cron_interval_seconds=10,
        upload_timeout_seconds=3600,
        max_concurrent_tickets=5,
        dry_run=False,
    )
    defaults.update(overrides)
    return FileTransferPollerConfig(**defaults)


def _make_ticket(**overrides):
    now = datetime.now(UTC).replace(tzinfo=None)
    defaults = dict(
        id=1,
        gmt_create=now - timedelta(seconds=30),
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
    return MagicMock(spec=TicketRecord, **defaults)


def _make_poller(config=None, **overrides):
    lock_service = MagicMock()
    ticket_repo = MagicMock()
    file_backend = MagicMock()
    paas_facade = MagicMock()

    if config is None:
        config = _make_config()

    # Default: lock acquired
    lock_ctx = MagicMock()
    lock_ctx.acquired = True
    lock_ctx.lock_holder = "holder-001"
    lock_service.acquire_lock.return_value = lock_ctx

    # Default: OSS object exists
    file_backend.check_object_exists.return_value = True
    file_backend.generate_download_url.return_value = "https://oss.example.com/dl?t=abc"

    # Default: pull_file is async
    paas_facade.pull_file = AsyncMock()

    lock_service = overrides.pop("lock_service", lock_service)
    ticket_repo = overrides.pop("ticket_repo", ticket_repo)
    file_backend = overrides.pop("file_backend", file_backend)
    paas_facade = overrides.pop("paas_facade", paas_facade)

    return FileTransferPoller(
        config=config,
        lock_service=lock_service,
        ticket_repo=ticket_repo,
        file_backend=file_backend,
        paas_facade=paas_facade,
    )


# ── run() tests ──────────────────────────────────────────────────────


class TestRun:
    @pytest.mark.asyncio
    async def test_run_disabled(self):
        """Disabled config skips processing."""
        config = _make_config(enabled=False)
        poller = _make_poller(config=config)

        await poller.run()
        # Should return early without hitting ticket_repo
        poller._ticket_repo.list_pending_uploads.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_dry_run(self):
        """Dry-run config skips processing."""
        config = _make_config(dry_run=True)
        poller = _make_poller(config=config)

        await poller.run()
        poller._ticket_repo.list_pending_uploads.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_no_pending_tickets(self):
        """Empty ticket list returns early."""
        poller = _make_poller()
        poller._ticket_repo.list_pending_uploads.return_value = []

        await poller.run()

    @pytest.mark.asyncio
    async def test_run_list_failure(self):
        """list_pending_uploads exception is caught and logged."""
        poller = _make_poller()
        poller._ticket_repo.list_pending_uploads.side_effect = RuntimeError("db down")

        await poller.run()  # should not raise

    @pytest.mark.asyncio
    async def test_run_processes_upload_tickets(self):
        """UPLOAD tickets are processed."""
        ticket = _make_ticket(status="CREATED", direction="UPLOAD")
        poller = _make_poller()
        poller._ticket_repo.list_pending_uploads.return_value = [ticket]

        await poller.run()

        # pull_file should have been called (normal path)
        poller._paas_facade.pull_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_processes_download_tickets(self):
        """DOWNLOAD tickets (CREATED) are processed."""
        ticket = _make_ticket(status="CREATED", direction="DOWNLOAD")
        poller = _make_poller()
        poller._ticket_repo.list_pending_uploads.return_value = [ticket]

        await poller.run()

        # download ticket: should call update_urls with download_url
        poller._ticket_repo.update_urls.assert_called_once()


# ── _process_single_ticket tests ─────────────────────────────────────


class TestProcessSingleTicket:
    def test_normal_path(self):
        """CREATED -> UPLOAD_COMPLETED -> DONE."""
        ticket = _make_ticket(status="CREATED")
        poller = _make_poller()

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "pull_success"
        transitions = [
            c.args[1] for c in poller._ticket_repo.update_status.call_args_list
        ]
        assert "UPLOAD_COMPLETED" in transitions
        assert "DONE" in transitions

    def test_retention_mode(self):
        """device_path=None skips pull_file, goes directly to DONE."""
        ticket = _make_ticket(status="UPLOADING", device_path=None)
        poller = _make_poller()

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "retention_done"
        poller._paas_facade.pull_file.assert_not_called()

    def test_timeout(self):
        """gmt_create + timeout < now marks FAILED."""
        config = _make_config(upload_timeout_seconds=10)
        ticket = _make_ticket(
            status="CREATED",
            gmt_create=datetime.now(UTC).replace(tzinfo=None)
            - timedelta(seconds=60),
        )
        poller = _make_poller(config=config)

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "timed_out"
        poller._ticket_repo.update_status.assert_called_with(
            "tf-001", "FAILED", "Upload timed out"
        )

    def test_terminal_state_skipped(self):
        """Terminal-state tickets are skipped."""
        ticket = _make_ticket(status="CANCELLED")
        poller = _make_poller()

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "skipped"
        poller._paas_facade.pull_file.assert_not_called()

    def test_lock_not_acquired(self):
        """Lock contention returns 'skipped'."""
        ticket = _make_ticket(status="CREATED")
        poller = _make_poller()
        lock_ctx = MagicMock()
        lock_ctx.acquired = False
        poller._lock_service.acquire_lock.return_value = lock_ctx

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "skipped"

    def test_oss_not_ready(self):
        """OSS object not yet present returns 'oss_not_ready'."""
        ticket = _make_ticket(status="CREATED")
        poller = _make_poller()
        poller._file_backend.check_object_exists.return_value = False

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "oss_not_ready"

    def test_pull_file_fails(self):
        """pull_file raises -> error caught, returns 'failed', ticket NOT marked FAILED.

        Transient errors (network issues, etc.) are intentionally NOT marked
        FAILED — the ticket stays in its current state (UPLOAD_COMPLETED) and
        will be retried in the next poller cycle.
        """
        ticket = _make_ticket(status="CREATED")
        poller = _make_poller()
        poller._paas_facade.pull_file.side_effect = RuntimeError("network error")

        import asyncio

        result = asyncio.run(poller._process_single_ticket(ticket))

        assert result == "failed"
        # Verify no FAILED transition was made — transient errors are retried
        failed_calls = [
            c for c in poller._ticket_repo.update_status.call_args_list
            if c.args[1] == "FAILED"
        ]
        assert len(failed_calls) == 0


# ── _process_download_ticket tests ───────────────────────────────────


class TestProcessDownloadTicket:
    def test_normal_path(self):
        """DOWNLOAD: CREATED -> PUSHING -> DONE with download_url."""
        ticket = _make_ticket(status="CREATED", direction="DOWNLOAD")
        poller = _make_poller()

        import asyncio

        result = asyncio.run(poller._process_download_ticket(ticket))

        assert result == "download_ready"

        # Should transition: CREATED -> PUSHING -> DONE
        transitions = [
            c.args[1] for c in poller._ticket_repo.update_status.call_args_list
        ]
        assert "PUSHING" in transitions
        assert "DONE" in transitions

        # Should call update_urls with download_url
        poller._ticket_repo.update_urls.assert_called_once()

    def test_timeout(self):
        """DOWNLOAD ticket timeout marks FAILED."""
        config = _make_config(upload_timeout_seconds=10)
        ticket = _make_ticket(
            status="CREATED",
            direction="DOWNLOAD",
            gmt_create=datetime.now(UTC).replace(tzinfo=None)
            - timedelta(seconds=60),
        )
        poller = _make_poller(config=config)

        import asyncio

        result = asyncio.run(poller._process_download_ticket(ticket))

        assert result == "timed_out"
        poller._ticket_repo.update_status.assert_called_with(
            "tf-001", "FAILED", "Download timed out"
        )

    def test_terminal_state_skipped(self):
        """DOWNLOAD ticket in terminal state is skipped."""
        ticket = _make_ticket(status="DONE", direction="DOWNLOAD")
        poller = _make_poller()

        import asyncio

        result = asyncio.run(poller._process_download_ticket(ticket))

        assert result == "skipped"

    def test_lock_not_acquired(self):
        """Lock contention returns 'skipped' for DOWNLOAD."""
        ticket = _make_ticket(status="CREATED", direction="DOWNLOAD")
        poller = _make_poller()
        lock_ctx = MagicMock()
        lock_ctx.acquired = False
        poller._lock_service.acquire_lock.return_value = lock_ctx

        import asyncio

        result = asyncio.run(poller._process_download_ticket(ticket))

        assert result == "skipped"

    def test_oss_not_ready(self):
        """OSS object not present for DOWNLOAD returns 'oss_not_ready'."""
        ticket = _make_ticket(status="CREATED", direction="DOWNLOAD")
        poller = _make_poller()
        poller._file_backend.check_object_exists.return_value = False

        import asyncio

        result = asyncio.run(poller._process_download_ticket(ticket))

        assert result == "oss_not_ready"

    def test_generic_error(self):
        """Generic error marks ticket FAILED."""
        ticket = _make_ticket(status="CREATED", direction="DOWNLOAD")
        poller = _make_poller()
        poller._file_backend.check_object_exists.side_effect = RuntimeError("oss down")

        import asyncio

        result = asyncio.run(poller._process_download_ticket(ticket))

        assert result == "failed"

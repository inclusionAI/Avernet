"""Contract test scaffold for FileTransferPoller.

Exercises _process_single_ticket in normal, retention, and timeout paths
using mocked dependencies.  Imports FileTransferPoller via the public
package path per secbaas import rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from secbaas.core.service.scheduler import FileTransferPoller, FileTransferPollerConfig


class TestFileTransferPollerContract:
    """Fixture-driven contract tests for FileTransferPoller."""

    _default_transfer_id = "tf-00000000-0000-0000-0000-000000000001"
    _default_paas_device_id = "sandbox-abc123@42"
    _default_device_path = "/home/bot/uploads/data.csv"

    # ── fixtures ──────────────────────────────────────────────────────

    @staticmethod
    def _make_config(**overrides) -> FileTransferPollerConfig:
        defaults = dict(
            enabled=True,
            lock_name="file_transfer_poller_lock",
            lock_expire_seconds=300,
            cron_interval_seconds=10,
            upload_timeout_seconds=3600,
            max_concurrent_tickets=5,
            dry_run=False,
        )
        defaults.update(overrides)
        return FileTransferPollerConfig(**defaults)

    @staticmethod
    def _make_ticket_record(**overrides):
        """Return a MagicMock that looks like a TicketRecord."""
        from secbaas.core.repository.file_transfer_ticket import TicketRecord

        now = datetime.now()
        defaults = dict(
            id=1,
            gmt_create=now - timedelta(seconds=30),
            gmt_modified=now,
            transfer_id=TestFileTransferPollerContract._default_transfer_id,
            tenant="test_tenant",
            paas_device_id=TestFileTransferPollerContract._default_paas_device_id,
            direction="UPLOAD",
            status="CREATED",
            staging_subdir=None,
            filename="data.csv",
            device_path=TestFileTransferPollerContract._default_device_path,
            fileservice_staging_path="ocb-staging/tf-00000000-0000-0000-0000-000000000001/data.csv",
            error_message=None,
            env="test",
        )
        defaults.update(overrides)
        return MagicMock(spec=TicketRecord, **defaults)

    @staticmethod
    def _make_poller(config=None, **mock_overrides):
        """Create a FileTransferPoller with mocks or overridden mocks.

        Default mocks: lock acquired, OSS object exists, pull_file succeeds.
        """
        if config is None:
            config = TestFileTransferPollerContract._make_config()

        lock_service = MagicMock()
        ticket_repo = MagicMock()
        file_backend = MagicMock()
        paas_facade = MagicMock()

        # Default mock behavior: lock acquired
        lock_ctx = MagicMock()
        lock_ctx.acquired = True
        lock_service.try_lock.return_value.__enter__.return_value = lock_ctx

        # Default: OSS object exists
        file_backend.check_object_exists.return_value = True
        file_backend.generate_download_url.return_value = (
            "https://oss.example.com/download?token=abc"
        )

        # Override with any caller-specific mocks
        for name, value in mock_overrides.items():
            locals()[name] = value

        return FileTransferPoller(
            config=config,
            lock_service=lock_service,
            ticket_repo=ticket_repo,
            file_backend=file_backend,
            paas_facade=paas_facade,
        )

    # ── normal path ───────────────────────────────────────────────────

    def test_process_single_ticket_normal_path(self) -> None:
        """Normal path: ticket is CREATED, OSS object ready, pull_file succeeds."""
        config = self._make_config()
        ticket = self._make_ticket_record(status="CREATED")
        paas_facade = MagicMock()
        ticket_repo = MagicMock()

        poller = self._make_poller(
            config=config,
            paas_facade=paas_facade,
            ticket_repo=ticket_repo,
        )

        import asyncio
        result = asyncio.run(poller._process_single_ticket(ticket))  # noqa: SLF001

        assert result == "pull_success"
        # Verify status transitions
        # update_status should be called: UPLOAD_COMPLETED, DONE
        status_calls = [
            c.args[0] for c in ticket_repo.update_status.call_args_list
            if len(c.args) >= 1
        ]
        # First positional arg is transfer_id; second is new_status
        status_transitions = [
            (c.args[0], c.args[1])
            for c in ticket_repo.update_status.call_args_list
        ]
        assert ("tf-00000000-0000-0000-0000-000000000001", "UPLOAD_COMPLETED") in status_transitions
        assert ("tf-00000000-0000-0000-0000-000000000001", "DONE") in status_transitions

    # ── retention path ────────────────────────────────────────────────

    def test_process_single_ticket_retention_mode(self) -> None:
        """Retention mode: device_path IS NULL -> skip pull_file, go UPLOAD_COMPLETED->DONE."""
        config = self._make_config()
        ticket = self._make_ticket_record(status="UPLOADING", device_path=None)
        paas_facade = MagicMock()
        ticket_repo = MagicMock()

        poller = self._make_poller(
            config=config,
            paas_facade=paas_facade,
            ticket_repo=ticket_repo,
        )

        import asyncio
        result = asyncio.run(poller._process_single_ticket(ticket))  # noqa: SLF001

        assert result == "retention_done"
        # pull_file must NOT be called
        paas_facade.pull_file.assert_not_called()
        # Status transitions: UPLOAD_COMPLETED -> DONE
        status_transitions = [
            (c.args[0], c.args[1])
            for c in ticket_repo.update_status.call_args_list
        ]
        assert ("tf-00000000-0000-0000-0000-000000000001", "UPLOAD_COMPLETED") in status_transitions
        assert ("tf-00000000-0000-0000-0000-000000000001", "DONE") in status_transitions

    # ── timeout path ──────────────────────────────────────────────────

    def test_process_single_ticket_timeout(self) -> None:
        """Timeout: gmt_create + upload_timeout_seconds < now -> mark FAILED."""
        config = self._make_config(upload_timeout_seconds=10)
        ticket = self._make_ticket_record(
            status="CREATED",
            gmt_create=datetime.now() - timedelta(seconds=60),
        )
        paas_facade = MagicMock()
        ticket_repo = MagicMock()

        poller = self._make_poller(
            config=config,
            paas_facade=paas_facade,
            ticket_repo=ticket_repo,
        )

        import asyncio
        result = asyncio.run(poller._process_single_ticket(ticket))  # noqa: SLF001

        assert result == "timed_out"
        # Must mark FAILED with timeout error
        ticket_repo.update_status.assert_called_once_with(
            "tf-00000000-0000-0000-0000-000000000001", "FAILED", "Upload timed out"
        )
        # pull_file must NOT be called
        paas_facade.pull_file.assert_not_called()
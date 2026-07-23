"""文件传输轮询器 — OSS 上传检测 + 设备被动下载触发

每 N 秒扫描 CREATED/UPLOADING 状态的 ticket，
按 transfer_id 粒度抢分布式锁后检测 OSS 文件，
触发 PaasServiceFacade.pull_file() 下载到 device。

依赖通过构造方法注入，由 DI 容器管理生命周期。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from secbaas.community.core.repository.file_transfer_ticket import (
    TicketRecord,
    TicketRepository,
)
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.logger import get_logger
from secbaas.community.spi.file_transfer import FileTransferBackend

log = get_logger("core-scheduler")

# OSS download URL validity: 24 hours for device to complete the download
_DOWNLOAD_URL_EXPIRE_SECONDS = 86400


@dataclass
class FileTransferPollerConfig:
    """文件传输轮询器配置"""

    enabled: bool = True
    lock_expire_seconds: int = 300
    cron_interval_seconds: int = 10
    upload_timeout_seconds: int = 3600
    max_concurrent_tickets: int = 5
    dry_run: bool = False


class FileTransferPoller:
    """文件传输轮询器

    1. 扫描 CREATED/UPLOADING 状态的 ticket
    2. 按 transfer_id 粒度抢 per-ticket 分布式锁（非阻塞）
    3. 检测 OSS 文件存在性
    4. 触发 device pull_file 下载 或 留存模式直接 DONE
    """

    def __init__(
        self,
        config: FileTransferPollerConfig,
        lock_service: DistributedLockService,
        ticket_repo: TicketRepository,
        file_backend: FileTransferBackend,
        paas_facade: PaasServiceFacade,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._ticket_repo = ticket_repo
        self._file_backend = file_backend
        self._paas_facade = paas_facade

    @property
    def name(self) -> str:
        return "file_transfer_poller"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self) -> None:
        start_time = time.monotonic()
        log.info("[FileTransferPoller] Task triggered at %s", datetime.now())

        if not self._config.enabled:
            log.info("[FileTransferPoller] Disabled, skipping")
            return

        if self._config.dry_run:
            log.info(
                "[FileTransferPoller] DRY_RUN mode - skipping. "
                "max_concurrent_tickets=%s",
                self._config.max_concurrent_tickets,
            )
            return

        try:
            all_tickets = self._ticket_repo.list_pending_uploads(
                # Include UPLOAD_COMPLETED and PUSHING as recovery states:
                # if a ticket gets stuck after status transition but before
                # the operation completes (e.g. pull_file fails after
                # update_status), the next poller cycle can retry it.
                statuses=["CREATED", "UPLOADING", "UPLOAD_COMPLETED", "PULLING", "PUSHING"],
                limit=10000,
            )
            # UPLOAD direction: CREATED/UPLOADING/UPLOAD_COMPLETED
            tickets = [t for t in all_tickets if t.direction == "UPLOAD"]
            # DOWNLOAD direction: CREATED/PUSHING (D-19)
            download_tickets = [
                t
                for t in all_tickets
                if t.direction == "DOWNLOAD" and t.status in ("CREATED", "PUSHING")
            ]
        except Exception:
            log.exception("[FileTransferPoller] Failed to query pending uploads")
            return

        if not tickets and not download_tickets:
            log.info("[FileTransferPoller] No pending tickets found")
            return

        # Process tickets concurrently with Semaphore-based concurrency control
        semaphore = asyncio.Semaphore(self._config.max_concurrent_tickets)

        async def _process_with_semaphore(ticket: TicketRecord) -> str:
            async with semaphore:
                if ticket.direction == "DOWNLOAD":
                    return await self._process_download_ticket(ticket)
                return await self._process_single_ticket(ticket)

        all_tickets_for_processing = tickets + download_tickets
        log.info(
            "[FileTransferPoller] Found %d pending tickets",
            len(all_tickets_for_processing),
        )
        results = await asyncio.gather(
            *[_process_with_semaphore(t) for t in all_tickets_for_processing]
        )

        # Aggregate counters
        processed = len(all_tickets_for_processing)
        oss_detected = sum(1 for r in results if r == "oss_detected")
        pull_success = sum(1 for r in results if r == "pull_success")
        failed = sum(1 for r in results if r == "failed")
        timed_out = sum(1 for r in results if r == "timed_out")
        retention_done = sum(1 for r in results if r == "retention_done")
        download_ready = sum(1 for r in results if r == "download_ready")

        duration = time.monotonic() - start_time
        log.info(
            "[FileTransferPoller] Completed: "
            "processed=%d oss_detected=%d pull_success=%d "
            "failed=%d timed_out=%d retention_done=%d download_ready=%d "
            "duration=%.2fs",
            processed,
            oss_detected,
            pull_success,
            failed,
            timed_out,
            retention_done,
            download_ready,
            duration,
        )

    async def _process_single_ticket(self, ticket: TicketRecord) -> str:
        """Process a single ticket asynchronously.

        Returns:
            Result category string: "timed_out", "skipped", "oss_not_ready",
            "retention_done", "pull_success", or "failed".
        """
        transfer_id = ticket.transfer_id

        # Defensive guard: terminal states should never reach the poller
        # through list_pending_uploads (which includes CREATED/UPLOADING/
        # UPLOAD_COMPLETED/PUSHING for recovery), but check
        # anyway to prevent accidental processing of stale tickets
        # (e.g., race with cancel API).
        if ticket.status in ("CANCELLED", "DELETED", "FAILED", "DONE"):
            log.info(
                "[FileTransferPoller] Skipping ticket %s — terminal state: %s",
                transfer_id,
                ticket.status,
            )
            return "skipped"

        log.info(
            "[FileTransferPoller] Processing ticket %s (status=%s)",
            transfer_id,
            ticket.status,
        )

        try:
            # Timeout check: gmt_create + upload_timeout_seconds < now
            if ticket.gmt_create + timedelta(
                seconds=self._config.upload_timeout_seconds
            ) < datetime.now(tz=UTC).replace(tzinfo=None):
                log.warning(
                    "[FileTransferPoller] Ticket %s timed out (created=%s, timeout=%ss)",
                    transfer_id,
                    ticket.gmt_create,
                    self._config.upload_timeout_seconds,
                )
                self._ticket_repo.update_status(
                    transfer_id, "FAILED", "Upload timed out"
                )
                return "timed_out"

            # Per-ticket distributed lock (held through entire processing scope).
            # Uses acquire_lock directly (not the try_lock context manager) so the
            # lock stays held until we explicitly release it below.
            lock_name = f"file_transfer_poller:{transfer_id}"

            def _acquire():
                return self._lock_service.acquire_lock(
                    lock_name=lock_name,
                    expire_seconds=self._config.lock_expire_seconds,
                    block=False,
                )

            lock_ctx = await asyncio.to_thread(_acquire)
            if not lock_ctx.acquired:
                log.info(
                    "[FileTransferPoller] Lock %s not acquired, skipping ticket %s",
                    lock_name,
                    transfer_id,
                )
                return "skipped"

            try:
                # OSS object existence check (offloaded to thread to avoid
                # blocking the async event loop with synchronous network I/O).
                exists = await asyncio.to_thread(
                    self._file_backend.check_object_exists,
                    ticket.fileservice_staging_path,
                )
                if not exists:
                    log.info(
                        "[FileTransferPoller] OSS object not ready for ticket %s",
                        transfer_id,
                    )
                    return "oss_not_ready"

                log.info(
                    "[FileTransferPoller] OSS object detected for ticket %s",
                    transfer_id,
                )

                # Retention mode: device_path IS NULL -> skip pull_file,
                # go directly UPLOAD_COMPLETED -> DONE (no PULLING needed
                # since there is no device to pull to).
                if ticket.device_path is None:
                    log.info(
                        "[FileTransferPoller] Ticket %s is retention mode "
                        "(device_path IS NULL) - skipping pull_file, "
                        "transitioning to DONE",
                        transfer_id,
                    )
                    # Recovery: if ticket is already UPLOAD_COMPLETED (stuck
                    # from a previous cycle), skip the redundant transition.
                    if ticket.status != "UPLOAD_COMPLETED":
                        self._ticket_repo.update_status(
                            transfer_id, "UPLOAD_COMPLETED", None
                        )
                    self._ticket_repo.update_status(transfer_id, "DONE", None)
                    return "retention_done"

                # Normal path: UPLOAD_COMPLETED -> PULLING -> pull_file -> DONE
                # Recovery: if ticket is already UPLOAD_COMPLETED or PULLING
                # (stuck from a previous cycle), skip the redundant transition.
                # PULLING must be excluded here because PULLING → UPLOAD_COMPLETED
                # is not a valid transition (backward rollback not allowed).
                if ticket.status not in ("UPLOAD_COMPLETED", "PULLING"):
                    self._ticket_repo.update_status(
                        transfer_id, "UPLOAD_COMPLETED", None
                    )

                # Transition to PULLING before the pull_file call.
                # Recovery: if ticket is already PULLING (stuck from a
                # previous cycle where pull_file failed after the PULLING
                # transition), skip the redundant status update and retry
                # pull_file directly.
                if ticket.status != "PULLING":
                    self._ticket_repo.update_status(
                        transfer_id, "PULLING", None
                    )

                download_url = await asyncio.to_thread(
                    self._file_backend.generate_download_url,
                    ticket.fileservice_staging_path,
                    _DOWNLOAD_URL_EXPIRE_SECONDS,
                )

                log.info(
                    "[FileTransferPoller] Triggering pull_file for ticket %s "
                    "(device_path=%s)",
                    transfer_id,
                    ticket.device_path,
                )

                await self._paas_facade.pull_file(
                    paas_device_id=ticket.paas_device_id,
                    source_url=download_url,
                    device_path=ticket.device_path,
                )

                self._ticket_repo.update_status(transfer_id, "DONE", None)
                log.info(
                    "[FileTransferPoller] pull_file succeeded for ticket %s",
                    transfer_id,
                )
                return "pull_success"

            finally:
                # Release the per-ticket lock even if processing raised.
                await asyncio.to_thread(
                    self._lock_service.release_lock,
                    lock_name,
                    lock_ctx.lock_holder,
                )

        except Exception as e:
            error_msg = str(e)[:500]
            log.error(
                "[FileTransferPoller] Transient error processing ticket %s: %s. "
                "Skipping for retry in next poller cycle.",
                transfer_id,
                error_msg,
                exc_info=True,
            )
            return "failed"

    async def _process_download_ticket(self, ticket: TicketRecord) -> str:
        """Process a DOWNLOAD direction ticket.

        Returns:
            "timed_out", "skipped", "oss_not_ready", or "download_ready".
        """
        transfer_id = ticket.transfer_id

        # Defensive guard: terminal states should never reach the poller
        # through list_pending_uploads (which includes CREATED/UPLOADING/
        # UPLOAD_COMPLETED/PUSHING for recovery), but check
        # anyway to prevent accidental processing of stale tickets.
        if ticket.status in ("CANCELLED", "DELETED", "FAILED", "DONE"):
            log.info(
                "[FileTransferPoller] Skipping DOWNLOAD ticket %s — terminal state: %s",
                transfer_id,
                ticket.status,
            )
            return "skipped"

        log.info(
            "[FileTransferPoller] Processing DOWNLOAD ticket %s (status=%s)",
            transfer_id,
            ticket.status,
        )

        try:
            # Timeout check (D-18: same upload_timeout_seconds)
            if ticket.gmt_create + timedelta(
                seconds=self._config.upload_timeout_seconds
            ) < datetime.now(tz=UTC).replace(tzinfo=None):
                log.warning(
                    "[FileTransferPoller] DOWNLOAD ticket %s timed out "
                    "(created=%s, timeout=%ss)",
                    transfer_id,
                    ticket.gmt_create,
                    self._config.upload_timeout_seconds,
                )
                self._ticket_repo.update_status(
                    transfer_id, "FAILED", "Download timed out"
                )
                return "timed_out"

            # Per-ticket distributed lock (same pattern as upload)
            lock_name = f"file_transfer_poller:{transfer_id}"

            def _acquire():
                return self._lock_service.acquire_lock(
                    lock_name=lock_name,
                    expire_seconds=self._config.lock_expire_seconds,
                    block=False,
                )

            lock_ctx = await asyncio.to_thread(_acquire)
            if not lock_ctx.acquired:
                log.info(
                    "[FileTransferPoller] Lock %s not acquired, "
                    "skipping DOWNLOAD ticket %s",
                    lock_name,
                    transfer_id,
                )
                return "skipped"

            try:
                # Check OSS object existence (D-17 step 1)
                exists = await asyncio.to_thread(
                    self._file_backend.check_object_exists,
                    ticket.fileservice_staging_path,
                )
                if not exists:
                    log.info(
                        "[FileTransferPoller] OSS object not ready "
                        "for DOWNLOAD ticket %s",
                        transfer_id,
                    )
                    return "oss_not_ready"

                log.info(
                    "[FileTransferPoller] OSS object detected for DOWNLOAD ticket %s",
                    transfer_id,
                )

                # Recovery: if ticket is already PUSHING (stuck from a
                # previous cycle where steps 3-4 failed after step 1),
                # skip the CREATED→PUSHING transition and proceed
                # directly to download URL generation.
                if ticket.status != "PUSHING":
                    # Transition CREATED → PUSHING on first OSS detection
                    # (VALID_TRANSITIONS: ("CREATED", "PUSHING") already exists)
                    self._ticket_repo.update_status(transfer_id, "PUSHING", None)

                # Generate download URL (D-17 step 2) — idempotent
                download_url = await asyncio.to_thread(
                    self._file_backend.generate_download_url,
                    ticket.fileservice_staging_path,
                    _DOWNLOAD_URL_EXPIRE_SECONDS,
                )

                # Write download_url to ticket (D-17 step 3)
                self._ticket_repo.update_urls(transfer_id, download_url=download_url)

                # Transition PUSHING → DONE (D-17 step 4)
                self._ticket_repo.update_status(transfer_id, "DONE", None)

                log.info(
                    "[FileTransferPoller] DOWNLOAD ticket %s completed "
                    "(download_url written)",
                    transfer_id,
                )
                return "download_ready"

            finally:
                await asyncio.to_thread(
                    self._lock_service.release_lock,
                    lock_name,
                    lock_ctx.lock_holder,
                )

        except Exception as e:
            error_msg = str(e)[:500]
            log.error(
                "[FileTransferPoller] Transient error processing DOWNLOAD ticket %s: %s. "
                "Skipping for retry in next poller cycle.",
                transfer_id,
                error_msg,
                exc_info=True,
            )
            return "failed"

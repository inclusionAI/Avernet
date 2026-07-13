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
from datetime import datetime, timedelta

from secbaas.core.repository.file_transfer_ticket import TicketRecord, TicketRepository
from secbaas.core.service.distributed_lock import DistributedLockService
from secbaas.core.service.paas import PaasServiceFacade
from secbaas.logger import get_logger
from secbaas.spi.file_transfer import FileTransferBackend

log = get_logger("core-scheduler")

# OSS download URL validity: 24 hours for device to complete the download
_DOWNLOAD_URL_EXPIRE_SECONDS = 86400


@dataclass
class FileTransferPollerConfig:
    """文件传输轮询器配置"""

    enabled: bool = True
    lock_name: str = "file_transfer_poller_lock"
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
            tickets = self._ticket_repo.list_pending_uploads(
                statuses=["CREATED", "UPLOADING"], limit=10000
            )
        except Exception:
            log.exception("[FileTransferPoller] Failed to query pending uploads")
            return

        if not tickets:
            log.info("[FileTransferPoller] No pending tickets found")
            return

        log.info("[FileTransferPoller] Found %d pending tickets", len(tickets))

        # Process tickets concurrently with Semaphore-based concurrency control
        semaphore = asyncio.Semaphore(self._config.max_concurrent_tickets)

        async def _process_with_semaphore(ticket: TicketRecord) -> str:
            async with semaphore:
                return await self._process_single_ticket(ticket)

        results = await asyncio.gather(
            *[_process_with_semaphore(t) for t in tickets]
        )

        # Aggregate counters
        processed = len(tickets)
        oss_detected = sum(1 for r in results if r == "oss_detected")
        pull_success = sum(1 for r in results if r == "pull_success")
        failed = sum(1 for r in results if r == "failed")
        timed_out = sum(1 for r in results if r == "timed_out")
        retention_done = sum(1 for r in results if r == "retention_done")

        duration = time.monotonic() - start_time
        log.info(
            "[FileTransferPoller] Completed: "
            "processed=%d oss_detected=%d pull_success=%d "
            "failed=%d timed_out=%d retention_done=%d "
            "duration=%.2fs",
            processed,
            oss_detected,
            pull_success,
            failed,
            timed_out,
            retention_done,
            duration,
        )

    async def _process_single_ticket(self, ticket: TicketRecord) -> str:
        """Process a single ticket asynchronously.

        Returns:
            Result category string: "timed_out", "skipped", "oss_not_ready",
            "retention_done", "pull_success", or "failed".
        """
        transfer_id = ticket.transfer_id
        log.info(
            "[FileTransferPoller] Processing ticket %s (status=%s)",
            transfer_id,
            ticket.status,
        )

        try:
            # Timeout check: gmt_create + upload_timeout_seconds < now
            if ticket.gmt_create + timedelta(
                seconds=self._config.upload_timeout_seconds
            ) < datetime.now():
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

            # Per-ticket distributed lock
            lock_acquired = await self._acquire_per_ticket_lock(transfer_id)
            if not lock_acquired:
                return "skipped"

            # OSS object existence check
            if not self._file_backend.check_object_exists(
                ticket.fileservice_staging_path
            ):
                log.info(
                    "[FileTransferPoller] OSS object not ready for ticket %s",
                    transfer_id,
                )
                return "oss_not_ready"

            log.info(
                "[FileTransferPoller] OSS object detected for ticket %s", transfer_id
            )

            # Retention mode: device_path IS NULL -> skip pull_file, go directly to DONE
            if ticket.device_path is None:
                log.info(
                    "[FileTransferPoller] Ticket %s is retention mode "
                    "(device_path IS NULL) - skipping pull_file, "
                    "transitioning to DONE",
                    transfer_id,
                )
                self._ticket_repo.update_status(transfer_id, "UPLOAD_COMPLETED", None)
                self._ticket_repo.update_status(transfer_id, "DONE", None)
                return "retention_done"

            # Normal path: UPLOAD_COMPLETED -> pull_file -> DONE
            self._ticket_repo.update_status(transfer_id, "UPLOAD_COMPLETED", None)

            download_url = self._file_backend.generate_download_url(
                ticket.fileservice_staging_path,
                expire_seconds=_DOWNLOAD_URL_EXPIRE_SECONDS,
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
                "[FileTransferPoller] pull_file succeeded for ticket %s", transfer_id
            )
            return "pull_success"

        except Exception as e:
            error_msg = str(e)[:500]
            log.error(
                "[FileTransferPoller] Ticket %s failed: %s",
                transfer_id,
                error_msg,
                exc_info=True,
            )
            try:
                self._ticket_repo.update_status(transfer_id, "FAILED", error_msg)
            except Exception:
                log.exception(
                    "[FileTransferPoller] Failed to mark ticket %s as FAILED",
                    transfer_id,
                )
            return "failed"

    async def _acquire_per_ticket_lock(self, transfer_id: str) -> bool:
        """Acquire a per-ticket distributed lock for cluster isolation.

        Wraps the synchronous try_lock context manager in asyncio.to_thread().

        Args:
            transfer_id: The ticket's unique transfer ID.

        Returns:
            True if the lock was acquired, False otherwise.
        """
        lock_name = f"file_transfer_poller:{transfer_id}"

        def _try_acquire() -> bool:
            with self._lock_service.try_lock(
                lock_name=lock_name,
                expire_seconds=self._config.lock_expire_seconds,
                block=False,
            ) as lock_ctx:
                return lock_ctx.acquired

        acquired = await asyncio.to_thread(_try_acquire)
        if not acquired:
            log.info(
                "[FileTransferPoller] Lock %s not acquired, skipping ticket %s",
                lock_name,
                transfer_id,
            )
        return acquired
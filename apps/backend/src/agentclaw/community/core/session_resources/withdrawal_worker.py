"""Single-flight durable delivery; downstream availability never blocks delete."""

import asyncio
import logging
import time

from agentclaw.community.core.repository.protocols.platform import (
    ResourceWithdrawalRepositoryProtocol,
)
from agentclaw.community.di.tc_resource_withdrawal_config import (
    ResourceWithdrawalConfig,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalEvent,
    ResourceWithdrawalPublisherPlugin,
    WithdrawalDeliveryError,
    WithdrawalReceipt,
)

log = logging.getLogger("session_resource.withdrawal")


class ResourceWithdrawalWorker(LifecycleBase):
    def __init__(
        self,
        repository: ResourceWithdrawalRepositoryProtocol,
        publisher: ResourceWithdrawalPublisherPlugin,
        config: ResourceWithdrawalConfig,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._config = config
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def startup(self) -> None:
        if not self._config.enabled:
            log.info("tc.withdrawal.paused tenant=%s", self._config.tenant)
            return
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="tc-resource-withdrawal")

    async def shutdown(self) -> None:
        self._stop.set()
        if self._task is not None:
            # Drain the single in-flight synchronous request before DB teardown.
            # Process death instead leaves the lease for another worker to recover.
            await self._task
            self._task = None

    async def run_once(self) -> bool:
        record = await asyncio.to_thread(
            self._repository.claim,
            tenant=self._config.tenant,
            lease_seconds=self._config.lease_seconds,
        )
        if record is None:
            return False
        status, code, delay = "accepted", "", 0
        try:
            if record.retry_count > self._config.max_attempts:
                raise WithdrawalDeliveryError(
                    "retry_exhausted_crash_recovery", retryable=False
                )
            receipt = await asyncio.to_thread(
                self._publisher.publish,
                ResourceWithdrawalEvent.for_resource(record.res_id),
            )
            if (
                not isinstance(receipt, WithdrawalReceipt)
                or receipt.event_id != record.event_id
            ):
                raise WithdrawalDeliveryError("invalid_receipt", retryable=False)
        except WithdrawalDeliveryError as exc:
            code = exc.code
            if exc.retryable and record.retry_count < self._config.max_attempts:
                status = "pending"
                delay = min(
                    self._config.retry_max_seconds,
                    self._config.retry_base_seconds
                    * 2 ** min(record.retry_count - 1, 20),
                )
            else:
                status = "blocked"
                if exc.retryable:
                    code = f"retry_exhausted_{code}"
        except Exception:
            status, code = "blocked", "unexpected_delivery_error"
        # Persist failures must propagate. Never convert a DB error to success.
        saved = await asyncio.to_thread(
            self._repository.finish,
            record,
            status=status,
            delay_seconds=delay,
            error_code=code,
        )
        if not saved:
            log.warning("tc.withdrawal.lease_lost event_id=%s", record.event_id)
        elif status == "blocked":
            log.error(
                "tc.withdrawal.blocked event_id=%s code=%s attempts=%s",
                record.event_id,
                code,
                record.attempts,
            )
        else:
            log.info(
                "tc.withdrawal.delivery event_id=%s status=%s code=%s attempts=%s",
                record.event_id,
                status,
                code,
                record.attempts,
            )
        return True

    async def _run(self) -> None:
        next_stats = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() >= next_stats:
                    stats = await asyncio.to_thread(
                        self._repository.stats, tenant=self._config.tenant
                    )
                    log.info(
                        "tc.withdrawal.backlog tenant=%s stats=%s",
                        self._config.tenant,
                        stats,
                    )
                    next_stats = time.monotonic() + 60
                if await self.run_once():
                    continue
            except Exception:
                # No raw exception/response/credentials in logs. Existing lease
                # and event remain durable; operators inspect DB + infra health.
                log.error(
                    "tc.withdrawal.storage_failure tenant=%s", self._config.tenant
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._config.poll_seconds
                )
            except TimeoutError:
                pass

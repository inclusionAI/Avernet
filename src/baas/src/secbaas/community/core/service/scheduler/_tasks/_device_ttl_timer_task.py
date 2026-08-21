"""设备 TTL 续期 + 探活定时 task

每 N 秒扫描符合条件（ACTIVE + ARCA + 有 sandbox_id）的全部个人 bot 设备
和服务 bot 设备，对每组执行续期和探活。
按 id keyset 分页，``id > last_id`` 保证即使 TTL 续期失败/相同，也能逐页
推进，避免 queue 头部饿死其它设备。

每轮 full-drain：cron 路径抢分布式锁后把整个队列扫完（ac_binding +
baas 两组并行跑，页内并发续期）；HTTP 手动触发路径（run_direct）不抢锁，
直接执行一轮，并可用 batch_size / max_pages 控制本轮规模。

依赖通过构造方法注入，由 DI 容器管理生命周期。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from secbaas.community.core.repository.device_binding import DeviceBindingRepository
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.service.health_check.sandbox import (
    SandboxDeviceRouter,
    TableType,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

log = get_logger("core-scheduler")


@dataclass
class DeviceTtlTimerTaskConfig:
    """设备 TTL 定时 task 配置"""

    enabled: bool = True
    lock_name: str = "device_ttl_timer_lock"
    # 锁过期时间须 < cron 间隔：租约在下一轮触发前到期释放，允许其它机器
    # 在轮次间抢到锁，避免同一实例每轮都重复占锁。
    lock_expire_seconds: int = 1750
    cron_interval_seconds: int = 1800
    batch_size: int = 100
    dry_run: bool = False
    # 每页内并发续期的最大并发数。并发越高一轮 full-drain 越快完成，
    # 但对 PaaS/数据库的瞬时压力也越大。
    max_page_concurrency: int = 10
    # 单页查询 DB 失败时的重试次数（指数退避），吸收瞬时 DB 抖动。
    query_retries: int = 3

    def resolved_lock_name(self) -> str:
        """返回按环境作用域的分布式锁名。

        在配置的 ``lock_name`` 基准上追加环境后缀（如 ``_pre``/``_prod``），
        使 pre 与 prod 使用不同的锁名，避免共享同一把分布式锁。
        """
        env = get_current_env()
        return f"{self.lock_name}_{env}"


@dataclass
class DeviceTtlGroupOutcome:
    """一个分组的执行结果统计（续期、探活、成功/失败计数）。"""

    records_processed: int = 0
    pages: int = 0
    renewed: int = 0
    warned: int = 0
    # 按记录结果归类：renew 成功计 success；renew 失败/异常计 failure。
    success: int = 0
    failure: int = 0

    @property
    def total(self) -> int:
        return self.success + self.failure


@dataclass
class DeviceTtlRunReport:
    """一轮 TTL 续期任务结束后生成的完整执行报告。"""

    run_uuid: str
    trigger: str = ""  # "cron" | "manual"
    duration_seconds: float = 0.0
    personal: DeviceTtlGroupOutcome = None  # type: ignore[assignment]
    service: DeviceTtlGroupOutcome = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.personal is None:
            self.personal = DeviceTtlGroupOutcome()
        if self.service is None:
            self.service = DeviceTtlGroupOutcome()

    @property
    def total_processed(self) -> int:
        return self.personal.records_processed + self.service.records_processed

    @property
    def total_success(self) -> int:
        return self.personal.success + self.service.success

    @property
    def total_failure(self) -> int:
        return self.personal.failure + self.service.failure

    @property
    def total_renewed(self) -> int:
        return self.personal.renewed + self.service.renewed

    @property
    def total_warned(self) -> int:
        return self.personal.warned + self.service.warned

    @property
    def total_pages(self) -> int:
        return self.personal.pages + self.service.pages

    def to_log(self) -> str:
        """单行报告：便于 monitor 采集（逗号分隔、无空格时间、缺省 -）。"""

        def _g(o: DeviceTtlGroupOutcome) -> str:
            return (
                f"{o.records_processed}-{o.pages}-{o.renewed}-{o.warned}-"
                f"{o.success}-{o.failure}"
            )

        return (
            f"ttl_renew_report,uuid={self.run_uuid},trigger={self.trigger},duration={self.duration_seconds:.2f},"
            f"personal={_g(self.personal)},service={_g(self.service)},total_processed={self.total_processed},total_success={self.total_success},"
            f"total_failure={self.total_failure},total_renewed={self.total_renewed},total_warned={self.total_warned},total_pages={self.total_pages}"
        )


class DeviceTtlTimerTask:
    """设备 TTL 续期 + 探活 task

    cron 走 ``run()``（带分布式锁，避免多实例并发同一轮）；
    手动触发走 ``run_direct()``（不抢锁，立即执行一轮）。
    同一进程内用 ``_running`` 标志防止 run()/run_direct() 并发重复扫描。
    """

    def __init__(
        self,
        config: DeviceTtlTimerTaskConfig,
        lock_service: DistributedLockService,
        binding_repo: DeviceBindingRepository,
        router: SandboxDeviceRouter,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._binding_repo = binding_repo
        self._router = router
        self._running = False

    @property
    def name(self) -> str:
        return "device_ttl_timer"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self, *, run_uuid: str | None = None) -> dict | None:
        """定时路径：先抢分布式锁，抢到才执行一轮完整续期。

        返回本轮摘要（{personal: {...}, service: {...}, duration}），
        未执行（disabled/dry_run/已在跑/未抢到锁）返回 ``None``。
        """
        run_uuid = run_uuid or str(uuid4())
        log.info(
            "[DeviceTtlTimer] Cron trigger at %s run_uuid=%s", datetime.now(), run_uuid
        )

        if not self._config.enabled:
            log.info("[DeviceTtlTimer] Disabled, skipping")
            return None

        if self._config.dry_run:
            log.info(
                "[DeviceTtlTimer] DRY_RUN mode - skipping. batch_size=%s",
                self._config.batch_size,
            )
            return None

        if self._running:
            log.info("[DeviceTtlTimer] Another run in progress, skipping cron trigger")
            return None

        lock_name = self._config.resolved_lock_name()
        with self._lock_service.try_lock(
            lock_name=lock_name,
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                log.info(
                    "[DeviceTtlTimer] Lock %s not acquired, skipping",
                    lock_name,
                )
                return None

            log.info("[DeviceTtlTimer] Lock %s acquired", lock_name)
            self._running = True
            try:
                return await self._run_once(run_uuid=run_uuid)
            finally:
                self._running = False

    async def run_direct(
        self,
        *,
        run_uuid: str | None = None,
        batch_size: int | None = None,
        max_pages: int | None = None,
    ) -> dict | None:
        """手动触发路径：不抢锁，直接执行一轮续期。

        ``batch_size`` 覆盖每页条数；``max_pages`` 限制每个分组 while 循环
        最多推进的页数（None = 跑完整队列）。仅对本次手动触发生效。
        返回本轮扫描 summary；disabled 或已有并发运行返回 ``None``。
        """
        run_uuid = run_uuid or str(uuid4())
        log.info(
            "[DeviceTtlTimer] Manual trigger at %s run_uuid=%s",
            datetime.now(),
            run_uuid,
        )

        if not self._config.enabled:
            log.info("[DeviceTtlTimer] Disabled, skipping")
            return None

        if self._running:
            log.info(
                "[DeviceTtlTimer] Another run in progress, skipping manual trigger"
            )
            return None

        self._running = True
        try:
            return await self._run_once(
                run_uuid=run_uuid,
                batch_size=batch_size,
                max_pages=max_pages,
                trigger="manual",
            )
        finally:
            self._running = False

    def trigger_async(
        self,
        *,
        run_uuid: str | None = None,
        batch_size: int | None = None,
        max_pages: int | None = None,
    ) -> str:
        """异步触发（fire-and-forget）：后台调度一轮 ``run_direct``。

        用于 ``renew-ttl-trigger`` 手动触发路径，接口不阻塞等待整表扫描
        完成，立即返回 run_uuid。后台任务的并发编排（asyncio.create_task）
        属于核心服务层，不应出现在 adapter。

        Returns:
            本轮 run_uuid。
        """
        run_uuid = run_uuid or str(uuid4())

        async def _background() -> None:
            try:
                await self.run_direct(
                    run_uuid=run_uuid,
                    batch_size=batch_size,
                    max_pages=max_pages,
                )
            except Exception as e:  # pragma: no cover - defensive
                log.error(
                    "[DeviceTtlTimer] Background trigger %s failed: %s",
                    run_uuid,
                    e,
                )

        task = asyncio.create_task(_background())
        task.add_done_callback(self._handle_trigger_done)
        log.info("[DeviceTtlTimer] Background trigger scheduled run_uuid=%s", run_uuid)
        return run_uuid

    def _handle_trigger_done(self, task: asyncio.Task) -> None:
        """消费后台任务异常，避免未观测异常告警；run_direct 已在 _background 内捕获。"""
        try:
            task.result()
        except Exception as e:  # pragma: no cover - defensive
            log.error("[DeviceTtlTimer] Background trigger task error: %s", e)

    async def _run_once(
        self,
        *,
        run_uuid: str,
        trigger: str = "scheduler",
        batch_size: int | None = None,
        max_pages: int | None = None,
    ) -> DeviceTtlRunReport:
        """执行一轮：个人 ac_binding + 服务 baas 各按 keyset 分页续期 + 探活。

        两个分组并行跑；任一分组 DB 持续失败只中断该组，不影响另一组。
        成功执行后打印并返回完整报告对象 ``DeviceTtlRunReport``。
        """
        start_time = time.monotonic()
        effective_batch = batch_size or self._config.batch_size
        sem = asyncio.Semaphore(self._config.max_page_concurrency)
        page_limit = max_pages

        personal, service = await asyncio.gather(
            self._scan_personal(
                run_uuid=run_uuid,
                effective_batch=effective_batch,
                page_limit=page_limit,
                sem=sem,
            ),
            self._scan_baas(
                run_uuid=run_uuid,
                effective_batch=effective_batch,
                page_limit=page_limit,
                sem=sem,
            ),
        )

        duration = time.monotonic() - start_time
        report = DeviceTtlRunReport(
            run_uuid=run_uuid,
            trigger=trigger,
            duration_seconds=duration,
            personal=personal,
            service=service,
        )
        log.info("[DeviceTtlTimer] Report: %s", report.to_log())
        return report

    async def _scan_personal(
        self,
        *,
        run_uuid: str,
        effective_batch: int,
        page_limit: int | None,
        sem: asyncio.Semaphore,
    ) -> DeviceTtlGroupOutcome:
        """扫描个人 ac_entity_device_binding 分组（keyset 分页 + 页内并发）。"""
        return await self._scan_bindings(
            run_uuid=run_uuid,
            effective_batch=effective_batch,
            page_limit=page_limit,
            sem=sem,
            table_type=TableType.AC_BINDING.value,
        )

    async def _scan_baas(
        self,
        *,
        run_uuid: str,
        effective_batch: int,
        page_limit: int | None,
        sem: asyncio.Semaphore,
    ) -> DeviceTtlGroupOutcome:
        """扫描服务 baas_device 分组（keyset 分页 + 页内并发）。"""
        return await self._scan_devices(
            run_uuid=run_uuid,
            effective_batch=effective_batch,
            page_limit=page_limit,
            sem=sem,
            table_type=TableType.BAAS.value,
        )

    async def _scan_bindings(
        self,
        *,
        run_uuid: str,
        effective_batch: int,
        page_limit: int | None,
        sem: asyncio.Semaphore,
        table_type: str,
    ) -> DeviceTtlGroupOutcome:
        """keyset 分页扫描 ac_entity_device_binding，返回该组统计对象。"""
        outcome = DeviceTtlGroupOutcome()
        last_id: int = 0
        page_count: int = 0

        while True:
            if page_limit is not None and page_count >= page_limit:
                log.info(
                    "[DeviceTtlTimer] %s loop stopped at max_pages=%s",
                    table_type,
                    page_limit,
                )
                break
            page_count += 1

            rows: list | None = None
            for attempt in range(1, self._config.query_retries + 1):
                try:
                    rows = self._binding_repo.list_bindings_by_id_asc(
                        last_id=last_id, limit=effective_batch
                    )
                    break
                except Exception as e:
                    log.warning(
                        "[DeviceTtlTimer] %s query attempt %d/%d failed: %s",
                        table_type,
                        attempt,
                        self._config.query_retries,
                        e,
                    )
                    if attempt < self._config.query_retries:
                        await asyncio.sleep(0.2)

            if rows is None:
                log.error(
                    "[DeviceTtlTimer] %s query failed after %d retries, aborting group",
                    table_type,
                    self._config.query_retries,
                )
                break

            if not rows:
                break

            outcome.pages += 1
            outcome.records_processed += len(rows)
            await self._process_page(
                rows=rows,
                table_type=table_type,
                run_uuid=run_uuid,
                sem=sem,
                outcome=outcome,
                row_id=lambda row: row.id,
            )

            next_id = rows[-1].id
            if next_id <= last_id:
                break
            last_id = next_id

        return outcome

    async def _scan_devices(
        self,
        *,
        run_uuid: str,
        effective_batch: int,
        page_limit: int | None,
        sem: asyncio.Semaphore,
        table_type: str,
    ) -> DeviceTtlGroupOutcome:
        """keyset 分页扫描 baas_device，返回该组统计对象。"""
        outcome = DeviceTtlGroupOutcome()
        last_id: int = 0
        page_count: int = 0

        while True:
            if page_limit is not None and page_count >= page_limit:
                log.info(
                    "[DeviceTtlTimer] %s loop stopped at max_pages=%s",
                    table_type,
                    page_limit,
                )
                break
            page_count += 1

            rows: list | None = None
            for attempt in range(1, self._config.query_retries + 1):
                try:
                    rows = self._binding_repo.list_baas_devices_by_id_asc(
                        last_id=last_id, limit=effective_batch
                    )
                    break
                except Exception as e:
                    log.warning(
                        "[DeviceTtlTimer] %s query attempt %d/%d failed: %s",
                        table_type,
                        attempt,
                        self._config.query_retries,
                        e,
                    )
                    if attempt < self._config.query_retries:
                        await asyncio.sleep(0.2)

            if rows is None:
                log.error(
                    "[DeviceTtlTimer] %s query failed after %d retries, aborting group",
                    table_type,
                    self._config.query_retries,
                )
                break

            if not rows:
                break

            outcome.pages += 1
            outcome.records_processed += len(rows)
            await self._process_page(
                rows=rows,
                table_type=table_type,
                run_uuid=run_uuid,
                sem=sem,
                outcome=outcome,
                row_id=lambda row: row["id"],
            )

            next_id = rows[-1]["id"]
            if next_id <= last_id:
                break
            last_id = next_id

        return outcome

    async def _process_page(
        self,
        *,
        rows: list,
        table_type: str,
        run_uuid: str,
        sem: asyncio.Semaphore,
        outcome: DeviceTtlGroupOutcome,
        row_id: Callable,
    ) -> None:
        """并发处理一页记录（页内 bounded concurrency），累计 outcome。"""

        async def _work_one(row) -> None:
            async with sem:
                table_id = row_id(row)
                try:
                    renew_result = await self._router.renew_ttl(
                        table_type=table_type,
                        table_id=table_id,
                        run_uuid=run_uuid,
                    )
                    if renew_result.success:
                        outcome.renewed += 1
                        outcome.success += 1
                    else:
                        outcome.failure += 1
                        log.warning(
                            "[DeviceTtlTimer] %s device %s renew_ttl failed: %s",
                            table_type,
                            table_id,
                            renew_result.error,
                        )
                except Exception as e:
                    outcome.failure += 1
                    log.error(
                        "[DeviceTtlTimer] %s device %s renew_ttl error: %s",
                        table_type,
                        table_id,
                        e,
                        exc_info=True,
                    )
                    return

                try:
                    await self._router.warn_device(
                        table_type=table_type,
                        table_id=table_id,
                    )
                    outcome.warned += 1
                except Exception as e:
                    log.error(
                        "[DeviceTtlTimer] %s device %s warn_device error: %s",
                        table_type,
                        table_id,
                        e,
                    )

        await asyncio.gather(*(_work_one(row) for row in rows))

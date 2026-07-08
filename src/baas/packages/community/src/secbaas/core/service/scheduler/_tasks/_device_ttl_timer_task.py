"""设备 TTL 续期 + 探活定时 task

每 N 秒扫描 TTL 过期时间最小的 top-N 设备，
对个人 bot 设备和服务 bot 设备分别执行续期和探活。

依赖通过构造方法注入，由 DI 容器管理生命周期。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from secbaas.core.repository.device_binding import DeviceBindingRepository
from secbaas.core.service.distributed_lock import DistributedLockService
from secbaas.core.service.health_check.sandbox import SandboxDeviceRouter, TableType
from secbaas.logger import get_logger

log = get_logger("core-scheduler")


@dataclass
class DeviceTtlTimerTaskConfig:
    """设备 TTL 定时 task 配置"""

    enabled: bool = True
    lock_name: str = "device_ttl_timer_lock"
    lock_expire_seconds: int = 300
    cron_interval_seconds: int = 300
    batch_size: int = 100
    dry_run: bool = False


class DeviceTtlTimerTask:
    """设备 TTL 续期 + 探活 task

    1. 获取分布式锁（非阻塞）
    2. 查询两类 top-N 设备
    3. 逐台续期 + 探活
    4. 释放锁
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

    @property
    def name(self) -> str:
        return "device_ttl_timer"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self) -> None:
        start_time = time.monotonic()
        log.info("[DeviceTtlTimer] Task triggered at %s", datetime.now())

        if not self._config.enabled:
            log.info("[DeviceTtlTimer] Disabled, skipping")
            return

        if self._config.dry_run:
            log.info(
                "[DeviceTtlTimer] DRY_RUN mode - skipping. batch_size=%s",
                self._config.batch_size,
            )
            return

        with self._lock_service.try_lock(
            lock_name=self._config.lock_name,
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                log.info(
                    "[DeviceTtlTimer] Lock %s not acquired, skipping",
                    self._config.lock_name,
                )
                return

            log.info("[DeviceTtlTimer] Lock %s acquired", self._config.lock_name)

            # ── 个人 bot 设备 ────────────────────────────────────
            personal_renewed = 0
            personal_warned = 0
            personal_failed = 0

            try:
                bindings = self._binding_repo.list_bindings_by_ttl_asc(
                    limit=self._config.batch_size
                )
                log.info(
                    "[DeviceTtlTimer] Found %d personal bot devices (batch=%d)",
                    len(bindings),
                    self._config.batch_size,
                )
            except Exception:
                log.exception("[DeviceTtlTimer] Failed to query personal bot devices")
                bindings = []

            for binding in bindings:
                table_id = binding.id
                try:
                    renew_result = await self._router.renew_ttl(
                        table_type=TableType.AC_BINDING.value,
                        table_id=table_id,
                    )
                    if renew_result.success:
                        personal_renewed += 1
                    else:
                        personal_failed += 1
                        log.warning(
                            "[DeviceTtlTimer] Personal device %s renew_ttl failed: %s",
                            table_id,
                            renew_result.error,
                        )
                except Exception as e:
                    personal_failed += 1
                    log.error(
                        "[DeviceTtlTimer] Personal device %s renew_ttl error: %s",
                        table_id,
                        e,
                        exc_info=True,
                    )
                    continue

                try:
                    await self._router.warn_device(
                        table_type=TableType.AC_BINDING.value,
                        table_id=table_id,
                    )
                    personal_warned += 1
                except Exception as e:
                    log.error(
                        "[DeviceTtlTimer] Personal device %s warn_device error: %s",
                        table_id,
                        e,
                    )

            # ── 服务 bot 设备 ────────────────────────────────────
            service_renewed = 0
            service_warned = 0
            service_failed = 0

            try:
                devices = self._binding_repo.list_baas_devices_by_ttl_asc(
                    limit=self._config.batch_size
                )
                log.info(
                    "[DeviceTtlTimer] Found %d service bot devices (batch=%d)",
                    len(devices),
                    self._config.batch_size,
                )
            except Exception:
                log.exception("[DeviceTtlTimer] Failed to query service bot devices")
                devices = []

            for device in devices:
                table_id = device["id"]
                try:
                    renew_result = await self._router.renew_ttl(
                        table_type=TableType.BAAS.value,
                        table_id=table_id,
                    )
                    if renew_result.success:
                        service_renewed += 1
                    else:
                        service_failed += 1
                        log.warning(
                            "[DeviceTtlTimer] Service device %s renew_ttl failed: %s",
                            table_id,
                            renew_result.error,
                        )
                except Exception as e:
                    service_failed += 1
                    log.error(
                        "[DeviceTtlTimer] Service device %s renew_ttl error: %s",
                        table_id,
                        e,
                        exc_info=True,
                    )
                    continue

                try:
                    await self._router.warn_device(
                        table_type=TableType.BAAS.value,
                        table_id=table_id,
                    )
                    service_warned += 1
                except Exception as e:
                    log.error(
                        "[DeviceTtlTimer] Service device %s warn_device error: %s",
                        table_id,
                        e,
                    )

            duration = time.monotonic() - start_time
            log.info(
                "[DeviceTtlTimer] Completed: "
                "personal_renewed=%d personal_warned=%d personal_failed=%d "
                "service_renewed=%d service_warned=%d service_failed=%d "
                "duration=%.2fs",
                personal_renewed,
                personal_warned,
                personal_failed,
                service_renewed,
                service_warned,
                service_failed,
                duration,
            )

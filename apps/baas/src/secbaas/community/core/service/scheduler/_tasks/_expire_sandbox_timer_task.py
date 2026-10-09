"""过期 ACK pod 沙箱定时 task

> **Only effective for Aliyun ACK-created sandboxes.** This task handles only
> devices whose sandbox was created by Aliyun ACK (Kubernetes Pod). Sandboxes
> created via the enterprise `arca_sdk` (Aliyun official ARC SDK) have their
> lifecycle and expiry managed by the ARC platform itself, and are **NOT routed
> through this task**, to avoid this task destroying sandboxes still within the
> ARC platform lease. The gate checks ``arca_provider`` and only executes when
> ``plugins.sandbox.arca == "aliyun_ack"``.

每 N 秒扫描 baas_device 中已到期（derived deadline <= now + grace）的 ACK pod
服务设备，对绑定到该设备的 bot 走高级别 BotManageService.stop_bot 流程停 bot：
创建 STOP publish、把 bot 状态置为 STOPPING，并沿用 publish 流程销毁底层
Kubernetes Pod（404 幂等）、把设备记录置为 STOPPED。bot 状态与设备状态由此
在同一条生命周期链路上保持一致。

deadline 由每 bot 的 extra_config.deploy_config.ttl_in_minutes（缺失回退业务
默认 default_ttl_minutes）派生。按 id keyset 分页，``id > last_id`` 保证逐页
推进、整轮 drain 整个到期队列。

cron 路径抢环境作用域分布式锁，多实例下同一时刻只有一个实例执行；页内以
bounded concurrency 停 bot。依赖通过构造方法注入，由 DI 容器管理生命周期。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from secbaas.community.api.bot_manage import BotManageService
from secbaas.community.api.config_manage import SystemConfigManageService
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.service.config import SystemConfigKey
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

log = get_logger("core-scheduler")

#: Delimiters for the whitelist conf_value: comma or newline (incl. CR).
_WHITELIST_DELIMITERS = re.compile(r"[,\n\r]+")


def parse_whitelist_bot_uuids(conf_value: str | None) -> set[str]:
    """Parse ``conf_value`` into a set of trimmed, non-empty bot_uuids.

    Delimiters are commas or newlines (mixable); each token is stripped; empty
    tokens are ignored. ``None`` / whitespace / no valid tokens -> empty set.
    """
    if not conf_value:
        return set()
    tokens = _WHITELIST_DELIMITERS.split(conf_value)
    return {token.strip() for token in tokens if token.strip()}


@dataclass
class ExpireSandboxTimerTaskConfig:
    """过期沙箱定时 task 配置"""

    enabled: bool = False
    # ARCA sandbox provider variant (plugins.sandbox.arca). This task only takes
    # effect for "aliyun_ack"; enterprise "arca_sdk" sandboxes are reclaimed by
    # the ARC platform itself and are not allowed through.
    arca_provider: str = "stub"
    lock_name: str = "expire_sandbox_timer_lock"
    # 锁过期时间须 < cron 间隔：租约在下一轮触发前到期释放，允许其它机器
    # 在轮次间抢到锁。
    lock_expire_seconds: int = 1750
    cron_interval_seconds: int = 86400
    batch_size: int = 100
    max_page_concurrency: int = 10
    query_retries: int = 3
    dry_run: bool = False
    # 到期判定余量（秒），抵消时钟偏差 / 边界 race。
    grace_seconds: int = 0
    # 业务默认 TTL（分钟），extra_config.deploy_config.ttl_in_minutes 缺失时回退。
    default_ttl_minutes: int = 10080
    # 停 bot 时记录的 modifier，便于审计。
    modifier: str = "expire_sandbox_timer"

    def resolved_lock_name(self) -> str:
        """返回按环境作用域的分布式锁名（追加环境后缀，隔离 pre/prod）。"""
        env = get_current_env()
        return f"{self.lock_name}_{env}"

    def provider_is_aliyun_ack(self) -> bool:
        """Whether this task may take effect for the configured ARCA provider.

        Only sandboxes created by Aliyun ACK are reclaimed by this task.
        Enterprise ``arca_sdk`` sandboxes (managed by the ARC platform) are
        reclaimed by the platform itself; this task must not destroy them, so it
        only proceeds when the ARCA provider is ``aliyun_ack``.
        """
        return self.arca_provider == "aliyun_ack"


@dataclass
class ExpireSandboxRunReport:
    """一轮过期扫描结束后的完整执行报告。"""

    run_uuid: str
    duration_seconds: float = 0.0
    scanned: int = 0
    stopped: int = 0
    failed: int = 0
    skipped: int = 0
    pages: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    def to_log(self) -> str:
        return (
            f"expire_sandbox_report,uuid={self.run_uuid},duration={self.duration_seconds:.2f},"
            f"scanned={self.scanned},stopped={self.stopped},failed={self.failed},"
            f"skipped={self.skipped},pages={self.pages}"
        )


class ExpireSandboxTimerTask:
    """过期 ACK pod 沙箱 task

    cron 走 ``run()``（带分布式锁，避免多实例并发同一轮）。
    同一进程内用 ``_running`` 标志防止并发重复扫描。
    """

    def __init__(
        self,
        config: ExpireSandboxTimerTaskConfig,
        lock_service: DistributedLockService,
        device_repo: DeviceRepository,
        bot_manage_service: BotManageService,
        bot_repo: BotRepository,
        bot_device_rel_repo: BotDeviceRelRepository,
        system_config_service: SystemConfigManageService,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._device_repo = device_repo
        self._bot_manage_service = bot_manage_service
        self._bot_repo = bot_repo
        self._bot_device_rel_repo = bot_device_rel_repo
        self._system_config_service = system_config_service
        self._running = False

    @property
    def name(self) -> str:
        return "expire_sandbox_timer"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self, *, run_uuid: str | None = None) -> dict | None:
        """定时路径：先抢分布式锁，抢到才执行一轮完整过期扫描。

        返回本轮摘要（{scanned, stopped, failed, pages, duration}），
        未执行（disabled/dry_run/已在跑/未抢到锁）返回 ``None``。
        """
        run_uuid = run_uuid or str(uuid4())
        log.info(
            "[ExpireSandbox] Cron trigger at %s run_uuid=%s", datetime.now(), run_uuid
        )

        if not self._config.enabled:
            log.info("[ExpireSandbox] Disabled, skipping")
            return None

        if not self._config.provider_is_aliyun_ack():
            log.info(
                "[ExpireSandbox] ARCA provider=%s is not aliyun_ack, skipping "
                "(task only applies to Aliyun ACK-created sandboxes)",
                self._config.arca_provider,
            )
            return None

        if self._config.dry_run:
            log.info(
                "[ExpireSandbox] DRY_RUN mode - skipping (no destroy/stop). "
                "batch_size=%s",
                self._config.batch_size,
            )
            return None

        if self._running:
            log.info("[ExpireSandbox] Another run in progress, skipping cron trigger")
            return None

        lock_name = self._config.resolved_lock_name()
        with self._lock_service.try_lock(
            lock_name=lock_name,
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                log.info("[ExpireSandbox] Lock %s not acquired, skipping", lock_name)
                return None

            log.info("[ExpireSandbox] Lock %s acquired", lock_name)
            self._running = True
            try:
                return await self._run_once(run_uuid=run_uuid)
            finally:
                self._running = False

    async def _run_once(self, *, run_uuid: str) -> ExpireSandboxRunReport:
        """执行一轮：keyset 分页扫描到期设备，页内 bounded concurrency 停 bot。

        每个到期设备都通过高级别 ``BotManageService.stop_bot`` 流程停 bot
        （创建 STOP publish → 状态转 STOPPING → 销毁设备），保证 bot 状态与设备
        状态一致，而不是只把设备置为 STOPPED。
        """
        start_time = time.monotonic()
        effective_batch = self._config.batch_size
        sem = asyncio.Semaphore(self._config.max_page_concurrency)

        whitelist = self._load_whitelist()

        last_id: int = 0
        page_count: int = 0
        scanned: int = 0
        stopped: int = 0
        failed: int = 0
        skipped: int = 0
        skipped_reasons: dict[str, int] = {}

        def _mark_skip(reason: str) -> None:
            nonlocal skipped
            skipped += 1
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

        while True:
            rows: list | None = None
            for attempt in range(1, self._config.query_retries + 1):
                try:
                    rows = self._device_repo.list_expired_paginated(
                        last_id=last_id,
                        limit=effective_batch,
                        grace_seconds=self._config.grace_seconds,
                        default_ttl_minutes=self._config.default_ttl_minutes,
                    )
                    break
                except Exception as e:
                    log.warning(
                        "[ExpireSandbox] query attempt %d/%d failed: %s",
                        attempt,
                        self._config.query_retries,
                        e,
                    )
                    if attempt < self._config.query_retries:
                        await asyncio.sleep(0.2)

            if rows is None:
                log.error(
                    "[ExpireSandbox] query failed after %d retries, aborting run",
                    self._config.query_retries,
                )
                break

            if not rows:
                break

            page_count += 1
            scanned += len(rows)

            async def _stop_one(row: dict) -> None:
                nonlocal stopped, failed
                async with sem:
                    tenant = row.get("tenant")
                    device_uuid = row.get("device_uuid")
                    env = row.get("env")
                    if not tenant or not device_uuid or not env:
                        log.warning(
                            "[ExpireSandbox] row missing tenant/device_uuid/env, skip id=%s",
                            row.get("id"),
                        )
                        _mark_skip("missing_identity")
                        return
                    try:
                        bot_uuid = self._resolve_bot_uuid(tenant, env, device_uuid)
                        if bot_uuid is None:
                            log.warning(
                                "[ExpireSandbox] no active bot bound to device %s, skip",
                                device_uuid,
                            )
                            _mark_skip("no_bot")
                            return
                        if bot_uuid in whitelist:
                            log.info(
                                "[ExpireSandbox] bot %s whitelisted, skip expire",
                                bot_uuid,
                            )
                            _mark_skip("whitelisted")
                            return
                        await self._bot_manage_service.stop_bot(
                            tenant=tenant,
                            bot_uuid=bot_uuid,
                            operator=self._config.modifier,
                            request_id=str(uuid4()),
                        )
                        stopped += 1
                    except Exception as e:
                        failed += 1
                        log.error(
                            "[ExpireSandbox] stop bot for device %s error: %s",
                            device_uuid,
                            e,
                            exc_info=True,
                        )

            await asyncio.gather(*(_stop_one(row) for row in rows))

            next_id = rows[-1]["id"]
            if next_id <= last_id:
                break
            last_id = next_id

        duration = time.monotonic() - start_time
        report = ExpireSandboxRunReport(
            run_uuid=run_uuid,
            duration_seconds=duration,
            scanned=scanned,
            stopped=stopped,
            failed=failed,
            skipped=skipped,
            pages=page_count,
            skipped_reasons=skipped_reasons,
        )
        log.info("[ExpireSandbox] Report: %s", report.to_log())
        return report

    def _resolve_bot_uuid(self, tenant: str, env: str, device_uuid: str) -> str | None:
        """Resolve the active bot bound to a device, if any."""
        rel = self._bot_device_rel_repo.get_by_device_uuid(device_uuid, tenant, env)
        if rel is None:
            return None
        bot = self._bot_repo.get_by_id(rel.bot_id, tenant=tenant, env=env)
        if bot is None:
            return None
        return bot.bot_uuid

    def _load_whitelist(self) -> set[str]:
        """Read this run's whitelist from system_config (env-scoped); treat failures as empty (fail open)."""
        try:
            resp = self._system_config_service.get_config(
                SystemConfigKey.EXPIRE_SANDBOX_WHITELIST_BOT_UUIDS
            )
        except Exception as e:
            log.warning(
                "[ExpireSandbox] read whitelist config failed, treating as empty: %s", e
            )
            return set()
        if resp is None:
            return set()
        return parse_whitelist_bot_uuids(resp.conf_value)

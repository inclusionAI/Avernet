"""DesktopBotLifecycle — recover PENDING bots on startup + periodic health scan."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotOrphanError,
    DesktopBotService,
    DesktopBotServiceError,
)
from agentclaw.community.core.desktop_bot.status_mapping import StatusDecision, map_baas_to_local
from agentclaw.community.di.config import DesktopBotPeriodicScanConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()

_PENDING_TIMEOUT = timedelta(minutes=10)
_DOWNLOADING_PENDING_TIMEOUT = timedelta(minutes=60)

# Periodic scan configuration
_SCAN_INTERVAL_MINUTES = 2
_SCAN_BATCH_SIZE = 50
_SCAN_SKIP_IF_CHECKED_WITHIN_SECONDS = 240  # 4min — less than scan interval

# Bounded concurrency for per-round BaaS queries. The single-instance lock
# still ensures only one worker scans; this caps how many device-status
# queries that worker fires at once (= BaaS instantaneous load ceiling).
# 5000 bots / 20 ≈ 250 batches × ~0.6s ≈ 2.5min per round.
_SCAN_CONCURRENCY = 20

# Single-instance guard: only one worker runs each scan round.
# TTL must be >= the worst-case single-round duration so a long scan
# does not let its lock expire and get stolen by another worker mid-scan.
# Round worst-case ≈ bot_count × (_SCAN_THROTTLE_SECONDS + BaaS RTT).
# 5× the scan interval (= 600s) gives ample headroom for current scale;
# revisit if bot count grows enough that a round approaches this value.
_SCAN_LOCK_KEY = "desktop_bot_periodic_scan"
_SCAN_LOCK_TTL_SECONDS = _SCAN_INTERVAL_MINUTES * 60 * 5  # 600s

# Cross-worker throttle: persisted timestamp of the last completed scan.
# Workers hold staggered sleep timers, so without this the lock would just
# be handed off round-robin and the global scan frequency would be far
# higher than _SCAN_INTERVAL_MINUTES. A worker that wins the lock checks
# this stamp first and skips if the previous scan was too recent.
_SCAN_TS_KEY = "desktop_bot_periodic_scan:last_ts"

# Starvation guard: the throttle skips a round when ``_SCAN_TS_KEY`` looks
# "scanned within the interval". If that shared timestamp ever gets wedged at a
# near-now value (clock skew / replication lag / concurrent writes in the shared
# GZone cache — the 2026-06-08 outage), every round would skip forever with no
# bound and no alert. After this many CONSECUTIVE throttle-skips, force one scan
# through regardless, so starvation can never be unbounded. Sized so the forced
# scan triggers well before a human would notice (≈ a handful of intervals).
_MAX_CONSECUTIVE_THROTTLE_SKIPS = 5

# Sentinel tokens accepted in apply_owner_whitelist meaning "apply to everyone"
_APPLY_ALL_TOKENS = frozenset({"*", "ALL"})


def _should_log_only(
    owner_id: str,
    whitelist: frozenset[str],
    global_dry: bool,
) -> bool:
    """Decide whether this bot's decision should be log-only (no DB write).

    Safe-by-default: only owners explicitly opted in via the whitelist (or the
    ``*`` / ``ALL`` sentinel) get their state mutated.

    - global_dry on                       → always log only (strongest)
    - whitelist contains "*" or "ALL"     → apply (full rollout)
    - whitelist non-empty + owner in it   → apply
    - whitelist non-empty + owner missing → log only
    - whitelist empty                     → log only (nobody opted in)
    """
    if global_dry:
        return True
    if whitelist & _APPLY_ALL_TOKENS:
        return False
    if not whitelist:
        return True
    return owner_id not in whitelist


class DesktopBotLifecycle(LifecycleBase):
    """Recover PENDING desktop bots on startup + periodic background health scan.

    Two-layer consistency:
    1. startup(): one-shot recovery of PENDING bots after backend restart
    2. _periodic_health_loop(): background scan of ALL non-terminal bots,
       independent of user frontend polling, ensuring orphan/stale detection
       even when users are offline.

    All policy (enabled flag, owner whitelist, global dry-run) is injected
    via :class:`DesktopBotPeriodicScanConfig`, sourced from
    ``user_config.desktop_bot_periodic_scan`` in YAML.
    """

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        desktop_bot_service: DesktopBotService,
        scan_config: DesktopBotPeriodicScanConfig,
        cache: CachePlugin,
    ) -> None:
        self._bot_repo = bot_repo
        self._svc = desktop_bot_service
        self._scan_cfg = scan_config
        self._cache = cache
        self._periodic_task: asyncio.Task | None = None
        self._running: bool = False
        # Consecutive throttle-skip counter for the starvation guard (see
        # _MAX_CONSECUTIVE_THROTTLE_SKIPS). Reset to 0 whenever a scan runs.
        self._consecutive_throttle_skips: int = 0
        # Env-scoped cache keys: prod / pre / dev share the SAME distributed cache
        # instance (agentclawTBaseCache) with no namespace isolation. Without
        # an env prefix, prod's frequent scans stamp the shared last_ts every
        # ~2min, and pre/dev read it as "scanned just now" and skip FOREVER —
        # pre's scan never runs. Prefixing the lock + timestamp keys with the
        # env gives each environment its own scan coordination.
        _env = get_current_env()
        self._scan_lock_key = f"{_SCAN_LOCK_KEY}:{_env}"
        self._scan_ts_key = f"{_SCAN_TS_KEY}:{_env}"

    async def startup(self) -> None:
        # Phase 1: recover PENDING bots
        await self._recover_pending_bots()

        # Phase 2: start periodic background scan
        if not self._scan_cfg.enabled:
            logger.warning(
                "[DesktopBotLifecycle] periodic scan DISABLED via "
                "user_config.desktop_bot_periodic_scan.enabled — "
                "startup recovery still ran"
            )
            return

        self._running = True
        self._periodic_task = asyncio.create_task(self._periodic_health_loop())
        logger.info(
            "[DesktopBotLifecycle] periodic health scan started "
            "(%dmin interval, apply_to=%s, global_dry_run=%s)",
            _SCAN_INTERVAL_MINUTES,
            self._format_apply_mode(),
            self._scan_cfg.global_dry_run,
        )

    def _format_apply_mode(self) -> str:
        wl = self._scan_cfg.apply_owner_whitelist
        if wl & _APPLY_ALL_TOKENS:
            return "ALL_USERS (full rollout)"
        if not wl:
            return (
                "NOBODY (safe default — populate "
                "user_config.desktop_bot_periodic_scan.apply_owner_whitelist)"
            )
        return f"whitelisted owners={sorted(wl)}"

    async def shutdown(self) -> None:
        self._running = False
        if self._periodic_task and not self._periodic_task.done():
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
        logger.info("[DesktopBotLifecycle] periodic health scan stopped")

    # ── Phase 1: PENDING recovery ────────────────────────────────────────

    async def _recover_pending_bots(self) -> None:
        _, pending_bots = self._bot_repo.search_bots(
            bot_type="desktop",
            bot_status="PENDING",
            page=1,
            page_size=100,
        )
        if not pending_bots:
            return

        logger.info(
            "[DesktopBotLifecycle] recovering %d PENDING desktop bots",
            len(pending_bots),
        )

        whitelist = self._scan_cfg.apply_owner_whitelist
        global_dry = self._scan_cfg.global_dry_run
        now = datetime.now()
        for bot in pending_bots:
            owner_id = bot.get("owner_id", "")
            log_only = _should_log_only(owner_id, whitelist, global_dry)

            bot_id = bot.get("bot_id", "")
            device_id = bot.get("device_id", "")
            binding_id = bot.get("binding_id")
            gmt_create_str = bot.get("gmt_create", "")

            if not device_id:
                logger.warning(
                    "[DesktopBotLifecycle] skip bot without device_id: %s", bot_id,
                )
                continue

            try:
                baas_response = self._svc.query_device_status(device_id)
                confirmed_orphan = False
                unreachable = False
            except DesktopBotOrphanError:
                baas_response = None
                confirmed_orphan = True
                unreachable = False
            except DesktopBotServiceError:
                baas_response = None
                confirmed_orphan = False
                unreachable = True

            decision = map_baas_to_local(
                baas_response=baas_response,
                current_local_status="PENDING",
                confirmed_orphan=confirmed_orphan,
            )

            if unreachable:
                if self._is_pending_timeout(gmt_create_str, now):
                    decision = StatusDecision(
                        target_status="FAILED",
                        release_reason="baas_unreachable_timeout",
                        log_context={"reason": "pending_timeout_baas_unreachable"},
                    )
                else:
                    logger.info(
                        "[DesktopBotLifecycle] skip recent PENDING bot_id=%s (unreachable)",
                        bot_id,
                    )
                    continue

            if decision.target_status:
                logger.info(
                    "[DesktopBotLifecycle] recovering bot_id=%s owner=%s → %s%s",
                    bot_id, owner_id, decision.target_status,
                    " (LOG_ONLY)" if log_only else "",
                )

            if log_only:
                continue
            self._svc._apply_decision(bot_id, owner_id, binding_id, "PENDING", decision)

    # ── Phase 2: Periodic background scan ────────────────────────────────

    async def _periodic_health_loop(self) -> None:
        """Background loop: scan all non-terminal desktop bots periodically.

        Policy is read from injected ``DesktopBotPeriodicScanConfig``; runtime
        kill-switch is the ``enabled`` flag in YAML, which takes effect on
        process restart (config is loaded once at boot, like the rest of
        ``user_config``).

        Each round is guarded by a distributed lock so that under a
        multi-worker / multi-pod deployment only one process runs the scan,
        avoiding N× BaaS load and concurrent writes to the same bot rows.
        """
        while self._running:
            try:
                await asyncio.sleep(_SCAN_INTERVAL_MINUTES * 60)
                if not self._running:
                    break
                await self._guarded_scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[DesktopBotLifecycle] periodic scan error: %s", e)

    async def _guarded_scan(self) -> None:
        """Acquire the single-instance lock, scan once, then release.

        If the lock is held by another worker (or the distributed cache is unreachable),
        skip this round entirely — safe degradation: missing one
        reconciliation round is preferable to N workers scanning at once.

        Even when the lock is won, a cross-worker timestamp throttle
        (``_SCAN_TS_KEY``) is checked first: workers hold staggered sleep
        timers, so without this the lock would be handed off round-robin and
        the global scan frequency would far exceed the configured interval.
        """
        try:
            token = self._cache.acquire_lock(
                self._scan_lock_key, ttl=_SCAN_LOCK_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning(
                "[periodic-scan] acquire_lock failed, skip round: %s", e,
            )
            return

        if not token:
            logger.info(
                "[periodic-scan] another worker holds the lock, skip this round",
            )
            return

        try:
            scan_ts_raw = self._read_scan_ts_raw()
            if self._is_ts_too_recent(scan_ts_raw):
                self._consecutive_throttle_skips += 1
                # Starvation guard: a wedged shared timestamp must never skip
                # forever. Past the limit, force one scan through and re-arm.
                if (
                    self._consecutive_throttle_skips
                    > _MAX_CONSECUTIVE_THROTTLE_SKIPS
                ):
                    logger.warning(
                        "[periodic-scan] throttle skipped %d consecutive rounds "
                        "(last_ts=%r) — forcing a scan to avoid starvation",
                        self._consecutive_throttle_skips,
                        scan_ts_raw,
                    )
                    self._consecutive_throttle_skips = 0
                    # fall through to scan
                else:
                    logger.info(
                        "[periodic-scan] last scan within %dmin, skip this round "
                        "(consecutive_skips=%d, last_ts=%r)",
                        _SCAN_INTERVAL_MINUTES,
                        self._consecutive_throttle_skips,
                        scan_ts_raw,
                    )
                    return
            await self._scan_all_bots()
            self._stamp_scan_ts()
            self._consecutive_throttle_skips = 0
        finally:
            try:
                self._cache.release_lock(self._scan_lock_key, token)
            except Exception as e:
                logger.warning(
                    "[periodic-scan] release_lock failed "
                    "(lock will expire via TTL): %s", e,
                )

    def _read_scan_ts_raw(self) -> str | None:
        """Read the raw last-scan timestamp from cache (None on miss/error).

        Exposed so the starvation guard can log the actual stuck value when it
        fires — that value is the smoking gun for diagnosing why the throttle
        wedged (clock skew vs replication lag vs concurrent write).
        """
        try:
            raw = self._cache.get(self._scan_ts_key)
        except Exception as e:
            logger.warning(
                "[periodic-scan] read last_scan_ts failed, proceeding: %s", e,
            )
            return None
        return raw if isinstance(raw, str) else None

    def _scanned_too_recently(self) -> bool:
        """Return True if a scan completed within the interval window.

        Conservative: a missing or unparseable timestamp means "not recent"
        (scan proceeds), so a corrupt cache value never wedges the loop.
        """
        return self._is_ts_too_recent(self._read_scan_ts_raw())

    def _is_ts_too_recent(self, raw: str | None) -> bool:
        """Pure check: is the given raw timestamp within the interval window?

        Split from the cache read so the caller reads ``_SCAN_TS_KEY`` once and
        reuses the value for both the decision and the diagnostic log line.
        """
        if not raw:
            return False
        try:
            last = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return False
        return (datetime.now() - last) < timedelta(minutes=_SCAN_INTERVAL_MINUTES)

    def _stamp_scan_ts(self) -> None:
        """Persist the current time as the last-scan timestamp."""
        try:
            self._cache.set(
                self._scan_ts_key,
                datetime.now().isoformat(),
                ttl=_SCAN_LOCK_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning(
                "[periodic-scan] stamp last_scan_ts failed: %s", e,
            )

    def _needs_check(self, bot: dict, now: datetime) -> bool:
        """是否需要对该 bot 查询 BaaS device-status(纯内存判断,无 IO)。

        排除:已释放 / 无 device_id / 近期已被前端轮询查过。
        与并发查询解耦——过滤是纯 CPU,在进入并发前同步完成。
        """
        if bot.get("status", "") == "RELEASED":
            return False
        if not bot.get("device_id", ""):
            return False
        ext = bot.get("ext") or {}
        last_check = ext.get("last_health_check", "")
        if last_check and self._recently_checked(last_check, now):
            return False
        return True

    async def _check_one_bot(
        self,
        bot: dict,
        sem: asyncio.Semaphore,
        now: datetime,
        whitelist: frozenset[str],
        global_dry: bool,
    ) -> str:
        """处理单个 bot:查 BaaS → 决策 → PENDING超时兜底 → 应用。

        返回 tally tag: 'applied' | 'log_only' | 'failed',供 _scan_all_bots 汇总。
        同步 IO(BaaS 查询、DB 写)用 to_thread 丢线程池;Semaphore 限并发。
        DB 连接 thread-local,并发写天然隔离。
        """
        async with sem:
            bot_id = bot.get("bot_id", "")
            owner_id = bot.get("owner_id", "")
            device_id = bot.get("device_id", "")
            binding_id = bot.get("binding_id")
            status = bot.get("status", "")
            ext = bot.get("ext") or {}
            log_only = _should_log_only(owner_id, whitelist, global_dry)

            try:
                baas_response = await asyncio.to_thread(
                    self._svc.query_device_status, device_id,
                )
                confirmed_orphan = False
            except DesktopBotOrphanError:
                baas_response = None
                confirmed_orphan = True
            except DesktopBotServiceError as e:
                logger.warning(
                    "[periodic-scan] query failed bot=%s: %s", bot_id, e,
                )
                return "failed"

            decision = map_baas_to_local(
                baas_response=baas_response,
                current_local_status=status,
                confirmed_orphan=confirmed_orphan,
            )

            # PENDING 超时兜底(逻辑与串行版一致):重启过渡保护(decision 保持
            # PENDING)只在合理窗内有效。pending_since 超 _PENDING_TIMEOUT 仍离线
            # → 判 OFFLINE,避免永久卡 PENDING。
            if (
                status == "PENDING"
                and decision.target_status is None
                and decision.log_context.get("pending_transition") == "true"
                and self._pending_since_timeout(ext, now)
            ):
                decision = StatusDecision(
                    target_status="OFFLINE",
                    log_context={"reason": "pending_transition_timeout"},
                )

            if log_only:
                if decision.target_status and decision.target_status != status:
                    logger.info(
                        "[periodic-scan][LOG_ONLY] would change "
                        "bot=%s owner=%s %s → %s ctx=%s",
                        bot_id, owner_id, status, decision.target_status,
                        decision.log_context,
                    )
                return "log_only"

            await asyncio.to_thread(
                self._svc._apply_decision,
                bot_id, owner_id, binding_id, status, decision,
            )
            return "applied"

    async def _scan_all_bots(self) -> None:
        """Paginated, bounded-concurrency scan of all non-terminal desktop bots.

        Per page: filter synchronously (_needs_check), then fan out the
        survivors through a Semaphore-bounded gather (_check_one_bot). The
        single-instance lock (in _guarded_scan) still serializes across
        workers; _SCAN_CONCURRENCY caps in-flight BaaS queries per round.

        - Skip bots recently checked by frontend polling / released / no device
        - Single bot failure → tagged 'failed', does not block others
        """
        whitelist = self._scan_cfg.apply_owner_whitelist
        global_dry = self._scan_cfg.global_dry_run
        now = datetime.now()
        sem = asyncio.Semaphore(_SCAN_CONCURRENCY)
        page = 1
        total_checked = 0
        total_skipped = 0
        total_log_only = 0
        total_failed = 0

        while True:
            _, bots = self._bot_repo.search_bots(
                bot_type="desktop",
                bot_status=None,
                page=page,
                page_size=_SCAN_BATCH_SIZE,
            )
            if not bots:
                break

            # 同步过滤(纯内存,无 IO):排除 RELEASED/无device_id/近期已查
            to_check = [bot for bot in bots if self._needs_check(bot, now)]
            total_skipped += len(bots) - len(to_check)

            # 有界并发处理本页存活 bot
            tasks = [
                self._check_one_bot(bot, sem, now, whitelist, global_dry)
                for bot in to_check
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    total_failed += 1
                elif r == "applied":
                    total_checked += 1
                elif r == "log_only":
                    total_log_only += 1
                elif r == "failed":
                    total_failed += 1

            if len(bots) < _SCAN_BATCH_SIZE:
                break
            page += 1

        if whitelist & _APPLY_ALL_TOKENS:
            wl_repr = "ALL"
        elif not whitelist:
            wl_repr = "NOBODY"
        else:
            wl_repr = f"size={len(whitelist)}"
        logger.info(
            "[DesktopBotLifecycle] periodic scan complete: "
            "applied=%d log_only=%d skipped=%d failed=%d "
            "whitelist=%s global_dry_run=%s concurrency=%d",
            total_checked, total_log_only, total_skipped, total_failed,
            wl_repr, global_dry, _SCAN_CONCURRENCY,
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _is_pending_timeout(self, gmt_create_str: str, now: datetime) -> bool:
        """Check if PENDING bot has exceeded timeout.

        Conservative: parse failure → treat as timeout (avoid dirty data stuck forever).
        """
        if not gmt_create_str:
            return True
        try:
            created = datetime.fromisoformat(gmt_create_str)
            return (now - created) > _PENDING_TIMEOUT
        except (ValueError, TypeError):
            return True

    def _pending_since_timeout(self, ext: object, now: datetime) -> bool:
        """Check if the current PENDING phase (ext.pending_since) exceeded timeout.

        Unlike :meth:`_is_pending_timeout` (startup recovery, keyed on
        gmt_create), this is keyed on ``ext.pending_since`` — the moment the
        bot most recently *entered* PENDING (restart/create). gmt_create would
        misfire for old bots: their creation time is always far past 10min, so
        any restart would instantly flip to OFFLINE.

        Conservative the OTHER way: a missing/unparseable ``pending_since``
        means "NOT timed out" → stay PENDING. Old rows without the field, or a
        corrupt value, must never be wrongly judged OFFLINE; the next time the
        bot enters PENDING the field gets stamped and the check works.
        """
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                return False
        if not isinstance(ext, dict):
            return False
        raw = ext.get("pending_since", "")
        if not raw or not isinstance(raw, str):
            return False
        try:
            since = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return False
        start_status = ext.get("start_status", "")
        timeout = (
            _DOWNLOADING_PENDING_TIMEOUT
            if start_status == "DOWNLOADING"
            else _PENDING_TIMEOUT
        )
        return (now - since) > timeout

    def _recently_checked(self, last_check_str: str, now: datetime) -> bool:
        """Return True if bot was checked within the skip window."""
        try:
            last = datetime.fromisoformat(last_check_str)
            return (now - last).total_seconds() < _SCAN_SKIP_IF_CHECKED_WITHIN_SECONDS
        except (ValueError, TypeError):
            return False

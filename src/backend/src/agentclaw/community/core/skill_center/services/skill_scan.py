"""Skill Scan Service implementation.

High-level service that orchestrates static skill scanning (locking, scheduling,
metadata writeback). The scanner itself is obtained through the
``SkillScannerPlugin`` capability, so this module carries no scanner-SDK import or
credential; when no scanner is available the service stays disabled.
"""

import asyncio
import os
import threading
from datetime import datetime, time
from typing import Any, TYPE_CHECKING

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
        SkillCenterSyncService,
    )


logger = get_logger()

# =============================================================================
# 定时任务执行时间配置（修改这里调整每天执行时间）
# =============================================================================
DAILY_TASK_HOUR = 1      # 小时（0-23）
DAILY_TASK_MINUTE = 0    # 分钟（0-59）

# Default configuration values. Scanner-SDK settings (storage dir, auth endpoint,
# credentials, intervals) live with the scanner plugin, not here — this service
# only needs the ``enabled`` flag.
DEFAULT_CONFIG = {
    "enabled": True,
}


# The git archive URL for the daily skill-scan job is deployment config —
# ``skill_scan.git_archive_url`` in the yaml, read into ``self._config`` by
# ``_load_config``. Empty (community build) → the daily scan scheduler is
# skipped at startup (see ``startup``).


class SkillScanService(LifecycleBase):
    """Skill Scan Service - 提供技能扫描和定时任务管理能力."""

    async def startup(self) -> None:
        """Lifecycle hook — start the scanner + both daily-task schedulers.

        Body lifted from the pre-R11 ``startup_skill_scan_service`` hook
        in ``api/lifecycle.py``. The hardcoded git archive URL stays
        inline to preserve existing behavior; cleaning that up is a
        separate task.
        """
        self.start()
        git_archive_url = self._config.get("git_archive_url", "")
        if git_archive_url:
            self.start_daily_task(git_archive_url)
        else:
            logger.info(
                "SkillScanService: skill_scan.git_archive_url not set, "
                "skipping git daily-scan scheduler"
            )
        self.start_center_daily_task()
        logger.info("SkillScanService started via Lifecycle.startup()")

    def __init__(
        self,
        cache_plugin: CachePlugin,
        skill_repository: "SkillRepository",
        skill_center_sync_service: "SkillCenterSyncService",
        scanner: SkillScannerPlugin,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the service."""
        self._config = self._load_config(config)
        self._sdk: Any = None
        self._started = False
        self._scheduler_started = False
        self._daily_task_thread: threading.Thread | None = None
        self._daily_task_stop_event = threading.Event()
        self._center_daily_task_thread: threading.Thread | None = None
        self._center_daily_task_stop_event = threading.Event()
        self._cache_plugin = cache_plugin
        self._skill_repository = skill_repository
        self._skill_center_sync_service = skill_center_sync_service
        self._scanner = scanner

    def _load_config(self, override_config: dict[str, Any] | None) -> dict[str, Any]:
        """Load configuration from application.yaml with override support."""
        config = DEFAULT_CONFIG.copy()

        # Try to load from application.yaml
        try:
            from agentclaw.community.core.config import sofa

            app_config_dict = sofa.sofa_config.model_dump()
            skill_scan_config = app_config_dict.get("user_config", {}).get("skill_scan", {})
            config.update(skill_scan_config)
        except Exception as e:
            logger.warning(f"Failed to load config from application.yaml: {e}")

        # Apply override config (highest priority)
        if override_config:
            config.update(override_config)

        logger.debug(f"Loaded skill_scan config: {config}")
        return config

    def start(self) -> bool:
        """Start the service.

        Returns:
            bool: True if started successfully or already running, False if
            disabled by config or no scanner is available.
        """
        if self._started:
            logger.debug("SkillScanService already started")
            return True

        # Check if enabled
        if not self._config.get("enabled", True):
            logger.info("SkillScanService is disabled by configuration")
            return False

        # Obtain a scanner SDK handle from the capability plugin. None ⇒ no
        # scanner available (e.g. community build), so scanning stays disabled.
        try:
            sdk = self._scanner.create_sdk()
        except Exception as e:
            logger.error(f"Failed to initialize scanner: {e}")
            return False
        if sdk is None:
            logger.info("SkillScanService: no scanner available; disabled")
            return False

        self._sdk = sdk
        self._started = True
        logger.info("SkillScanService started successfully")

        return True

    def stop(self) -> bool:
        """Stop the service.

        Returns:
            bool: True if stopped successfully or already stopped.
        """
        if not self._started:
            logger.debug("SkillScanService not running, nothing to stop")
            return True

        # Stop daily task thread
        if self._daily_task_thread and self._daily_task_thread.is_alive():
            self._daily_task_stop_event.set()
            self._daily_task_thread.join(timeout=5)
            logger.info("Daily task thread stopped")

        self._started = False
        self._sdk = None
        logger.info("SkillScanService stopped successfully")
        return True

    def is_running(self) -> bool:
        """Check if the service is running.

        Returns:
            bool: True if the service has been started.
        """
        return self._started

    def _ensure_started(self) -> None:
        """Ensure the service is started before performing operations."""
        if not self._started:
            raise RuntimeError("SkillScanService is not started. Call start() first.")

    # =========================================================================
    # 每日定时任务（内存级别，每天凌晨1点执行）
    # =========================================================================
    def exec_task(self, git_url: str, private_token: str | None = None) -> dict[str, Any]:
        """Execute daily scan task.

        Args:
            git_url: Git repository URL to scan.
            private_token: Git private token (optional).

        Returns:
            Dict with task execution result.
        """
        cache = self._cache_plugin

        # 1、抢锁
        # 根据环境生成锁 key
        from agentclaw.community.utils.env_utils import get_current_env
        current_env = get_current_env()
        lock_key = f"skill_scan_exec_task_{current_env}" if current_env != "dev" else "skill_scan_exec_task"

        lock_value = cache.acquire_lock(lock_key, ttl=300)
        if not lock_value:
            logger.info("Lock already held by another instance, skipping execution")
            return {
                "success": False,
                "git_url": git_url,
                "error": "Lock already held by another instance, skipping execution",
            }
        logger.info("Lock success")
        # 2、执行git扫描
        try:
            logger.info(f"exec_task started: scanning {git_url}")
            results = self.scan_git(git_url=git_url, private_token=private_token)
            success_count = sum(
                1
                for r in results
                if r.get("success")
            )
            logger.info(f"exec_task completed: {success_count}/{len(results)} skills scanned successfully")

            # 3、保存扫描结果
            saved_count = 0
            for r in results:
                if not r.get("success"):
                    continue

                result_data = r.get("result")
                if not result_data:
                    continue

                try:
                    # 从 result 中获取 skill_name
                    skill_code = result_data.skill_code
                    if not skill_code:
                        continue

                    git_path = f"git://{skill_code}"

                    # 提取 mcp_dependencies
                    mcp_deps = result_data.mcp_dependencies or []
                    mcp_dependencies = [
                        dep.model_dump() if hasattr(dep, "model_dump") else dep
                        for dep in mcp_deps
                    ]

                    # 提取 risk_tags
                    risk_tags_raw = result_data.risk_tags if hasattr(result_data, "risk_tags") else []
                    risk_tags = [
                        tag.model_dump() if hasattr(tag, "model_dump") else tag
                        for tag in risk_tags_raw
                    ]

                    # 调用更新方法
                    update_res = self.update_skill_metadata_by_git_path(
                        git_path=git_path,
                        risk_tags=risk_tags,
                        mcp_dependencies=mcp_dependencies,
                    )
                    logger.info(f"Saved skill metadata update_res: id={update_res.get('id') if update_res else None}")
                    saved_count += 1
                    logger.info(f"Saved skill metadata: {git_path} {risk_tags} {mcp_dependencies}")
                except Exception as e:
                    logger.warning(f"Failed to save skill metadata: {e}")

            logger.info(f"Saved {saved_count} skill metadata results")

            return {
                "success": True,
                "git_url": git_url,
                "total": len(results),
                "success_count": success_count,
                "saved_count": saved_count,
                "results": results,
            }
        except Exception as e:
            logger.error(f"exec_task failed: {e}")
            return {
                "success": False,
                "git_url": git_url,
                "error": str(e),
            }
        finally:
            cache.release_lock(lock_key, lock_value)

    def _calculate_seconds_until_target_time(self) -> float:
        """Calculate seconds until next scheduled time (DAILY_TASK_HOUR:DAILY_TASK_MINUTE)."""
        from datetime import timedelta

        from agentclaw.community.utils.env_utils import get_current_env

        now = datetime.now()

        # 生产环境加2小时
        current_env = get_current_env()
        hour_offset = 2 if current_env == "prod" else 0

        hour = (DAILY_TASK_HOUR + hour_offset) % 24
        target = datetime.combine(now.date(), time(hour, DAILY_TASK_MINUTE, 0))
        if now >= target:
            # If already past target time today, schedule for tomorrow
            target = datetime.combine(now.date() + timedelta(days=1), time(hour, DAILY_TASK_MINUTE, 0))
        return (target - now).total_seconds()

    def _get_actual_task_time(self) -> tuple[int, int]:
        """获取实际的执行时间（考虑环境偏移）."""
        from agentclaw.community.utils.env_utils import get_current_env
        current_env = get_current_env()
        hour_offset = 2 if current_env == "prod" else 0
        actual_hour = (DAILY_TASK_HOUR + hour_offset) % 24
        return actual_hour, DAILY_TASK_MINUTE

    def _daily_task_loop(self, git_url: str, private_token: str | None = None) -> None:
        """Background thread loop for daily scheduled task."""
        actual_hour, actual_minute = self._get_actual_task_time()
        logger.info(f"Daily task thread started, scheduled to run at {actual_hour:02d}:{actual_minute:02d} daily")

        while not self._daily_task_stop_event.is_set():
            try:
                # Calculate wait time until target time
                wait_seconds = self._calculate_seconds_until_target_time()
                logger.info(f"Next daily task scheduled in {wait_seconds:.0f} seconds")

                # Wait until target time or stop signal
                if self._daily_task_stop_event.wait(timeout=wait_seconds):
                    # Stop signal received
                    break

                # Execute the task
                logger.info("Starting daily scheduled Git scan task")
                self.exec_task(git_url=git_url, private_token=private_token)

            except Exception as e:
                logger.error(f"Error in daily task loop: {e}")
                # Wait a bit before retrying to avoid tight error loop
                self._daily_task_stop_event.wait(timeout=60)

        logger.info("Daily task thread ended")

    def start_daily_task(self, git_url: str, private_token: str | None = None) -> bool:
        """Start the daily scheduled Git scan task at scheduled time.

        Args:
            git_url: Git repository URL to scan.
            private_token: Git private token (optional).

        Returns:
            True if started successfully.

        Raises:
            RuntimeError: If service is not started.
        """
        # 仅预发和线上环境启动定时任务
        from agentclaw.community.utils.env_utils import get_current_env_with_gray
        current_env = get_current_env_with_gray()
        if current_env not in ["pre", "prod"]:
            logger.info(f"Environment '{current_env}' detected, skipping daily task (only pre/prod allowed)")
            return False

        self._ensure_started()

        if self._daily_task_thread and self._daily_task_thread.is_alive():
            logger.debug("Daily task already running")
            return True

        self._daily_task_stop_event.clear()
        self._daily_task_thread = threading.Thread(
            target=self._daily_task_loop,
            args=(git_url, private_token),
            name="SkillScanDailyTask",
            daemon=True,
        )
        self._daily_task_thread.start()
        logger.info(f"Daily scheduled task started for {git_url}")
        return True

    def stop_daily_task(self) -> bool:
        """Stop the daily scheduled task.

        Returns:
            True if stopped successfully.
        """
        if not self._daily_task_thread or not self._daily_task_thread.is_alive():
            logger.debug("Daily task not running")
            return True

        self._daily_task_stop_event.set()
        self._daily_task_thread.join(timeout=5)
        logger.info("Daily scheduled task stopped")
        return True

    # =========================================================================
    # Center Skill 每日定时任务（与 git daily task 完全对称）
    # =========================================================================
    def exec_center_task(self) -> dict[str, Any]:
        """执行一次全量 center:// skill 扫描。

        流程：
        1. 抢分布式锁（防多机并发）
        2. 查 ac_skill 表所有 center:// PUBLISHED skill
        3. 对每个 skill 调 SkillCenterSyncService.scan_after_sync()
        """
        from agentclaw.community.utils.env_utils import get_current_env

        env = get_current_env()
        cache = self._cache_plugin
        lock_key = f"skill_center_scan_exec_task_{env}" if env != "dev" else "skill_center_scan_exec_task"
        lock_value = cache.acquire_lock(lock_key, ttl=600)
        if not lock_value:
            logger.info("[SkillScanService] exec_center_task: lock held, skipping")
            return {"success": False, "error": "Lock held by another instance"}

        try:
            skill_repo = self._skill_repository
            skills = skill_repo.list_published_center_skills()
            logger.info("[SkillScanService] exec_center_task: found %d center skills", len(skills))

            sync_svc = self._skill_center_sync_service
            success_count = 0
            failed_count = 0

            for skill in skills:
                uuid = skill.get("skill_uuid") or skill.get("uuid") or skill.get("id")
                if not uuid:
                    continue
                try:
                    sync_svc.scan_after_sync(str(uuid), env)
                    success_count += 1
                except Exception as e:
                    logger.warning("[SkillScanService] exec_center_task: scan failed uuid=%s: %s", uuid, e)
                    failed_count += 1

            logger.info(
                "[SkillScanService] exec_center_task done: total=%d success=%d failed=%d",
                len(skills), success_count, failed_count,
            )
            return {"success": True, "total": len(skills), "success_count": success_count, "failed_count": failed_count}

        except Exception as e:
            logger.error("[SkillScanService] exec_center_task error: %s", e)
            return {"success": False, "error": str(e)}
        finally:
            cache.release_lock(lock_key, lock_value)
            logger.info("[SkillScanService] exec_center_task: released lock key=%s", lock_key)

    def _center_daily_task_loop(self) -> None:
        """Center skill 每日定时扫描 loop（与 _daily_task_loop 完全对称）。"""
        actual_hour, actual_minute = self._get_actual_task_time()
        logger.info(
            "[SkillScanService] center daily task started, scheduled at %02d:%02d daily",
            actual_hour, actual_minute,
        )
        while not self._center_daily_task_stop_event.is_set():
            try:
                wait_seconds = self._calculate_seconds_until_target_time()
                logger.info("[SkillScanService] center next scan in %.0f seconds", wait_seconds)
                if self._center_daily_task_stop_event.wait(timeout=wait_seconds):
                    break
                logger.info("[SkillScanService] starting center daily scan task")
                self.exec_center_task()
            except Exception as e:
                logger.error("[SkillScanService] center daily task loop error: %s", e)
                self._center_daily_task_stop_event.wait(timeout=60)
        logger.info("[SkillScanService] center daily task thread ended")

    def start_center_daily_task(self) -> bool:
        """启动 center skill 每日定时扫描（仅 pre/prod 环境）。

        与 start_daily_task 完全对称。
        """
        from agentclaw.community.utils.env_utils import get_current_env_with_gray
        current_env = get_current_env_with_gray()
        if current_env not in ["pre", "prod"]:
            logger.info(
                "[SkillScanService] env='%s', skipping center daily task (only pre/prod)",
                current_env,
            )
            return False

        self._ensure_started()

        if self._center_daily_task_thread and self._center_daily_task_thread.is_alive():
            logger.info("[SkillScanService] center daily task already running, skipping duplicate start")
            return True

        self._center_daily_task_stop_event.clear()
        self._center_daily_task_thread = threading.Thread(
            target=self._center_daily_task_loop,
            name="SkillScanCenterDailyTask",
            daemon=True,
        )
        self._center_daily_task_thread.start()
        logger.info("[SkillScanService] center daily task started")
        return True

    def stop_center_daily_task(self) -> bool:
        """停止 center skill 每日定时扫描。"""
        if not self._center_daily_task_thread or not self._center_daily_task_thread.is_alive():
            return True
        self._center_daily_task_stop_event.set()
        self._center_daily_task_thread.join(timeout=5)
        logger.info("[SkillScanService] center daily task stopped")
        return True

    def scan_skill(self, skill_path: str) -> Any:
        """Scan a single skill file.

        Args:
            skill_path: Path to the skill file.

        Returns:
            Scan result from SDK.

        Raises:
            RuntimeError: If service is not started.
            FileNotFoundError: If skill file does not exist.
        """
        self._ensure_started()
        logger.info(f"Scanning skill: {skill_path}")

        if not os.path.exists(skill_path):
            raise FileNotFoundError(f"Skill file not found: {skill_path}")

        try:
            result = self._sdk.scan(skill_path)
            logger.info(f"Successfully scanned skill: {skill_path}")
            return result
        except Exception as e:
            logger.error(f"Failed to scan skill '{skill_path}': {e}")
            raise

    def get_mcp_dependencies(
        self,
        skill_path: str,
        base_dir: str | None = None,
        min_confidence: float = 0.8,
    ) -> list[dict[str, Any]]:
        """Get MCP dependencies for a skill.

        Args:
            skill_path: Path to the skill file or skills_mcp_map.json.
            base_dir: Base directory for looking up dependent skills.
            min_confidence: Minimum confidence threshold (default 0.8).

        Returns:
            List of MCP dependency dictionaries.

        Raises:
            RuntimeError: If service is not started.
            FileNotFoundError: If skill file does not exist.
        """
        self._ensure_started()
        logger.info(f"Getting dependencies for: {skill_path}, min_confidence={min_confidence}")

        try:
            # Use get_mcp_dependencies to get MCP dependencies directly
            mcp_dependencies = self._sdk.get_mcp_dependencies(
                skill_path=skill_path,
                base_dir=base_dir,
                min_confidence=min_confidence,
            )

            logger.info(f"Found {len(mcp_dependencies)} MCP dependencies")
            return list(mcp_dependencies)
        except Exception as e:
            logger.error(f"Failed to get dependencies for {skill_path}: {e}")
            raise

    def scan_git(
        self,
        git_url: str,
        private_token: str | None = None,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        """Scan all skills from a Git repository.

        Downloads the repository archive, extracts it, and scans all skill packages.

        Args:
            git_url: Git API URL, e.g., https://example.com/api/v3/projects/xxx/repository/archive.tar.gz
            private_token: Git Private Token (optional, optional, resolved from the secret store if not provided)
            max_workers: Max concurrent scans (default: from config)

        Returns:
            List of scan task results, each containing:
                - skill_path: Path to the skill
                - success: Whether the scan succeeded
                - result: ScanResult if successful
                - error: Error message if failed
                - duration_ms: Scan duration in milliseconds

        Raises:
            RuntimeError: If service is not started.
        """
        self._ensure_started()
        logger.info(f"Scanning Git repository: {git_url}")

        try:
            results = self._sdk.scan_git_repo(
                git_url=git_url,
                private_token=private_token,
                max_workers=max_workers,
                skills_root="skills",
            )
            success_count = sum(
                1
                for r in results
                if r.success
            )
            logger.info(f"Git scan completed: {success_count}/{len(results)} successful")
            return [
                {
                    "skill_path": r.skill_path,
                    "success": r.success,
                    "result": r.result,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Failed to scan Git repository '{git_url}': {e}")
            raise

    async def scan_git_async(
        self,
        git_url: str,
        private_token: str | None = None,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        """Asynchronously scan all skills from a Git repository.

        Uses asyncio.to_thread() to wrap the synchronous SDK call.

        Args:
            git_url: Git API URL
            private_token: Git Private Token (optional)
            max_workers: Max concurrent scans (optional)

        Returns:
            List of scan task results.
        """
        self._ensure_started()
        logger.info(f"Async scanning Git repository: {git_url}")
        return await asyncio.to_thread(
            self.scan_git,
            git_url=git_url,
            private_token=private_token,
            max_workers=max_workers,
        )

    def start_scheduler(self) -> bool:
        """Start the scheduler for periodic scanning.

        Returns:
            bool: True if started successfully or already running.

        Raises:
            RuntimeError: If service is not started.
        """
        self._ensure_started()

        if self._scheduler_started:
            logger.debug("Scheduler already started")
            return True

        try:
            self._sdk.start_scheduler()
            self._scheduler_started = True
            logger.info("Scheduler started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
            raise

    def add_scheduled_git_scan(
        self,
        git_url: str,
        private_token: str | None = None,
        max_workers: int | None = None,
        interval_hours: int = 1,
        interval_minutes: int = 0,
        job_id: str | None = None,
    ) -> str:
        """Add a scheduled Git scan job.

        Args:
            git_url: Git API URL, e.g., https://example.com/api/v3/projects/xxx/repository/archive.tar.gz
            private_token: Git Private Token (optional, optional, resolved from the secret store if not provided)
            max_workers: Max concurrent scans (default: from config)
            interval_hours: Hours between scans.
            interval_minutes: Minutes between scans.
            job_id: Optional job ID.

        Returns:
            Job ID string.

        Raises:
            RuntimeError: If service is not started.
        """
        self._ensure_started()
        logger.info(f"Adding scheduled Git scan for: {git_url}")

        try:
            result_job_id = self._sdk.add_scheduled_git_scan(
                git_url=git_url,
                private_token=private_token,
                max_workers=max_workers,
                interval_hours=interval_hours,
                interval_minutes=interval_minutes,
                job_id=job_id,
            )
            logger.info(f"Added scheduled Git scan with job_id: {result_job_id}")
            return result_job_id
        except Exception as e:
            logger.error(f"Failed to add scheduled Git scan: {e}")
            raise

    def _filter_mcp_dependencies(self, mcp_dependencies: list | None) -> list:
        """过滤 mcp_dependencies，只保留 code, name, url 三个字段。

        Args:
            mcp_dependencies: MCP 依赖列表，可能为空或 None

        Returns:
            过滤后的 MCP 依赖列表，只包含 code, name, url 字段
        """
        if not mcp_dependencies:
            return []

        filtered = []
        for dep in mcp_dependencies:
            # 支持字典和对象两种形式
            if isinstance(dep, dict):
                filtered.append({
                    "code": dep.get("code", ""),
                    "name": dep.get("name", ""),
                    "url": dep.get("url", ""),
                })
            else:
                # 对象形式
                filtered.append({
                    "code": getattr(dep, "code", ""),
                    "name": getattr(dep, "name", ""),
                    "url": getattr(dep, "url", ""),
                })
        return filtered

    def update_skill_metadata_by_git_path(
        self,
        git_path: str,
        risk_tags: list | None = None,
        mcp_dependencies: list | None = None,
    ) -> dict[str, Any] | None:
        from agentclaw.community.utils import env_utils

        if not env_utils.get_current_env() in ["pre","dev"]:
            risk_tags = []

        """根据 git_path 更新技能的 risk_tags 和 mcp_dependencies。

        这是一个业务方法，封装了查询和更新操作：
        1. 通过 git_path 查找技能
        2. 更新 risk_tags 和/或 mcp_dependencies

        注意：此方法不需要启动 SDK，可以直接调用。
        mcp_dependencies 会自动过滤，只保留 code, name, url 三个字段。

        Args:
            git_path: 技能的 git 路径
            risk_tags: 风险标签列表，None 表示不更新
            mcp_dependencies: MCP 依赖列表，None 表示不更新，会自动过滤字段

        Returns:
            更新后的技能字典，如果未找到或更新失败返回 None

        Raises:
            ValueError: 如果 git_path 为空或两个更新参数都为 None
        """
        if not git_path:
            raise ValueError("git_path cannot be empty")

        if risk_tags is None and mcp_dependencies is None:
            raise ValueError("At least one of risk_tags or mcp_dependencies must be provided")

        logger.info(f"Updating skill metadata by git_path: {git_path}")

        try:
            skill_repo = self._skill_repository

            # 通过 git_path 查找技能
            skill = skill_repo.get_by_git_path(git_path)
            if skill is None:
                logger.warning(f"Skill not found with git_path: {git_path}")
                return None

            skill_id = skill['id']
            result = skill

            # 更新 risk_tags
            if risk_tags is not None:
                result = skill_repo.update_risk_tags(skill_id, risk_tags)
                if result:
                    logger.info(f"Updated risk_tags for skill {skill_id}")
                else:
                    logger.warning(f"Failed to update risk_tags for skill {skill_id}")
                    return None

            # 更新 mcp_dependencies（过滤字段，只保留 code, name, url）
            if mcp_dependencies is not None:
                filtered_mcp_deps = self._filter_mcp_dependencies(mcp_dependencies)
                result = skill_repo.update_mcp_dependencies(skill_id, filtered_mcp_deps)
                if result:
                    logger.info(f"Updated mcp_dependencies for skill {skill_id}")
                else:
                    logger.warning(f"Failed to update mcp_dependencies for skill {skill_id}")
                    return None

            logger.info(f"Successfully updated skill metadata: skill_id={skill_id}, git_path={git_path}")
            return result

        except Exception as e:
            logger.warning(f"Error updating skill metadata by git_path '{git_path}': {e}")
            return None

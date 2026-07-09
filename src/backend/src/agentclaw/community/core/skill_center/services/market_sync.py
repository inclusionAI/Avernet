"""Market Sync Service - 市场技能同步定时任务服务

每 10 分钟执行一次市场同步：
1. Git pull 拉取最新技能仓库代码
2. 扫描仓库，同步数据库
3. 刷新市场缓存

仅 pre 和 prod 环境启动定时任务。
"""

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.factories import SkillServiceFactory


logger = get_logger()

# 定时任务执行间隔（分钟）
SYNC_INTERVAL_MINUTES = 5


class MarketSyncService:
    """市场技能同步定时任务服务

    定时调用现有的同步逻辑，确保市场数据始终保持最新。
    使用 asyncio 异步实现，通过 asyncio.to_thread 执行同步代码。
    """

    def __init__(
        self,
        cache_plugin: CachePlugin,
        skill_service_factory: "SkillServiceFactory",
    ) -> None:
        self._started = False
        self._sync_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_sync_time: float = 0
        self._last_sync_result: dict[str, Any] | None = None
        self._cache_plugin = cache_plugin
        self._skill_service_factory = skill_service_factory

    async def start(self) -> bool:
        """启动定时任务

        仅 pre 和 prod 环境启动。
        """
        if self._started:
            logger.debug("[MarketSyncService] Already started")
            return True

        # 检查环境
        try:
            from agentclaw.community.utils.env_utils import get_current_env_with_gray
            current_env = await asyncio.to_thread(get_current_env_with_gray)
            if current_env not in ["pre", "prod"]:
                logger.info(f"[MarketSyncService] Environment '{current_env}' detected, skipping scheduled sync (only pre/prod allowed)")
                return False
        except Exception as e:
            logger.warning(f"[MarketSyncService] Failed to check environment: {e}")
            return False

        self._stop_event.clear()
        self._sync_task = asyncio.create_task(self._sync_loop())
        self._started = True
        logger.info(f"[MarketSyncService] Started, interval={SYNC_INTERVAL_MINUTES} minutes")
        return True

    async def stop(self) -> bool:
        """停止定时任务"""
        if not self._started:
            return True

        self._stop_event.set()
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        self._started = False
        logger.info("[MarketSyncService] Stopped")
        return True

    def is_running(self) -> bool:
        """检查服务是否运行中"""
        return self._started and self._sync_task is not None and not self._sync_task.done()

    def _get_lock_key(self) -> str:
        """获取分布式锁 key"""
        try:
            from agentclaw.community.utils.env_utils import get_current_env
            env = get_current_env() or "dev"
            return f"market_sync_lock:{env}"
        except Exception:
            return "market_sync_lock:dev"

    async def _sync_loop(self) -> None:
        """定时同步循环（异步）"""
        logger.info("[MarketSyncService] Sync loop started")

        while not self._stop_event.is_set():
            try:
                # 执行同步（异步）
                await self.exec_sync()
            except Exception as e:
                logger.error(f"[MarketSyncService] Sync error: {e}")

            # 等待下一次执行
            logger.debug(f"[MarketSyncService] Waiting {SYNC_INTERVAL_MINUTES} minutes for next sync")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=SYNC_INTERVAL_MINUTES * 60)
            except TimeoutError:
                pass  # 正常超时，继续下一次循环

        logger.info("[MarketSyncService] Sync loop ended")

    async def exec_sync(self) -> dict[str, Any]:
        """执行一次同步（异步）

        流程:
        1. 抢分布式锁
        2. Git pull 到本地仓库 /home/admin/aiworkbench/
        3. rsync 同步到目标目录（过滤 .git）
        4. 同步数据库 + 刷新缓存
        5. 释放锁

        注意：缓存刷新已内置在 sync_skills_from_git 中
        """
        from agentclaw.community.core.skill_center.services.skill_cache import GlobalSyncLock

        result = {
            "success": False,
            "git_pull": False,
            "db_sync": {},
            "error": None,
        }

        # 使用 GlobalSyncLock 防止并发同步
        if not GlobalSyncLock.acquire(timeout=60):
            logger.info("[MarketSyncService] GlobalSyncLock already held, skipping")
            result["error"] = "Sync already in progress (GlobalSyncLock)"
            return result

        logger.info("[MarketSyncService] Acquired GlobalSyncLock")

        # 1. 抢分布式锁（多机器互斥）
        cache = self._cache_plugin
        lock_key = self._get_lock_key()
        lock_value = await asyncio.to_thread(cache.acquire_lock, lock_key, ttl=300)

        if not lock_value:
            logger.info(f"[MarketSyncService] Lock already held: {lock_key}, skipping")
            GlobalSyncLock.release()
            result["error"] = "Lock already held by another instance"
            return result

        logger.info(f"[MarketSyncService] Acquired distributed lock: {lock_key}")

        try:
            # 2. Git pull 到 NAS（使用 pull-skills.sh，内部已包含 rsync）
            git_result = await asyncio.to_thread(self._do_git_pull)
            result["git_pull"] = git_result.get("success", False)
            result["target_dir"] = git_result.get("local_repo", "")
            logger.info(f"[MarketSyncService] Git sync result: {git_result}")

            if not git_result.get("success"):
                result["error"] = f"Git sync failed: {git_result.get('message')}"
                return result

            # 3. 同步数据库 + 刷新缓存
            db_result = await asyncio.to_thread(self._do_sync_db)
            result["db_sync"] = db_result
            result["success"] = True
            logger.info(f"[MarketSyncService] DB sync result: created={db_result.get('created', 0)}, updated={db_result.get('updated', 0)}, deleted={db_result.get('deleted', 0)}")

            self._last_sync_time = time.time()
            self._last_sync_result = result

        except Exception as e:
            logger.error(f"[MarketSyncService] Sync failed: {e}")
            result["error"] = str(e)
        finally:
            await asyncio.to_thread(cache.release_lock, lock_key, lock_value)
            GlobalSyncLock.release()
            logger.info(f"[MarketSyncService] Released locks: {lock_key}, GlobalSyncLock")

        return result

    def _do_git_pull(self) -> dict[str, Any]:
        """执行 Git pull 到本地仓库

        复用线上已部署的 pull-skills.sh 脚本逻辑：
        1. 调用 pull-skills.sh 从 aiworkbench 同步 skills 子目录到 NAS
        2. pull-skills.sh 内部使用 git archive 提取 skills 目录，排除 agents
        3. 通过 .skills-version 文件追踪版本

        Returns:
            Dict with keys:
                - success: bool
                - message: str
                - local_repo: str - 本地仓库路径
        """
        try:
            from agentclaw.community.core.workspace.path_factory import RSYNC_TARGET_DIR

            target_dir = Path(RSYNC_TARGET_DIR)
            pull_script = target_dir / "pull-skills.sh"

            logger.info(f"[MarketSyncService] Git sync using pull-skills.sh: target={target_dir}")

            # 确保目标目录存在
            target_dir.mkdir(parents=True, exist_ok=True)

            # 检查 pull-skills.sh 是否存在
            if not pull_script.exists():
                logger.error(f"[MarketSyncService] pull-skills.sh not found: {pull_script}")
                return {
                    "success": False,
                    "message": f"pull-skills.sh not found: {pull_script}",
                    "local_repo": str(target_dir)
                }

            # 执行 pull-skills.sh 脚本
            # 该脚本会：
            # 1. git fetch aiworkbench master --depth=1
            # 2. 比较 .skills-version 文件判断是否需要更新
            # 3. git archive 提取 skills 子目录到本地缓存
            # 4. rsync 同步到目标目录（排除 agents/ 等）
            # 5. 更新 .skills-version 文件
            logger.info(f"[MarketSyncService] Executing {pull_script}")
            result = subprocess.run(
                ["bash", str(pull_script)],
                cwd=target_dir,
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                stdout = result.stdout.strip()
                logger.info(f"[MarketSyncService] pull-skills.sh successful: {stdout}")
                return {
                    "success": True,
                    "message": f"pull-skills.sh: {stdout}",
                    "local_repo": str(target_dir)
                }
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                logger.error(f"[MarketSyncService] pull-skills.sh failed: {error_msg}")
                return {
                    "success": False,
                    "message": f"pull-skills.sh failed: {error_msg}",
                    "local_repo": str(target_dir)
                }

        except Exception as e:
            logger.error(f"[MarketSyncService] Git sync error: {e}")
            return {"success": False, "message": f"Git sync error: {str(e)}"}

    def _do_sync_db(self) -> dict[str, Any]:
        """同步数据库 + 刷新缓存（调用现有逻辑）

        从 rsync 目标目录同步技能到数据库
        """
        try:
            from agentclaw.community.core.workspace.path_factory import RSYNC_TARGET_DIR

            repo_dir = Path(RSYNC_TARGET_DIR)
            service = self._skill_service_factory.create(repo_dir=repo_dir)

            # sync_skills_from_git 内部会调用 _refresh_market_cache
            logger.info(f"[MarketSyncService] DB sync from: {repo_dir}")
            result = service.sync_skills_from_git(user_id=None)
            logger.info(f"[MarketSyncService] DB sync result: {result}")
            return result
        except Exception as e:
            logger.error(f"[MarketSyncService] DB sync error: {e}")
            return {"success": False, "error": str(e)}

    async def sync_now(self) -> dict[str, Any]:
        """手动触发同步（不等待定时）"""
        logger.info("[MarketSyncService] Manual sync triggered")
        return await self.exec_sync()

    def get_status(self) -> dict[str, Any]:
        """获取服务状态"""
        return {
            "started": self._started,
            "running": self.is_running(),
            "interval_minutes": SYNC_INTERVAL_MINUTES,
            "last_sync_time": self._last_sync_time,
            "last_sync_result": self._last_sync_result,
        }

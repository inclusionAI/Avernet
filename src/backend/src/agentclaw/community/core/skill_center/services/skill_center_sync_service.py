"""SkillCenterSyncService — 把 SC 上的 skill 产物同步到本地 NAS。

核心接口：
- force_sync(skill_uuid, env, version=None) → bool  （单 skill 同步）
- bootstrap() → dict  （启动时批量同步所有 center:// 已发布 skills）
- start_periodic_sync() → None  （启动定期同步）
- stop_periodic_sync() → None
- cleanup_stale(skill_uuid, keep=2) → int

被 4.1 SkillPropagationService._sync_nas_artifact 调用。
"""
import asyncio
import functools
import os
import random
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

import requests

from agentclaw.community.core.workspace.path_factory import get_bolt_shared_dir
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

NAS_ROOT_ENV = "SKILL_CENTER_NAS_ROOT"
DEFAULT_NAS_ROOT = os.path.expanduser("~/aiworkbench/skills-center")


@runtime_checkable
class SkillCenterSyncLogRepository(Protocol):
    """SC 同步日志 repository 协议（local SQLite / prod 关系存储实现）。"""

    def create(self, data: dict) -> dict:
        ...

    def mark_success(self, skill_uuid: str, version: str, env: str, checksum: str = None) -> None:
        ...


def _parse_version(version_str: str) -> tuple:
    """解析语义化版本号，返回可比较的 tuple。

    例如 '9.0.0' -> (9, 0, 0), '10.0.0' -> (10, 0, 0)。
    """
    if not version_str:
        return (0,)
    parts = version_str.split(".")
    return tuple(
        int(p) if p.isdigit() else 0
        for p in parts
    )


class SkillSyncError(Exception):
    """同步失败时抛出。4.1 _sync_nas_artifact 会 catch 此异常并仅记录日志。"""
    pass


def download_and_extract_zip(url: str, dst_dir: Path, timeout: int = 60) -> bool:
    """下载 ZIP 并解压到 dst_dir。返回是否成功。"""
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if url.startswith("file://"):
            zip_path = Path(url[7:])
            if not zip_path.is_absolute():
                zip_path = Path.cwd() / zip_path
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(dst_dir)
            return True

        # HTTP download
        with requests.get(url, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                tmp_zip = f.name
        try:
            with zipfile.ZipFile(tmp_zip, "r") as z:
                z.extractall(dst_dir)
        finally:
            os.unlink(tmp_zip)
        return True
    except Exception:
        logger.exception("[SkillSync] download_and_extract_zip failed: url=%s", url)
        return False


class SkillCenterSyncService(LifecycleBase):
    """把 SC 上的 skill 产物同步到本地 NAS。"""

    async def startup(self) -> None:
        """Lifecycle hook — batch-sync all center:// skills + start periodic sync.

        Body lifted from the pre-R11 ``startup_skill_center_sync_service``
        hook in ``api/lifecycle.py``. Errors propagate (fail-fast boot).
        """
        logger.info(f"[SkillCenterSyncService] startup, env={get_current_env()}")
        bootstrap_result = await self.sync_bootstrap()
        logger.info(f"SkillCenterSyncService bootstrap: {bootstrap_result}")
        await self.start_periodic_sync()
        logger.info("SkillCenterSyncService started via Lifecycle.startup()")

    async def shutdown(self) -> None:
        """Lifecycle hook — stop the periodic sync task."""
        await self.stop_periodic_sync()

    def __init__(
        self,
        skill_center_client,
        sync_log_repo,
        skill_repo,
        cache_plugin,
        skill_scan_service_provider,
        nas_root: str | None = None,
        nas_sync_enabled: bool | None = None,
    ):
        self._client = skill_center_client
        self._log_repo = sync_log_repo
        self._skill_repo = skill_repo
        self._cache_plugin = cache_plugin
        # Cycle-breaker: SkillScanService.__init__ needs SkillCenterSyncService,
        # so we hold a ``Callable[[], SkillScanService]`` and resolve on demand
        # inside scan_after_sync.
        self._skill_scan_service_provider = skill_scan_service_provider
        self._nas_root = Path(nas_root or os.environ.get(NAS_ROOT_ENV, DEFAULT_NAS_ROOT))

        # Resolve nas_sync_enabled: env var > config.get() > default False
        _env = get_current_env()
        _resolved = nas_sync_enabled
        if _resolved is None:
            _resolved = os.environ.get("SC_NAS_SYNC_ENABLED", "").lower() == "true"
        if not _resolved:
            try:
                from agentclaw.community.core.config import sofa

                sc_cfg = sofa.sofa_config.user_config.get("skill_center", {})
                _resolved = sc_cfg.get("nas_sync_enabled", False)
            except Exception:
                pass
        self._nas_sync_enabled = bool(_resolved)
        logger.info("[SkillCenterSyncService] nas_sync_enabled=%s (env=%s)",
                    self._nas_sync_enabled, _env)
        self._executor = ThreadPoolExecutor(max_workers=2)

    async def _run_sync(self, func, *args, **kwargs):
        """在线程池中运行同步函数。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            functools.partial(func, *args, **kwargs)
        )

    def force_sync(
        self,
        skill_uuid: str,
        env: str,
        version: str | None = None,
    ) -> bool:
        """同步指定 skill 到 NAS。

        Args:
            skill_uuid: ac_skill.skill_uuid（跨版本不变标识）
            env: 环境 dev / prod
            version: 目标版本，None = 取 SC 上 latest published version

        Returns:
            True = 成功或已跳过；抛 SkillSyncError = 失败

        Raises:
            SkillSyncError: 下载或写入失败时抛出
        """
        logger.info("[SkillCenterSyncService] force_sync start: skill_uuid=%s env=%s version=%s", skill_uuid, env, version)

        # 0. 用 skill_uuid 查 ac_skill.name（SC 的 skillCode）
        skill_code = skill_uuid
        if self._skill_repo is not None:
            logger.info("[SkillCenterSyncService] step0: calling get_by_uuid: skill_uuid=%s env=%s", skill_uuid, env)
            skill_record = self._skill_repo.get_by_uuid(skill_uuid, env)
            logger.info("[SkillCenterSyncService] step0: get_by_uuid returned: %s", skill_record is not None)
            if skill_record and skill_record.get("name"):
                skill_code = skill_record["name"]
                logger.info("[SkillCenterSyncService] resolved skill_uuid=%s -> skill_code=%s", skill_uuid, skill_code)
            else:
                logger.warning("[SkillCenterSyncService] skill_uuid=%s not found in DB or has no name, using as-is as skill_code", skill_uuid)

        # 1. 决定目标 version
        target_version = version
        if target_version is None:
            logger.info("[SkillCenterSyncService] step1: calling list_versions: skill_code=%s", skill_code)
            versions = self._client.list_versions(skill_code)
            if not versions:
                logger.error("[SkillCenterSyncService] no published versions: skill_code=%s", skill_code)
                raise SkillSyncError(f"No published versions for skill_code={skill_code}")
            # 按版本号数字排序，确保取到最新版本
            sorted_versions = sorted(versions, key=lambda v: _parse_version(v.get("versionNumber") or v.get("versionId") or "0"))
            target_version = sorted_versions[-1].get("versionNumber") or sorted_versions[-1].get("versionId", "1")
            logger.info("[SkillCenterSyncService] list_versions returned: skill_code=%s versions=%s sorted=%s selected=%s (latest)", skill_code, versions, sorted_versions, target_version)

        # 2. 检查是否已同步（skip）
        logger.info("[SkillCenterSyncService] step2: calling find_latest: skill_uuid=%s env=%s", skill_uuid, env)
        latest = self._log_repo.find_latest(skill_uuid, env)
        if latest and latest.get("status") == "success" and latest.get("version") == str(target_version):
            skill_dir = self._nas_root / skill_uuid
            if skill_dir.exists():
                logger.info("[SkillCenterSyncService] skip already synced: skill_uuid=%s version=%s", skill_uuid, target_version)
                return True

        # 3. 获取下载 URL
        logger.info("[SkillCenterSyncService] step3: calling get_download_url: skill_code=%s version=%s", skill_code, target_version)
        dl_result = self._client.get_download_url(skill_code, str(target_version))
        if not dl_result.get("success"):
            error_msg = dl_result.get("errorMsg", "unknown error")
            logger.error("[SkillCenterSyncService] get_download_url failed: skill_code=%s error=%s", skill_code, error_msg)
            raise SkillSyncError(f"get_download_url failed for skill_code={skill_code}: {error_msg}")

        download_url = dl_result["data"].get("intranetDownloadUrl") or dl_result["data"].get("officeDownloadUrl")
        if not download_url:
            logger.error("[SkillCenterSyncService] get_download_url returned no URL: skill_code=%s", skill_code)
            raise SkillSyncError(f"get_download_url returned no URL for skill_code={skill_code}")
        logger.info("[SkillCenterSyncService] got download_url: skill_code=%s url=%s", skill_code, download_url[:80])

        # 4. 写 pending log
        logger.info("[SkillCenterSyncService] step4: writing pending sync_log: skill_uuid=%s version=%s env=%s", skill_uuid, target_version, env)
        self._log_repo.create({
            "skill_uuid": skill_uuid,
            "version": str(target_version),
            "env": env,
            "status": "pending",
        })

        # 5. 下载到 tmp 目录
        tmp_dir = self._nas_root / skill_uuid / f"_tmp_{int(time.time() * 1000)}"
        logger.info("[SkillCenterSyncService] downloading to tmp_dir: skill_uuid=%s tmp_dir=%s", skill_uuid, tmp_dir)
        try:
            ok = download_and_extract_zip(download_url, tmp_dir)
            if not ok:
                logger.error("[SkillCenterSyncService] download failed: skill_uuid=%s", skill_uuid)
                raise SkillSyncError(f"download failed for skill_uuid={skill_uuid}")
            logger.info("[SkillCenterSyncService] download & extract OK: skill_uuid=%s tmp_dir=%s", skill_uuid, tmp_dir)
        except SkillSyncError:
            self._log_repo.mark_failed(skill_uuid, str(target_version), env, "download failed")
            raise
        except Exception as exc:
            self._log_repo.mark_failed(skill_uuid, str(target_version), env, str(exc)[:500])
            logger.error("[SkillCenterSyncService] sync failed: skill_uuid=%s error=%s", skill_uuid, exc)
            raise SkillSyncError(f"sync failed: {exc}") from exc

        # 6. atomic rename 到目标目录
        version_dir = self._nas_root / skill_uuid / str(target_version)
        logger.info("[SkillCenterSyncService] moving to version_dir: skill_uuid=%s from=%s to=%s", skill_uuid, tmp_dir, version_dir)
        if version_dir.exists():
            shutil.rmtree(version_dir)
        shutil.move(str(tmp_dir), str(version_dir))
        logger.info("[SkillCenterSyncService] move done: skill_uuid=%s version_dir=%s", skill_uuid, version_dir)

        # 7. 更新 current symlink
        current_link = self._nas_root / skill_uuid / "current"
        if current_link.is_symlink() or current_link.exists():
            current_link.unlink()
        try:
            current_link.symlink_to(str(target_version))
            logger.info("[SkillCenterSyncService] symlink created: skill_uuid=%s link=%s -> %s", skill_uuid, current_link, target_version)
        except OSError as e:
            logger.warning("[SkillCenterSyncService] symlink not supported on NAS (OSError): skill_uuid=%s error=%s", skill_uuid, e)

        # 7. 写 success log
        self._log_repo.mark_success(skill_uuid, str(target_version), env)
        logger.info(
            "[SkillCenterSyncService] force_sync done: skill_uuid=%s version=%s env=%s → %s",
            skill_uuid, target_version, env, version_dir,
        )

        # 扫描 MCP 依赖（不阻塞主链路）
        self.scan_after_sync(skill_uuid, env)

        return True

    def cleanup_stale(self, skill_uuid: str, keep: int = 2) -> int:
        """清理旧版本，保留最近 N 个。

        Args:
            skill_uuid: skill UUID
            keep: 保留最近 N 个版本（不包括 current symlink 指向的版本）

        Returns:
            删除的版本数量
        """
        skill_dir = self._nas_root / skill_uuid
        if not skill_dir.exists():
            return 0

        versions = sorted(
            [
                p.name
                for p in skill_dir.iterdir()
                if p.is_dir() and not p.is_symlink() and not p.name.startswith("_tmp")
            ],
            reverse=True,
        )

        current_target = None
        current_link = skill_dir / "current"
        if current_link.is_symlink():
            current_target = os.readlink(str(current_link))

        protected = {current_target} if current_target else set()
        protected.update(versions[:keep])

        removed = 0
        for v in versions:
            if v not in protected:
                shutil.rmtree(skill_dir / v)
                logger.info("[SkillSync] removed stale version: skill_uuid=%s version=%s", skill_uuid, v)
                removed += 1

        return removed

    def is_synced(self, skill_uuid: str, env: str, version: str | None = None) -> bool:
        """检查指定 skill+version 是否已同步。"""
        latest = self._log_repo.find_latest(skill_uuid, env)
        if not latest or latest.get("status") != "success":
            return False
        target_version = version or latest.get("version")
        if target_version and latest.get("version") != str(target_version):
            return False
        # 验证 version 子目录存在（不只是 base dir）
        version_dir = self._nas_root / skill_uuid / str(target_version)
        return version_dir.exists()

    # =========================================================================
    # 批量 / 定时同步（与 GitSyncService 对齐）
    # =========================================================================

    async def sync_bootstrap(self) -> dict:
        """启动时批量同步所有 center:// 已发布 skill 到本地。

        查询本地 ac_skill 表中 git_path 以 'center://' 开头且 status='PUBLISHED' 的 skill，
        对每个 skill 调用 force_sync()。

        双锁保护：
        - GlobalSyncLock：防止同进程内并发（8 个 SpawnProcess worker）
        - 分布式锁：防止多机器并发（跨进程/机器）
        """
        from agentclaw.community.core.skill_center.services.skill_cache import GlobalSyncLock

        result = {"synced": 0, "failed": 0, "skipped": 0}

        # 1. GlobalSyncLock（进程内锁）
        if not GlobalSyncLock.acquire(timeout=60):
            logger.warning("[SkillCenterSyncService] bootstrap: GlobalSyncLock held, skipping")
            result["error"] = "GlobalSyncLock held"
            return result

        try:
            # 2. 分布式锁（跨进程/机器）
            env = get_current_env()
            cache = self._cache_plugin
            lock_key = f"skill_center_sync:{env}"
            lock_value = await self._run_sync(
                functools.partial(cache.acquire_lock, lock_key, ttl=600)
            )

            if not lock_value:
                logger.warning(
                    "[SkillCenterSyncService] bootstrap: distributed lock held: key=%s, skipping",
                    lock_key,
                )
                result["error"] = "Distributed lock held"
                return result

            try:
                # 3. 执行 bootstrap 主体
                inner = await self._bootstrap_inner(env)
                result.update(inner)
            finally:
                await self._run_sync(
                    functools.partial(cache.release_lock, lock_key, lock_value)
                )
                logger.info("[SkillCenterSyncService] bootstrap: released distributed lock: %s", lock_key)

        finally:
            GlobalSyncLock.release()
            logger.info("[SkillCenterSyncService] bootstrap: released GlobalSyncLock")

        logger.info("[SkillCenterSyncService] bootstrap done: %s", result)
        return result

    async def _bootstrap_inner(self, env: str) -> dict:
        """bootstrap 核心逻辑（在双锁保护下执行）。

        使用 list_published_center_skills() 而非 list_skills() —— 后者默认参数
        bolt_id='default' 会漏掉 bolt_id 为具体 bot_xxx 的 center:// skill。
        center:// skill 是全局共享的，同步行为不应受 bolt_id 限制。
        """
        if self._skill_repo is None:
            logger.warning("[SkillCenterSyncService] bootstrap: no skill_repo, skipping")
            return {"synced": 0, "failed": 0, "skipped": 0}

        try:
            center_skills = self._skill_repo.list_published_center_skills()
            logger.info(
                "[SkillCenterSyncService] bootstrap: found %d center:// published skills (env=%s)",
                len(center_skills), env,
            )
        except Exception as exc:
            logger.error("[SkillCenterSyncService] bootstrap: failed to list center skills: %s", exc)
            return {"synced": 0, "failed": 0, "skipped": 0, "error": str(exc)}

        synced = failed = skipped = 0
        for skill in center_skills:
            skill_uuid = skill.get("skill_uuid")
            if not skill_uuid:
                skipped += 1
                continue
            try:
                self.force_sync(skill_uuid=skill_uuid, env=env, version=None)
                synced += 1
            except Exception as exc:
                logger.warning(
                    "[SkillCenterSyncService] bootstrap: force_sync failed: "
                    "skill_uuid=%s error=%s",
                    skill_uuid, exc,
                )
                failed += 1

        return {"synced": synced, "failed": failed, "skipped": skipped}

    # 定时同步相关状态
    _periodic_started: bool = False
    _periodic_task: asyncio.Task | None = None
    _executor: ThreadPoolExecutor | None = None
    SYNC_INTERVAL_MINUTES: int = 1
    SYNC_JITTER_SECONDS: int = 60

    async def start_periodic_sync(self) -> None:
        """启动定期同步（每 30min 增量同步 center:// 已有 skills）。"""
        if self._periodic_started:
            return
        self._periodic_started = True
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._periodic_task = asyncio.create_task(self._periodic_sync_loop())
        logger.info(
            "[SkillCenterSyncService] periodic sync started (%dmin interval)",
            self.SYNC_INTERVAL_MINUTES,
        )

    async def stop_periodic_sync(self) -> None:
        """停止定期同步。"""
        if not self._periodic_started:
            return
        self._periodic_started = False
        if self._periodic_task and not self._periodic_task.done():
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
        if self._executor:
            self._executor.shutdown(wait=False)
        logger.info("[SkillCenterSyncService] periodic sync stopped")

    async def _periodic_sync_loop(self) -> None:
        """定期同步循环。"""
        while self._periodic_started:
            try:
                jitter = random.randint(0, self.SYNC_JITTER_SECONDS)
                await asyncio.sleep(jitter)

                result = await asyncio.get_event_loop().run_in_executor(
                    self._executor, self._sync_all_center_skills
                )
                logger.info(
                    "[SkillCenterSyncService] periodic sync result: %s",
                    result,
                )
                # 全量目录同步到 NAS（类比 GitSyncService._sync_to_oss_sync）
                if self._nas_sync_enabled:
                    nas_result = await asyncio.get_event_loop().run_in_executor(
                        self._executor, self._sync_to_nas_all
                    )
                    logger.info(
                        "[SkillCenterSyncService] _sync_to_nas_all result: %s",
                        nas_result,
                    )
            except Exception as exc:
                logger.error(
                    "[SkillCenterSyncService] periodic sync error: %s",
                    exc,
                )

            await asyncio.sleep(self.SYNC_INTERVAL_MINUTES * 60)

    def _sync_all_center_skills(self) -> dict:
        """同步所有 center:// 已发布 skill（在线程池中执行）。

        使用 list_published_center_skills() 而非 list_skills() —— 同 bootstrap：
        center:// skill 是全局共享的，bolt_id 字段不应限制同步范围。
        """
        if self._skill_repo is None:
            return {"synced": 0, "failed": 0, "skipped": 0}

        try:
            env = get_current_env()
            center_skills = self._skill_repo.list_published_center_skills()
            logger.info(
                "[SkillCenterSyncService] periodic sync: found %d center:// published skills (env=%s)",
                len(center_skills), env,
            )
        except Exception as exc:
            logger.error("[SkillCenterSyncService] periodic sync: list_published_center_skills failed: %s", exc)
            return {"synced": 0, "failed": 0, "skipped": 0, "error": str(exc)}

        synced = failed = skipped = 0
        for skill in center_skills:
            skill_uuid = skill.get("skill_uuid")
            if not skill_uuid:
                skipped += 1
                continue

            try:
                # === 增量检查：先比版本，避免无意义的 SC API 调用 ===
                # 0. 解析 skill_code（get_by_uuid 已自带 status=PUBLISHED 过滤）
                skill_code = skill_uuid
                if self._skill_repo is not None:
                    record = self._skill_repo.get_by_uuid(skill_uuid, env)
                    if record and record.get("name"):
                        skill_code = record["name"]

                # 1. 查 SC 最新版本
                versions = self._client.list_versions(skill_code)
                if not versions:
                    logger.warning(
                        "[SkillCenterSyncService] periodic sync: no versions for skill_code=%s, skipping",
                        skill_code,
                    )
                    skipped += 1
                    continue
                # 按版本号数字排序，确保取到最新版本
                sorted_versions = sorted(versions, key=lambda v: _parse_version(v.get("versionNumber") or v.get("versionId") or "0"))
                latest_version = sorted_versions[-1].get("versionNumber") or sorted_versions[-1].get("versionId", "1")

                # 2. 查本地已同步版本
                last_synced = self._log_repo.find_latest(skill_uuid, env)
                last_version = last_synced.get("version") if last_synced else None

                # 3. 版本一致则跳过
                if last_version == str(latest_version):
                    logger.info(
                        "[SkillCenterSyncService] periodic sync: skip unchanged skill_uuid=%s version=%s",
                        skill_uuid, latest_version,
                    )
                    skipped += 1
                    continue

                # 4. 版本不一致，调 force_sync 拉取新版本
                logger.info(
                    "[SkillCenterSyncService] periodic sync: version changed skill_uuid=%s %s -> %s, syncing",
                    skill_uuid, last_version, latest_version,
                )
                self.force_sync(skill_uuid=skill_uuid, env=env, version=str(latest_version))
                synced += 1

            except SkillSyncError as exc:
                logger.warning(
                    "[SkillCenterSyncService] periodic sync: force_sync failed skill_uuid=%s: %s",
                    skill_uuid, exc,
                )
                failed += 1
            except Exception as exc:
                logger.warning(
                    "[SkillCenterSyncService] periodic sync: unexpected error skill_uuid=%s: %s",
                    skill_uuid, exc,
                )
                failed += 1

        return {"synced": synced, "failed": failed, "skipped": skipped}

    def _sync_to_nas_all(self) -> dict:
        """把整个 ~/aiworkbench/skills-center/ 同步到 NAS bolt_shared/skills-center/。

        参考 GitSyncService._sync_to_oss_sync 的 rsync 逻辑。
        每次 periodic sync 完成后调用，做全量目录级增量同步。

        注意：rsync **不带 --delete**。本地目录是当前机器视图的子集（force_sync
        只对"本地版本 ≠ SC 最新版本"的 skill 生效），不是 NAS 的权威源；如果
        加 --delete，多机器 / 手动上传场景下会把 NAS 上其他机器同步过的 skill
        当作"多余文件"删掉。NAS 旧版本清理由 cleanup_stale 按 sync_log 驱动。
        """
        if not self._nas_sync_enabled:
            return {"skipped": True, "reason": "nas_sync_enabled=False"}

        local_src = self._nas_root  # ~/aiworkbench/skills-center/
        if not local_src.exists():
            logger.warning("[SkillCenterSyncService] _sync_to_nas_all: source not found: %s", local_src)
            return {"success": False, "error": "source not found"}

        try:
            bolt_shared = get_bolt_shared_dir()
            nas_dst = bolt_shared / "skills-center"
            nas_dst.mkdir(parents=True, exist_ok=True)

            logger.info("[SkillCenterSyncService] _sync_to_nas_all: rsync %s -> %s", local_src, nas_dst)
            result = subprocess.run(
                ["rsync", "-rltD",
                 str(local_src) + "/", str(nas_dst) + "/"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info("[SkillCenterSyncService] _sync_to_nas_all done: %s -> %s", local_src, nas_dst)
                return {"success": True}
            else:
                logger.warning("[SkillCenterSyncService] _sync_to_nas_all failed: %s", result.stderr)
                return {"success": False, "error": result.stderr}
        except Exception as e:
            logger.warning("[SkillCenterSyncService] _sync_to_nas_all error: %s", e)
            return {"success": False, "error": str(e)}

    def scan_after_sync(self, skill_uuid: str, env: str) -> None:
        """同步完成后扫描 NAS 目录，更新 mcp_dependencies / risk_tags。

        current symlink 不存在时静默跳过。扫描或 DB 写入异常不向外传播，
        保证不影响主同步链路。
        """
        current_link = self._nas_root / skill_uuid / "current"
        if not current_link.exists():
            logger.info(
                "[SkillCenterSyncService] scan_after_sync: current link missing, skip: %s",
                skill_uuid,
            )
            return

        skill_path = str(current_link.resolve())
        logger.info(
            "[SkillCenterSyncService] scan_after_sync: scanning %s (env=%s)",
            skill_path, env,
        )

        try:
            scan_svc = self._skill_scan_service_provider()
            # Self-healing start(): the startup hook at
            # api/app.py:startup_skill_scan_service swallows SDK init
            # failures with a bare except. Without this call, a transient
            # init failure at boot would permanently break every
            # scan_after_sync for the process lifetime. start() is
            # idempotent (no-op once _started is True), so re-invoking
            # it here is free on the hot path.
            scan_svc.start()
            result = scan_svc.scan_skill(skill_path)

            if result is None:
                logger.warning(
                    "[SkillCenterSyncService] scan_after_sync: SDK returned None for %s",
                    skill_uuid,
                )
                return

            if self._skill_repo is None:
                logger.warning(
                    "[SkillCenterSyncService] scan_after_sync: skill_repo not injected, skipping DB update"
                )
                return
            skill = self._skill_repo.get_by_uuid(skill_uuid, env)
            if not skill:
                logger.warning(
                    "[SkillCenterSyncService] scan_after_sync: skill not found in DB: %s",
                    skill_uuid,
                )
                return

            skill_id = str(skill["id"])
            skill_repo = self._skill_repo

            mcp_deps_raw = getattr(result, "mcp_dependencies", None) or []
            mcp_dependencies = [
                {"code": getattr(d, "code", ""), "name": getattr(d, "name", ""), "url": getattr(d, "url", "")}
                if not isinstance(d, dict) else
                {"code": d.get("code", ""), "name": d.get("name", ""), "url": d.get("url", "")}
                for d in mcp_deps_raw
            ]

            risk_tags_raw = getattr(result, "risk_tags", None) or []
            risk_tags = [
                t.model_dump() if hasattr(t, "model_dump") else t
                for t in risk_tags_raw
            ]

            skill_repo.update_mcp_dependencies(skill_id, mcp_dependencies)
            skill_repo.update_risk_tags(skill_id, risk_tags)

            logger.info(
                "[SkillCenterSyncService] scan_after_sync: done skill_uuid=%s mcp=%d risk=%d",
                skill_uuid, len(mcp_dependencies), len(risk_tags),
            )

        except Exception as exc:
            logger.warning(
                "[SkillCenterSyncService] scan_after_sync: failed for %s: %s",
                skill_uuid, exc,
            )

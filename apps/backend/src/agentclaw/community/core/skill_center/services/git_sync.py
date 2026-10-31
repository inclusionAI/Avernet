"""Git Sync Service - Unified git synchronization service for skills and agents.

This service handles:
1. Bootstrap: Download or clone aiworkbench repository on startup
2. Sync: Periodic fetch, archive, and rsync of subtrees (skills, agents)
3. Cache refresh: Update market cache after sync
4. Archive: Nightly zip creation for cold start acceleration
"""

import asyncio
import functools
import os
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

from agentclaw.community.core.skill_center.services.git_sync_subtrees import GitSyncSubtreeIO
from agentclaw.community.core.skill_center.services.git_sync_storage import GitSyncStorageIO

from agentclaw.community.core.expert_market.protocols import ExpertScannerProtocol
from agentclaw.community.core.skill_center.constants import (
    DISTRIBUTED_LOCK_HELD,
    GLOBAL_SYNC_LOCK_HELD,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.core.skill_center.git_sync_service_protocol import GitSyncServiceProtocol

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.factories import SkillServiceFactory


logger = get_logger()

# The secret-store key holding the skills source repo URL is deployment config
# (``SecretNamesConfig.aiworkbench_repo_url``, from the ``secret_names`` yaml
# block) — corp env overlays set the real secret-registry name; the neutral
# shipped code carries no secret reference (OSS-0 #3). Empty name = no repo-URL
# secret (community / permissive local: git-sync degrades to the on-disk source).


def _resolve_temp_cache_base() -> Path:
    """Resolve the base dir for subtree extraction temp caches.

    Prefers ``/dev/shm`` (tmpfs, fast) on Linux/CI; falls back to the OS
    temp dir when ``/dev/shm`` is missing or not writable (e.g. macOS dev).
    Overridable via ``GIT_SYNC_TMP_BASE``.
    """
    override = os.getenv("GIT_SYNC_TMP_BASE")
    if override:
        return Path(override)
    shm = Path("/dev/shm")
    if shm.exists() and os.access(shm, os.W_OK):
        return shm
    return Path(tempfile.gettempdir())


class GitSyncConfig:
    """Configuration for GitSyncService"""

    def __init__(self):
        from agentclaw.community.core.workspace.path_factory import get_bolt_shared_dir

        # NOTE: the source repo URL is NOT a config field — it is a runtime secret
        # (it carries a PAT). GitSyncService resolves it from the secret store; see
        # ``GitSyncService._repo_url``.
        self.branch = "master"

        # Local disk paths (fast I/O for sync and cache building)
        local_base = Path.home() / "aiworkbench"
        self.skills_target = local_base / "skills-repo"
        self.agents_target = local_base / "agents-repo"

        # OSS/backup paths (for persistent storage)
        bolt_shared = get_bolt_shared_dir()
        self.skills_oss = bolt_shared / "skills-repo"
        self.agents_oss = bolt_shared / "agents-repo"
        self.archive_zip = bolt_shared / "aiworkbench.tar.gz"

        # OSS object paths for desktop VM download
        env = _get_env()
        self.oss_archive_path = f"aidesktop/aidesktop_{env}/bolt_shared/aiworkbench.tar.gz"
        self.oss_meta_path = f"agentclaw-sys/skills-repo-meta-{env}.json"

        # Local paths (container local disk)
        self.local_bare_repo = local_base / "aiworkbench.git"

        # Enable OSS sync (background backup to OSS)。
        # 优先级：显式 env 胜出（singlebox local_setup 设 ENABLE_OSS_SYNC=false 强关，
        # 否则会被 application-dev.yaml 的 true 翻盘）；env 未设才回落 config。
        _env_oss = os.getenv("ENABLE_OSS_SYNC")
        self.enable_oss_sync = (_env_oss or "false").lower() == "true"
        # 仅当 env 完全未设（None）时才读 config；显式设 false 不再被 config 覆盖。
        if _env_oss is None and not self.enable_oss_sync:
            try:
                import yaml
                # B11: configs live in the community subtree; a deploy's assembled
                # runtime `configs/` (cwd) holds them too.
                config_path = Path.cwd() / "configs" / "application.yaml"
                if not config_path.exists():
                    config_path = Path(__file__).resolve().parents[3] / "configs" / "application.yaml"
                if config_path.exists():
                    with open(config_path) as f:
                        cfg = yaml.safe_load(f)
                    if cfg and "user_config" in cfg:
                        git_sync_cfg = cfg["user_config"].get("git_sync", {})
                        self.enable_oss_sync = git_sync_cfg.get("enable_oss_sync", False)
                        if self.enable_oss_sync:
                            logger.info(f"[GitSyncConfig] enable_oss_sync={self.enable_oss_sync} from config")
            except Exception:
                pass

        # Whether to sync skills from the git repo into the database at startup.
        # Community deployments set this to false in the overlay (no corp git repo).
        self.enable_skill_sync = True
        try:
            from agentclaw.community.core.config import sofa

            _uc = sofa.sofa_config.model_dump().get("user_config", {}) or {}
            self.enable_skill_sync = (_uc.get("git_sync", {}) or {}).get(
                "enable_skill_sync", True
            )
        except Exception:
            pass

        # Subtree configurations
        self.subtrees: list[dict[str, Any]] = [
            {
                "name": "skills",
                "source_path": "skills",
                "target_dir": self.skills_target,
                "oss_dir": self.skills_oss,
                "version_file": ".skills-version",
            },
            {
                "name": "agents",
                "source_path": "agents",
                "target_dir": self.agents_target,
                "oss_dir": self.agents_oss,
                "version_file": ".agents-version",
            }
        ]

        # Externally-reachable OSS endpoint for desktop-VM download (deployment
        # config: git_sync.office_oss_endpoint in the merged yaml; empty = no
        # presigned-URL rewrite). Read from the merged config so a corp env
        # overlay supplies it.
        self.office_oss_endpoint = ""
        try:
            from agentclaw.community.core.config import sofa

            _uc = sofa.sofa_config.model_dump().get("user_config", {}) or {}
            self.office_oss_endpoint = (_uc.get("git_sync", {}) or {}).get(
                "office_oss_endpoint", ""
            )
        except Exception:
            pass

        # Sync schedule
        self.sync_interval_minutes = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))
        self.sync_jitter_seconds = int(os.getenv("SYNC_JITTER_SECONDS", "60"))
        if self.sync_interval_minutes < 1:
            raise ValueError("SYNC_INTERVAL_MINUTES must be positive")
        if self.sync_jitter_seconds < 0:
            raise ValueError("SYNC_JITTER_SECONDS must be non-negative")
        # 抢不到 bootstrap 锁的 worker 轮询等待 bare repo 就绪的超时（秒）
        self.bootstrap_wait_timeout = int(os.getenv("BOOTSTRAP_WAIT_TIMEOUT", "60"))
        self.archive_cron = "0 0 * * *"  # Daily at 00:00
        self.archive_max_age_hours = 24

        # Remote name in git config
        self.remote_name = "origin"



class GitSyncService(GitSyncSubtreeIO, GitSyncStorageIO, LifecycleBase, GitSyncServiceProtocol):
    """Unified Git synchronization service.

    Handles:
    - Bootstrap: Initial setup from zip or clone
    - Sync: Periodic fetch and subtree sync
    - Cache refresh: Non-blocking market cache update
    - Archive: Nightly zip creation
    """

    async def startup(self) -> None:
        """Lifecycle hook — seed local-only profiles or start periodic sync.

        Body lifted from the pre-R11 ``startup_git_sync_service`` hook in
        ``api/lifecycle.py``. Remote reconciliation is deliberately deferred
        to the periodic task so backend readiness does not depend on Git/OSS.
        """
        if not self.config.enable_skill_sync:
            logger.info(
                "[GitSyncService] Skill sync disabled (enable_skill_sync=false); "
                "skipping bootstrap, database sync, and periodic sync"
            )
            return

        if self._repo_url is None:
            seed_result = await self._sync_existing_local_market()
            logger.info(f"GitSyncService local bootstrap: {seed_result}")
            logger.info("GitSyncService started via Lifecycle.startup() in local mode")
            return

        await self.start_periodic_sync()
        logger.info(
            "GitSyncService started via Lifecycle.startup(); "
            "first remote reconciliation is deferred to the periodic interval"
        )

    async def shutdown(self) -> None:
        """Lifecycle hook — stop periodic sync and shut down the executor."""
        await self.stop_periodic_sync()
        if self._executor:
            self._executor.shutdown(wait=True)
            logger.info("[GitSyncService] Executor shutdown")

    def __init__(
        self,
        cache_plugin: CachePlugin,
        skill_service_factory: "SkillServiceFactory",
        config: GitSyncConfig,
        oss_storage: ObjectStoragePlugin,
        secret_resolver: SecretResolver,
        allow_missing_repo_url: bool = False,
        repo_url_secret_name: str = "",
        expert_scanner: ExpertScannerProtocol | None = None,
        expert_source_repo_id: str = "aiworkbench",
    ):
        self.config = config
        self._expert_scanner = expert_scanner
        self._expert_source_repo_id = expert_source_repo_id
        # The source repo URL is resolved through SecretResolver. Corp's
        # resolver reads the deployed secret backend; local/singlebox can
        # synthesize selected secrets
        # from local config while keeping the same service contract.
        # Local profiles may also already have a host-side
        # ~/aiworkbench/skills-repo. If the resolver has no repo URL, the
        # service stays constructible and seeds the market DB/cache from disk.
        # Corp profiles still fail loudly when the secret is missing.
        self._local_profile = allow_missing_repo_url
        self._repo_url = self._resolve_repo_url_from_secret(
            secret_resolver,
            secret_name=repo_url_secret_name,
            allow_missing=self._local_profile,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="git_sync_"
        )
        self._sync_task: asyncio.Task | None = None
        self._started = False
        self._cache_plugin = cache_plugin
        self._skill_service_factory = skill_service_factory
        self._oss_storage = oss_storage

    @staticmethod
    def _resolve_repo_url_from_secret(
        secret_resolver: SecretResolver,
        *,
        secret_name: str = "",
        allow_missing: bool = False,
    ) -> str | None:
        """Read the skills source repo URL from the secret store.

        Fails loudly if the secret is absent/empty (or the lookup errors) — the
        repo URL is required corp infrastructure and silently going inert would
        hide a misconfiguration. When no ``secret_name`` is configured (empty)
        there is no repo-URL secret to read: permissive profiles degrade to the
        on-disk source, strict (prod) profiles fail loudly.
        """
        secret = (
            secret_resolver.get_secret(secret_name) if secret_name else None
        )
        if secret is None or not secret.secret_value:
            if allow_missing:
                logger.info(
                    "[GitSyncService] repo URL secret missing; using local "
                    "skills-repo source for local profile"
                )
                return None
            raise RuntimeError(
                "Skills repo URL not found in the secret store (secret "
                f"name {secret_name!r}); GitSyncService cannot start."
            )
        logger.info("[GitSyncService] repo URL resolved from the secret store")
        return str(secret.secret_value)

    async def _run_sync(self, func, *args, **kwargs):
        """Run synchronous function in thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            functools.partial(func, *args, **kwargs)
        )

    # ==========================================================================
    # Bootstrap - Initial setup on service start
    # ==========================================================================

    async def sync_bootstrap(self) -> dict[str, Any]:
        """Bootstrap the local bare repository.

        Flow:
        1. Check if local bare repo exists
        2. If not, acquire distributed lock and clone (or fallback to OSS tar)
        3. Other workers skip if lock is held (the lock holder will succeed)
        """
        result = {"success": False, "method": None, "duration": 0}
        start_time = time.time()
        cache = self._cache_plugin

        try:
            if self._repo_url is None:
                if self.config.skills_target.is_dir():
                    result["success"] = True
                    result["method"] = "local_existing"
                else:
                    result["method"] = "local_missing"
                    result["error"] = (
                        f"local skills repo not found: {self.config.skills_target}"
                    )
                result["duration"] = time.time() - start_time
                return result

            # Check if already bootstrapped
            if self.config.local_bare_repo.exists():
                logger.info(f"[GitSyncService] Bootstrap: local repo already exists at {self.config.local_bare_repo}")
                return await self._bootstrap_existing_result(result)

            # Ensure parent directory exists
            self.config.local_bare_repo.parent.mkdir(parents=True, exist_ok=True)

            # Serialize clone across processes
            bootstrap_lock_key = f"git_sync_bootstrap:{_get_env()}"
            bootstrap_lock_value = await self._run_sync(
                functools.partial(cache.acquire_lock, bootstrap_lock_key, ttl=600)
            )

            if not bootstrap_lock_value:
                # Lock holder is bootstrapping. Do NOT optimistically return
                # success — wait until the bare repo actually exists, else the
                # caller may proceed on a repo that was never built.
                logger.info(
                    "[GitSyncService] Bootstrap: another process holds the lock, "
                    "waiting for bare repo to become ready"
                )
                waited = 0
                while not self.config.local_bare_repo.exists():
                    if waited >= self.config.bootstrap_wait_timeout:
                        logger.error(
                            "[GitSyncService] Bootstrap: lock holder did not produce "
                            "bare repo within %ds; giving up (self-heal will retry)",
                            self.config.bootstrap_wait_timeout,
                        )
                        result["success"] = False
                        result["method"] = "wait_timeout"
                        result["error"] = "bootstrap by lock holder did not complete in time"
                        return result
                    await asyncio.sleep(2)
                    waited += 2
                logger.info("[GitSyncService] Bootstrap: bare repo ready (built by lock holder)")
                return await self._bootstrap_existing_result(result)

            try:
                if self.config.local_bare_repo.exists():
                    logger.info("[GitSyncService] Bootstrap: repo created while waiting for lock")
                    return await self._bootstrap_existing_result(result)

                result.update(await self._bootstrap_clone_or_fallback())
            finally:
                if bootstrap_lock_value:
                    cache.release_lock(bootstrap_lock_key, bootstrap_lock_value)
                    logger.info("[GitSyncService] Bootstrap: released bootstrap lock")

            result["duration"] = time.time() - start_time
            logger.info(f"[GitSyncService] Bootstrap completed in {result['duration']:.2f}s via {result['method']}")

        except Exception as e:
            logger.error(f"[GitSyncService] Bootstrap failed: {e}")
            result["error"] = str(e)

        return result

    async def _bootstrap_clone_or_fallback(self) -> dict[str, Any]:
        # Fall back to OSS only for the clone step. A successful bare clone has
        # already built the bootstrap target; post-clone dependency failures must
        # not rerun this path because the guaranteed OSS overwrite check would
        # deliberately reject the existing bare repo and mask the real failure.
        logger.info("[GitSyncService] Bootstrap: cloning repository...")
        try:
            await self._clone_bare_repo()
            logger.info("[GitSyncService] Bootstrap: bare clone completed")
        except Exception as e:
            logger.error(
                "[GitSyncService] Bootstrap: clone failed (%s), "
                "falling back to OSS tar", e,
            )
            try:
                await self._download_from_oss_and_extract()
                logger.info("[GitSyncService] Bootstrap: OSS fallback download completed")
                return {"success": True, "method": "oss_fallback"}
            except Exception as fe:
                logger.error(
                    "[GitSyncService] Bootstrap: OSS fallback also failed (%s); "
                    "bare repo not built (self-heal will retry next cycle)", fe,
                )
                return {
                    "success": False,
                    "method": "failed",
                    "error": f"clone and fallback both failed: {fe}",
                }

        try:
            # Fetch to create FETCH_HEAD required by subtree extraction
            fetch_result = await self._git_fetch()
            if not fetch_result["success"]:
                raise RuntimeError(f"Git fetch failed after clone: {fetch_result.get('error')}")
            logger.info("[GitSyncService] Bootstrap: git fetch completed")

            # Extract skills subtree so VM gets the working directory, not bare repo
            subtree_result = await self._sync_subtree(self.config.subtrees[0])
            if isinstance(subtree_result, dict) and not subtree_result.get("success", False):
                raise RuntimeError(f"Skills subtree extraction failed: {subtree_result.get('error')}")
            logger.info("[GitSyncService] Bootstrap: skills subtree extracted")
            if self._expert_scanner is not None:
                from agentclaw.community.core.expert_market.git_sync import sync_git_experts
                await self._run_sync(sync_git_experts, self, fetch_result.get("fetch_head"))

            # Upload to OSS so desktop VMs can download the initial tar.gz immediately.
            # singlebox/offline 无 OSS（mock:// storage）→ enable_oss_sync=false 时跳过。
            if self.config.enable_oss_sync:
                logger.info("[GitSyncService] Bootstrap: uploading skills repo to OSS...")
                await self._run_sync(self._sync_upload_skills_repo_to_oss)
                logger.info("[GitSyncService] Bootstrap: OSS upload completed")
                await self._run_sync(self._sync_refresh_meta_to_oss)
                logger.info("[GitSyncService] Bootstrap: meta JSON refreshed")
            else:
                logger.info("[GitSyncService] Bootstrap: OSS sync disabled, skip upload/meta")

            return {"success": True, "method": "clone"}
        except Exception as e:
            logger.error(
                "[GitSyncService] Bootstrap: post-clone failure (%s); "
                "keeping the cloned bare repo", e,
            )
            return {"success": False, "method": "failed", "error": str(e)}

    async def _bootstrap_existing_result(
        self,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        repair_result = await self._ensure_skills_subtree_ready()
        result["success"] = repair_result["success"]
        result["method"] = repair_result["method"]
        if not repair_result["success"]:
            result["error"] = repair_result.get("error")
        result["subtree"] = repair_result.get("subtree")
        if result["success"] and self._expert_scanner is not None:
            from agentclaw.community.core.expert_market.git_sync import sync_git_experts
            result["experts"] = await self._run_sync(sync_git_experts, self)
        return result

    async def _ensure_skills_subtree_ready(self) -> dict[str, Any]:
        """Ensure skills working tree exists when the bare repo already exists."""
        skills_subtree = next(
            (
                subtree
                for subtree in self.config.subtrees
                if subtree["name"] == "skills"
            ),
            self.config.subtrees[0],
        )
        target_dir = skills_subtree["target_dir"]
        version_file = target_dir / skills_subtree["version_file"]

        if target_dir.is_dir() and version_file.is_file():
            return {"success": True, "method": "existing"}

        logger.warning(
            "[GitSyncService] Bootstrap: bare repo exists but skills subtree "
            "is missing/incomplete at %s; repairing",
            target_dir,
        )
        fetch_result = await self._git_fetch()
        if not fetch_result["success"]:
            return {
                "success": False,
                "method": "existing_repair_failed",
                "error": (
                    "Git fetch failed while repairing skills subtree: "
                    f"{fetch_result.get('error')}"
                ),
            }

        subtree_result = await self._sync_subtree(skills_subtree)
        if isinstance(subtree_result, dict) and not subtree_result.get("success", False):
            return {
                "success": False,
                "method": "existing_repair_failed",
                "error": (
                    "Skills subtree extraction failed: "
                    f"{subtree_result.get('error')}"
                ),
                "subtree": subtree_result,
            }

        logger.info("[GitSyncService] Bootstrap: repaired skills subtree")
        return {
            "success": True,
            "method": "existing_repaired",
            "subtree": subtree_result,
        }

    async def sync(self) -> dict[str, Any]:
        """Execute full synchronization.

        1. Acquire locks
        2. Git fetch
        3. Sync each subtree
        4. Update database
        5. Refresh cache (atomic overwrite)
        """
        from agentclaw.community.core.skill_center.services.skill_cache import GlobalSyncLock

        result = {
            "success": False,
            "fetch": False,
            "subtrees": {},
            "cache_refreshed": False,
            "error": None
        }

        # 1. Global sync lock (in-process)
        if not GlobalSyncLock.acquire(timeout=60):
            logger.info("[GitSyncService] Sync: GlobalSyncLock held, skipping")
            result["error"] = GLOBAL_SYNC_LOCK_HELD
            return result

        try:
            # 2. Distributed lock (cross-process)
            cache = self._cache_plugin
            # Use same lock key as old code for backward compatibility
            lock_key = f"skill_repo_sync:{_get_env()}"
            lock_value = await self._run_sync(
                functools.partial(cache.acquire_lock, lock_key, ttl=600)
            )

            if not lock_value:
                logger.info("[GitSyncService] Sync: distributed lock held, skipping")
                result["error"] = DISTRIBUTED_LOCK_HELD
                return result

            try:
                # 3. Git fetch
                fetch_result = await self._git_fetch()
                result["fetch"] = fetch_result["success"]

                if not fetch_result["success"]:
                    result["error"] = f"Git fetch failed: {fetch_result.get('error')}"
                    return result

                # 4. Sync subtrees concurrently
                # Skills and agents can sync in parallel
                results = await asyncio.gather(*[
                    self._sync_subtree(subtree)
                    for subtree in self.config.subtrees
                ], return_exceptions=True)

                subtree_failed = False
                skill_renames = {}
                for subtree, res in zip(self.config.subtrees, results, strict=True):
                    if isinstance(res, Exception):
                        logger.error(f"[GitSyncService] Subtree {subtree['name']} failed: {res}")
                        result["subtrees"][subtree["name"]] = {"success": False, "error": str(res)}
                        subtree_failed = True
                    else:
                        result["subtrees"][subtree["name"]] = res
                        if not res.get("success", False):
                            subtree_failed = True
                        if subtree.get("name") == "skills":
                            skill_renames = res.get("renames", {}) or {}

                agents_result = result["subtrees"].get("agents", {})
                if agents_result.get("success"):
                    from agentclaw.community.core.expert_market.git_sync import sync_git_experts
                    result["experts"] = await self._run_sync(sync_git_experts, self, fetch_result.get("fetch_head"))

                if subtree_failed:
                    result["error"] = "Subtree sync failed"
                    return result

                # Canonical sync is one observable operation: even an
                # up-to-date Git tree must re-scan DB facts and rebuild cache.
                # Otherwise a partial prior scan or a lost cache looks like a
                # successful sync and the caller cannot self-heal it.
                db_result = await self._update_database(git_renames=skill_renames)
                result["database"] = db_result
                if int(db_result.get("failed", 0)) > 0:
                    result["error"] = "Database scan failed"
                    return result

                # 6. Refresh cache (atomic overwrite, no invalidation)
                cache_result = await self._refresh_cache_async()
                if not isinstance(cache_result, dict) or not cache_result.get(
                    "cache_refreshed", False
                ):
                    result["error"] = "Market cache refresh failed"
                    return result
                result["cache_refreshed"] = True

                # 7. Upload skills-repo tar.gz to OSS for VM download
                # singlebox/offline 无 OSS → enable_oss_sync=false 时跳过。
                if self.config.enable_oss_sync:
                    await self._run_sync(self._sync_upload_skills_repo_to_oss)

                # 8. Always refresh meta JSON URL so new VMs can download
                # even when the code hasn't changed (presigned URLs expire).
                # singlebox 同样跳过（无 OSS，meta 刷新必失败）。
                if self.config.enable_oss_sync:
                    await self._run_sync(self._sync_refresh_meta_to_oss)

                result["success"] = True

            finally:
                await self._run_sync(
                    functools.partial(cache.release_lock, lock_key, lock_value)
                )

        finally:
            GlobalSyncLock.release()

        # Background OSS sync (non-blocking)
        if result["success"] and self.config.enable_oss_sync:
            asyncio.create_task(self._sync_to_oss_async())

        return result

    async def _sync_existing_local_market(self) -> dict[str, Any]:
        """Seed market DB/cache from an existing local skills-repo.

        singlebox/test do not have the corp repo-url secret. Their source of
        truth is the host-side ``~/aiworkbench/skills-repo`` prepared by the
        local setup flow, so startup must hydrate SQLite from that directory.
        """
        result = {
            "success": False,
            "method": "local_existing",
            "database": None,
            "cache_refreshed": False,
            "error": None,
        }
        if not self.config.skills_target.is_dir():
            result["method"] = "local_missing"
            result["error"] = f"local skills repo not found: {self.config.skills_target}"
            logger.warning("[GitSyncService] %s", result["error"])
            return result

        from agentclaw.community.core.expert_market.git_sync import sync_local_experts
        result["experts"] = await self._run_sync(sync_local_experts, self)
        result["database"] = await self._update_database(git_renames={})
        if int(result["database"].get("failed", 0)) > 0:
            result["error"] = "Database scan failed"
            return result
        cache_result = await self._refresh_cache_async()
        if not isinstance(cache_result, dict) or not cache_result.get(
            "cache_refreshed", False
        ):
            result["error"] = "Market cache refresh failed"
            return result
        result["cache_refreshed"] = True
        result["success"] = True
        return result

    async def _market_db_empty(self) -> bool:
        """市场 skill 表是否为空（用于 singlebox 重启后 SQLite 内存库被清的
        重灌判断）。直接查 DB repo（绕过 list_git_skills 的 cache，cache 可能
        残留旧数据导致误判）。任何异常按"非空"处理 — 宁可漏灌也不异常反复刷。"""
        def _check() -> bool:
            try:
                service = self._skill_service_factory.create(
                    repo_dir=self.config.skills_target,
                    global_repo_dir=self.config.skills_target,
                )
                rows = service._skill_repo.list_skills(bolt_id=None)
                git_rows = [
                    r
                    for r in rows
                    if (r.get("git_path") or "").startswith("git://")
                ]
                return len(git_rows) == 0
            except Exception:
                return False
        return await self._run_sync(_check)

    async def _update_database(self, git_renames: dict[str, str] | None = None) -> dict[str, Any]:
        """Update database from skills repo."""
        return await self._run_sync(self._sync_update_database, git_renames)

    def _sync_update_database(self, git_renames: dict[str, str] | None = None) -> dict[str, Any]:
        """Call existing sync logic from skill_service."""
        service = self._skill_service_factory.create(
            repo_dir=self.config.skills_target,
            global_repo_dir=self.config.skills_target,
        )
        result = service.sync_skills_from_git(git_renames=git_renames or {})
        return result

    async def _refresh_cache_async(self):
        """Refresh market cache atomically (overwrite, not invalidate)."""
        return await self._run_sync(self._sync_refresh_cache)

    def _sync_refresh_cache(self):
        """Build new cache data and overwrite (no invalidate window)."""
        service = self._skill_service_factory.create(
            repo_dir=self.config.skills_target,
            global_repo_dir=self.config.skills_target,
        )

        result = service._refresh_market_cache()
        logger.info("[GitSyncService] Cache refreshed: %s", result)
        return result

    # ==========================================================================
    # Archive - Nightly zip creation
    # ==========================================================================

    async def archive_if_needed(self) -> dict[str, Any] | None:
        """Create zip archive if current one is too old."""
        # Check age
        if not await self._should_archive():
            return None

        try:
            cache = self._cache_plugin
            lock_key = f"create_aiworkbench_zip:{_get_env()}"
            lock_value = await self._run_sync(
                functools.partial(cache.acquire_lock, lock_key, ttl=1800)
            )

            if not lock_value:
                return {"skipped": True, "reason": "lock held"}

            try:
                if await self._should_archive():
                    await self._create_and_upload_zip()
                    return {"success": True, "zip_created": True}
                else:
                    return {"skipped": True, "reason": "another service updated"}
            finally:
                await self._run_sync(
                    functools.partial(cache.release_lock, lock_key, lock_value)
                )

        except Exception as e:
            logger.error(f"[GitSyncService] Archive failed: {e}")
            return {"success": False, "error": str(e)}

    async def _should_archive(self) -> bool:
        """Check if zip needs update."""
        return await self._run_sync(self._sync_should_archive)

    def _sync_should_archive(self) -> bool:
        zip_path = self.config.archive_zip
        if not zip_path.exists():
            return True

        # Check if older than max age
        mtime = datetime.fromtimestamp(zip_path.stat().st_mtime)
        age = datetime.now() - mtime
        return age > timedelta(hours=self.config.archive_max_age_hours)

    # ==========================================================================
    # Utility
    # ==========================================================================

    async def start_periodic_sync(self):
        """Start periodic sync task."""
        if self._started:
            return

        self._started = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info(f"[GitSyncService] Periodic sync started ({self.config.sync_interval_minutes}min interval)")

    async def stop_periodic_sync(self):
        """Stop periodic sync task."""
        if not self._started:
            return

        self._started = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        self._executor.shutdown(wait=False)
        logger.info("[GitSyncService] Periodic sync stopped")

    async def _sync_loop(self):
        """Main sync loop with jitter."""
        while self._started:
            jitter = random.randint(0, self.config.sync_jitter_seconds)
            delay = self.config.sync_interval_minutes * 60 + jitter
            logger.debug(
                f"[GitSyncService] Waiting {delay}s before next periodic sync..."
            )
            await asyncio.sleep(delay)

            try:
                result = await self.sync()
                logger.info(f"[GitSyncService] Periodic sync result: {result}")

            except Exception as e:
                logger.error(f"[GitSyncService] Periodic sync error: {e}")


def _rewrite_presigned_url_to_office(meta_url: str, office_endpoint: str) -> str:
    """Rewrite an OSS presigned URL to ``office_endpoint`` when configured.

    ``office_endpoint`` is deployment config (``git_sync.office_oss_endpoint`` in
    the yaml, on ``GitSyncConfig.office_oss_endpoint``). Keeps the path and query
    parameters (signature, expires, etc.) intact. Returns the URL unchanged when
    no endpoint is configured.
    """
    if not office_endpoint:
        return meta_url
    parsed = urlparse(meta_url)
    return urlunparse((
        "https", office_endpoint, parsed.path,
        parsed.params, parsed.query, parsed.fragment,
    ))


def _get_env() -> str:
    """Get current environment."""
    from agentclaw.community.utils.env_utils import get_current_env
    return get_current_env() or "dev"

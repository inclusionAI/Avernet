"""SkillService — 统一的技能管理服务

Migrated from: services/openclawserver/server/services/skill_service.py
Replaces: SkillService class (lines 535-2625)

整合所有技能相关功能：
- 技能文件解析 (SKILL.md)
- 技能激活/停用 (软链接管理)
- 市场技能浏览 (仓库扫描)
- Git 仓库同步
- 数据库 CRUD (原 skill_metadata_service)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.services.git_sync import GitSyncService

from agentclaw.community.core.access.admin_scopes import skill_admin
from agentclaw.community.core.skill_center.errors import (
    SkillDeleteConsistencyError,
    SkillReferencedBySkillSetError,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillCategoryRepository,
    SkillRepository,
)
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.core.skill_center.services.skill_parser import SkillInfo, SkillParser, SkillTreeNode
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin

logger = get_logger()

# --------------------------------------------------------------------------
# Default path constants — ARCA container fallback only. 业务 caller 已经走
# _get_bot_paths(path_factory, ...) 按 bot 隔离;这几个常量只兜底 admin /
# migration 场景,历史上这条链路只在 ARCA 容器内跑。
# --------------------------------------------------------------------------
SKILLS_DIR = Path("/home/admin/.openclaw/workspace/skills")
SKILLS_REPO_DIR = SKILLS_DIR / "skills-repo"
SKILLS_LOCAL_DIR = SKILLS_DIR / "skills-local"
# The teclaw minimal-logical local-skill dir name. Same value as the canonical
# ``config_compose.teclaw_paths.LOCAL_SKILLS_DIRNAME``; kept as a local literal so
# core.skill_center doesn't import config_compose (which depends on skill_center).
_LOCAL_SKILLS_DIRNAME = "skills-local"


def _get_default_global_repo_dir() -> Path:
    """Get global skills repo dir — lazy import from config if available."""
    try:
        from agentclaw.community.core.workspace.path_factory import get_global_skills_repo_dir
        return get_global_skills_repo_dir()
    except Exception:
        return SKILLS_REPO_DIR


class SkillService:
    """统一的技能管理服务 - 包含文件系统操作和数据库CRUD"""

    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_repo_sync: SkillRepoSyncPlugin,
        category_repo: SkillCategoryRepository,
        market_cache: MarketCache,
        device_fs_factory,
        git_sync_service_factory: Callable[[], "GitSyncService"],
        active_dir: Path | None = None,
        repo_dir: Path | None = None,
        local_dir: Path | None = None,
        global_repo_dir: Path | None = None,
        local_skill_path_adapter: "Callable[[str], str] | None" = None,
        local_skill_locator_adapter: "Callable[[str], str] | None" = None,
        runtime_uses_pool_paths: bool = False,
        device_owner_id: str | None = None,
    ):
        """
        Args:
            skill_repo: ``SkillRepository`` plugin (required, supplied by
                ``SkillServiceFactory``).
            skill_repo_sync: ``SkillRepoSyncPlugin`` (required, supplied by
                ``SkillServiceFactory``).
            category_repo: ``SkillCategoryRepository`` plugin (required).
            active_dir: 激活技能目录（软链接目录），默认使用 SKILLS_DIR
            repo_dir: 技能仓库目录（用户视角），默认使用 SKILLS_REPO_DIR
            local_dir: 本地技能目录，默认使用 SKILLS_LOCAL_DIR
            global_repo_dir: 全局共享的技能仓库目录（云端模式使用），默认使用 GLOBAL_SKILLS_REPO_DIR
            device_fs_factory: Callable[[bot_id, user_id], DeviceFileSystem]
                Factory function to obtain a device filesystem for a given bot.
                Defaults to ``DeviceFilesystemDispatcher.for_bot`` from the DI injector.
        """
        self._skill_repo = skill_repo
        self.skill_repo_sync = skill_repo_sync
        self._category_repo = category_repo

        self.active_dir = active_dir or SKILLS_DIR
        self.repo_dir = repo_dir or SKILLS_REPO_DIR
        self.local_dir = local_dir or SKILLS_LOCAL_DIR

        # Use global_repo_dir from config (bolt_shared/skills-repo)
        self.global_repo_dir = global_repo_dir or _get_default_global_repo_dir()
        logger.info(f"[SkillService] Global repo dir: {self.global_repo_dir}")

        # Device filesystem factory — supplied per-request by
        # SkillServiceFactory.create() (uses DeviceFilesystemDispatcher.for_bot).
        self._device_fs_factory = device_fs_factory
        self._device_owner_id = device_owner_id

        # Adapter applied to a local-skill path right before it is handed to the
        # device filesystem. Identity for arca/baas/local (they pass a host path
        # the engine's _convert_path strips); for teclaw it expands the minimal
        # ``skills-local/...`` logical path to the workspace namespace
        # (``workspace/skills-local/...``) the teclaw mapper accepts. The DB
        # ``git_path`` always keeps the un-adapted logical/host path.
        self._local_skill_path_adapter: "Callable[[str], str]" = (
            local_skill_path_adapter or (lambda p: p)
        )
        # Adapter applied only when persisting a local:// DB locator. It is
        # deliberately separate from the device-I/O adapter above: Teclaw
        # expands its logical locator for container I/O but must keep the
        # minimal logical value in DB. Pool-active file engines set both
        # adapters to the canonical Pool resolver.
        self._local_skill_locator_adapter: "Callable[[str], str]" = (
            local_skill_locator_adapter or (lambda p: p)
        )
        # Public request-scope policy consumed by the HTTP adapter. Legacy
        # runtimes historically tolerate an unavailable device-sync endpoint;
        # once Pool owns I/O, reporting CRUD success without committing the
        # runtime mapping would leave DB/filesystem/runtime inconsistent.
        self.runtime_uses_pool_paths = runtime_uses_pool_paths

        # Lazy GitSyncService lookup — eager injection would close the cycle
        # GitSyncService → SkillServiceFactory → SkillService → GitSyncService.
        self._git_sync_service_factory = git_sync_service_factory

        # 检测是否使用全局共享仓库（云端模式）
        # 如果显式传入了 repo_dir，就不使用全局仓库
        if repo_dir is not None:
            self.use_global_repo = False
        else:
            self.use_global_repo = self.repo_dir != SKILLS_REPO_DIR and self.global_repo_dir != SKILLS_REPO_DIR

        logger.info(f"[SkillService] Initialized: active_dir={self.active_dir}, repo_dir={self.repo_dir}, "
                   f"local_dir={self.local_dir}, global_repo_dir={self.global_repo_dir}, use_global_repo={self.use_global_repo}")

        # 确保目录存在
        self._ensure_directories()

        # 全局市场缓存（跨请求共享，支持分布式缓存）— 由 DI 注入
        self._market_cache = market_cache

    def _resolve_category_path(self, git_path: str) -> str | None:
        """根据 git_path 从 ac_skill_category 表匹配最长前缀的类目 path。

        git_path 格式: git://business/aml/complaint
        匹配逻辑: 去掉 git:// 后做最长前缀匹配
        """
        if not git_path or not git_path.startswith("git://"):
            return None

        relative = git_path[6:]  # "business/aml/complaint"
        categories = self._category_repo.list_active()

        best: str | None = None
        for cat in categories:
            cat_prefix = cat["path"].lstrip("/")  # "business/aml/"
            if relative.startswith(cat_prefix):
                if best is None or len(cat["path"]) > len(best):
                    best = cat["path"]
        return best

    def _sync_categories_batch(self, git_paths: list[str]) -> dict[str, str]:
        """批量同步类目树（增量版本）

        依赖：必须在锁保护下调用（由 MarketSyncService 的分布式锁保证）

        Args:
            git_paths: Git 路径列表，如 ["git://business/aml/skill1", ...]

        Returns:
            Dict[git_path, category_path] - 每个技能对应的类目路径
        """
        repo = self._category_repo
        if repo is None:
            logger.warning("[_sync_categories_batch] category_repo not available")
            return {}

        # 1. 收集所有唯一的类目路径
        category_nodes: dict[str, dict] = {}

        for git_path in git_paths:
            if not git_path or not git_path.startswith("git://"):
                continue

            relative = git_path[6:]
            parts = relative.split("/")

            if len(parts) < 2:
                continue

            # 提取类目路径（不包含最后的技能名）
            for i in range(len(parts) - 1):
                path = "/" + "/".join(parts[: i + 1]) + "/"
                code = parts[i]

                if path not in category_nodes:
                    # 一级类目：parent_code='ROOT', level=1
                    # 二级及以下：parent_code=上一级 code, level=i+1
                    parent_code = "ROOT" if i == 0 else parts[i - 1]
                    level = i + 1

                    category_nodes[path] = {
                        "code": code,
                        "parent_code": parent_code,
                        "level": level,
                        "name": code,
                        "is_active": 1,
                        "status": 1,
                        "sort_order": 0,
                    }

        if not category_nodes:
            return {}

        # 2. 批量查询已存在的类目
        existing_paths = set()
        for path in category_nodes.keys():
            existing = repo.get_by_path(path)
            if existing:
                existing_paths.add(path)

        # 3. 批量创建缺失的类目
        created_count = 0
        for path, data in category_nodes.items():
            if path in existing_paths:
                continue

            repo.create(
                code=data["code"],
                name=data["name"],
                parent_code=data["parent_code"],
                path=path,
                level=data["level"],
                sort_order=data["sort_order"],
            )
            created_count += 1

        logger.info(f"[_sync_categories_batch] Sync completed: created={created_count}")

        # 4. 返回 {git_path: category_path}
        result = {}
        for git_path in git_paths:
            relative = git_path[6:]
            parts = relative.split("/")
            if len(parts) >= 2:
                result[git_path] = "/" + "/".join(parts[:-1]) + "/"

        return result

    def _ensure_directories(self):
        """确保所需目录存在

        注意：repo_dir 不再自动创建，因为在 cloud mode 下它应该是软链接，
        由 get_bot_skills_repo_dir() 方法负责创建。local_dir 已经移动到 skills/ 下面，
        也不需要在这里创建。
        """
        try:
            logger.info("[SkillService._ensure_directories] Ensuring directories exist")
            self.active_dir.mkdir(parents=True, exist_ok=True)
            # repo_dir: 不再创建，cloud mode 下由软链接管理
            # local_dir: 已移动到 skills/skills-local，由 get_bot_skills_local_dir 管理
            logger.info(f"[SkillService._ensure_directories] Directories ensured: active_dir={self.active_dir.exists()}")
        except Exception as e:
            logger.error(f"[SkillService._ensure_directories] Warning: {e}")

    # ========================================================================
    # 路径转换工具方法
    # ========================================================================

    @staticmethod
    def get_link_name(relative_path: str) -> str:
        """将相对路径转换为链接名称: infra/demo/skill -> infra_demo_skill"""
        return relative_path.strip('/').replace('/', '_')

    @staticmethod
    def get_relative_path_from_link_name(link_name: str) -> str:
        """将链接名称转换回相对路径 (尽力而为)"""
        return link_name.replace('_', '/')

    def parse_skill_path(self, skill_path: str) -> tuple[str, Path]:
        """
        解析 skill_path，返回 (协议类型, 实际路径)

        Args:
            skill_path: 格式为 git://path 或 local://name

        Returns:
            Tuple of (protocol, actual_path)

        Raises:
            ValueError: 如果格式不正确
        """
        if not skill_path:
            raise ValueError("skill_path cannot be empty")

        if skill_path.startswith("git://"):
            path = skill_path[6:]  # 去掉 git:// 前缀
            full_path = self.repo_dir / path
            # 如果路径不存在且是短路径，尝试查找完整路径
            if not full_path.exists() and '/' not in path:
                found_path = self._find_skill_in_repo(path)
                if found_path:
                    return ("git", found_path)
            return ("git", full_path)
        elif skill_path.startswith("local://"):
            path = skill_path[8:]  # 去掉 local:// 前缀
            # 两种合法形态:
            #   1) 绝对主机路径 (arca/baas 走 NAS 挂载):
            #      local:///aidesktop/.../skills-local/<name>
            #   2) teclaw 最小逻辑路径 (engine 拥有文件):
            #      local://skills-local/<name>
            #      —— device-fs seam 由 to_local_skill_engine_path 展开到
            #      workspace/skills-local/...，DB 不存主机路径。
            is_absolute = path.startswith('/')
            is_teclaw_logical = (
                path == _LOCAL_SKILLS_DIRNAME or path.startswith(_LOCAL_SKILLS_DIRNAME + "/")
            )
            if not (is_absolute or is_teclaw_logical):
                raise ValueError(
                    f"local:// path must be an absolute host path or a "
                    f"'{_LOCAL_SKILLS_DIRNAME}/...' logical path: {skill_path}"
                )
            return ("local", Path(path))
        else:
            # 兼容旧格式（没有前缀），假设为 git 路径
            return ("git", self.repo_dir / skill_path)

    def _find_skill_in_repo(self, skill_name: str) -> Path | None:
        """在仓库中查找技能的完整路径"""
        if not self.repo_dir.exists():
            return None
        for skill_file in self.repo_dir.rglob("SKILL.md"):
            if skill_file.parent.name == skill_name:
                return skill_file.parent
        return None

    def _resolve_symlink_for_management(self, link_path: Path) -> Path:
        """
        Resolve an activation symlink to a path that exists on the management host.

        On the management host (NAS direct access):
          active_dir/skills-repo/ is an empty mount-point placeholder.
          The actual skill content lives in repo_dir (active_dir/../skills-repo/).
          This method translates active_dir/skills-repo/X  →  repo_dir/X.

        On the runtime device (NFS mounts active):
          active_dir/skills-repo/ is filled by the bolt_shared NFS submount.
          Symlinks resolve directly — this method is not called on device.
        """
        if not link_path.is_symlink():
            return link_path

        target = link_path.readlink()
        # Already resolvable (e.g. local dev, or device runtime)
        resolved = link_path.resolve()
        if resolved.exists():
            return resolved

        # skills-repo: active_dir/skills-repo/X → translate to repo_dir/X
        # (active_dir/skills-repo/ is empty on management host)
        # Handle both relative target (e.g. "skills-repo/infra/demo") and
        # absolute target (e.g. "/path/to/active/skills-repo/infra/demo")
        target_str = str(target)
        if target_str.startswith("skills-repo/"):
            # Relative symlink pointing into skills-repo
            remainder = target_str[len("skills-repo/"):]
            if self.repo_dir and self.repo_dir.exists():
                translated = (self.repo_dir / remainder).resolve()
                if translated.exists():
                    return translated
        else:
            skills_repo_in_active = self.active_dir / "skills-repo"
            try:
                relative = target.relative_to(skills_repo_in_active)
                if self.repo_dir and self.repo_dir.exists():
                    translated = (self.repo_dir / relative).resolve()
                    if translated.exists():
                        return translated
            except ValueError:
                pass

        # skills-local: ./skills-local/X → translate to local_dir/X
        # (skills-local is under skills/ directory)
        if str(target).startswith("./skills-local/"):
            try:
                relative_path = str(target)[len("./skills-local/"):]
                if self.local_dir and self.local_dir.exists():
                    translated = (self.local_dir / relative_path).resolve()
                    if translated.exists():
                        return translated
            except (ValueError, IndexError):
                pass

        return resolved

    # ========================================================================
    # 技能查询 - 已激活技能 (文件系统)
    # ========================================================================

    # Reserved names that should not be treated as skills or cleaned during deactivation
    # Includes directories (skills-repo, skills-local) and config files
    RESERVED_SKILL_NAMES = frozenset([
        "skills-repo",      # 技能仓库目录
        "skills-local",     # 本地上传技能目录
        ".current_skill_set",  # 当前技能集标记文件
        "skill_sets.json",  # 技能集配置文件
    ])

    # Backward compatible alias
    RESERVED_SKILL_DIR_NAMES = RESERVED_SKILL_NAMES

    def get_active_skills(self) -> list[SkillInfo]:
        """获取所有已激活的技能 (扫描软链接目录)"""
        skills = []

        if not self.active_dir.exists():
            return skills

        for item in self.active_dir.iterdir():
            # Only process symlinks (skill activations are symlinks)
            # Config files and other non-symlink items are automatically skipped
            if not item.is_symlink():
                continue

            # Skip reserved directory names (skills-repo, skills-local)
            # These are storage directories/symlinks, not individual skills
            if item.name in self.RESERVED_SKILL_NAMES:
                logger.debug(f"[get_active_skills] Skipping reserved directory: {item.name}")
                continue

            # Translate device-side absolute path to management-accessible path
            source_path = self._resolve_symlink_for_management(item)

            skill_info = SkillParser.parse(source_path)

            if not skill_info:
                continue

            skill = SkillInfo(
                id=item.name,
                name=skill_info.get("name", item.name),
                description=skill_info.get("description", ""),
                version=skill_info.get("version", "1.0.0"),
                category=skill_info.get("category", "general"),
                icon=self._get_icon_for_category(skill_info.get("category", "general")),
                path=str(item),
                source_path=str(source_path),
                is_active=True,
                is_installed=True,
                capabilities=skill_info.get("capabilities", []),
                author=skill_info.get("author", ""),
                created_at=skill_info.get("created_at", ""),
                updated_at=skill_info.get("updated_at", ""),
            )
            skills.append(skill)

        skills.sort(key=lambda s: s.name)
        return skills

    async def get_active_skills_from_device(
        self, *, bot_id: str, owner_id: str
    ) -> list[SkillInfo]:
        """Read active Skill entries from a Bot's live device filesystem.

        Desktop active roots are local to the user's device and therefore cannot
        be observed through ``Path.iterdir()`` on the Backend host.  The runtime
        entry itself remains authoritative: list its direct children, then read
        ``SKILL.md`` through each entry so dangling links and unrelated folders
        are not reported as active Skills.

        Device I/O errors deliberately propagate.  Returning an empty list for
        an unavailable runtime would make a transport failure indistinguishable
        from a Bot with no active Skills.
        """
        device_fs = self._device_fs_factory(bot_id, owner_id)
        entries = await device_fs.list_dir(str(self.active_dir), recursive=False)
        if entries is None:
            return []

        skills: list[SkillInfo] = []
        for entry in entries:
            name = entry.get("name")
            if (
                not isinstance(name, str)
                or not name
                or name in {".", ".."}
                or Path(name).name != name
                or name in self.RESERVED_SKILL_NAMES
            ):
                continue

            active_path = self.active_dir / name
            content = None
            skill_file = active_path / "SKILL.md"
            if await device_fs.exists(str(skill_file)):
                content = await device_fs.read_file(str(skill_file))
            else:
                readme_file = active_path / "README.md"
                if await device_fs.exists(str(readme_file)):
                    content = await device_fs.read_file(str(readme_file))
            if content is None:
                continue

            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("gbk", errors="replace")
            skill_info = SkillParser.parse_content(text)
            if not skill_info:
                continue

            source_path = str(active_path)
            skill_record = self.get_skill_by_link_name(name, bolt_id=bot_id)
            locator = skill_record.get("git_path") if skill_record else None
            if locator and locator.startswith(("git://", "local://")):
                try:
                    _, resolved_source = self.parse_skill_path(locator)
                    source_path = str(resolved_source)
                except ValueError:
                    logger.warning(
                        "[get_active_skills_from_device] Invalid locator for %s: %s",
                        name,
                        locator,
                    )

            skills.append(
                SkillInfo(
                    id=name,
                    name=skill_info.get("name") or name,
                    description=skill_info.get("description", ""),
                    version=skill_info.get("version", "1.0.0"),
                    category=skill_info.get("category", "general"),
                    icon=self._get_icon_for_category(
                        skill_info.get("category", "general")
                    ),
                    path=str(active_path),
                    source_path=source_path,
                    is_active=True,
                    is_installed=True,
                    capabilities=skill_info.get("capabilities", []),
                    author=skill_info.get("author", ""),
                    created_at=skill_info.get("created_at", ""),
                    updated_at=skill_info.get("updated_at", ""),
                )
            )

        skills.sort(key=lambda skill: skill.name)
        return skills

    def get_active_skill(self, skill_id: str) -> SkillInfo | None:
        """获取单个已激活技能"""
        active_path = self.active_dir / skill_id

        # Use is_symlink() to check that the activation entry exists (not its target).
        # _resolve_symlink_for_management handles both new relative symlinks and
        # legacy absolute-path symlinks for backward compatibility.
        if not active_path.is_symlink():
            return None

        # Translate device-side absolute path to management-accessible path
        source_path = self._resolve_symlink_for_management(active_path)
        skill_info = SkillParser.parse(source_path)

        if not skill_info:
            return None

        return SkillInfo(
            id=skill_id,
            name=skill_info.get("name", skill_id),
            description=skill_info.get("description", ""),
            version=skill_info.get("version", "1.0.0"),
            category=skill_info.get("category", "general"),
            icon=self._get_icon_for_category(skill_info.get("category", "general")),
            path=str(active_path),
            source_path=str(source_path),
            is_active=True,
            is_installed=True,
            capabilities=skill_info.get("capabilities", []),
            author=skill_info.get("author", ""),
            created_at=skill_info.get("created_at", ""),
            updated_at=skill_info.get("updated_at", ""),
        )

    # ========================================================================
    # 技能激活/停用 (文件系统)
    # ========================================================================

    async def activate_skill(self, skill_path: str, user_id: str | None = None, bolt_id: str | None = None) -> bool:
        """
        激活单个技能 (创建软链接)

        Args:
            skill_path: 技能路径，格式为 git://path 或 local://name
            user_id: 用户 ID（用于 device_fs 路由）
            bolt_id: Bolt ID（用于 device_fs 路由）

        Note:
            Pool-owned local skill 必须先通过 DeviceFileSystem 验证源路径
            存在，避免向运行时发布 dangling mapping。Legacy 保留历史
            best-effort 行为；git skill 继续由运行时 repo mount 解析，不在
            Backend 管理视图预检源路径。
        """
        logger.info(f"[SkillService.activate_skill] Start: skill_path={skill_path}")

        try:
            protocol, source = self.parse_skill_path(skill_path)
        except ValueError as e:
            logger.error(f"[SkillService.activate_skill] Invalid skill_path: {e}")
            return False

        device_fs = self._device_fs_factory(bolt_id, user_id)
        if protocol == "local" and self.runtime_uses_pool_paths:
            source = Path(self._local_skill_path_adapter(str(source)))
            try:
                source_exists = await device_fs.exists(str(source))
            except Exception as e:
                logger.warning(
                    "[SkillService.activate_skill] Failed to verify local "
                    "source %s: %s",
                    source,
                    e,
                    exc_info=True,
                )
                return False
            if not source_exists:
                logger.error(
                    "[SkillService.activate_skill] Refusing to activate "
                    "missing local source: %s",
                    source,
                )
                return False

        # 生成链接名称 —— 软链名取尾名 patent-quality-audit，与 local 分支
        # (Path(path).name) 及 get_symlink_mappings (split('/')[-1]) 对齐。
        # 注意：DB link_name 字段仍用全路径下划线格式（get_link_name），二者口径不同。
        if protocol == "git":
            relative_path = skill_path[6:]  # git://path -> path
            link_name = relative_path.strip("/").split("/")[-1]
        else:
            # local:///aidesktop/.../skills-local/skill-name -> skill-name
            path = skill_path[8:]  # 去掉 local:// 前缀
            link_name = Path(path).name  # 提取技能名称（路径最后一部分）

        target_link = self.active_dir / link_name

        # Protect reserved names (directories and config files) - prevent overwriting
        if link_name in self.RESERVED_SKILL_NAMES:
            logger.error(f"[SkillService.activate_skill] Cannot activate skill with reserved name: {link_name}")
            return False

        # Calculate path for skill activation
        # Use relative paths so symlinks resolve correctly on BOTH
        # the management host (NAS direct access) and the runtime device (NFS mount).
        #
        # On device, skills-repo and skills-local are NFS-mounted INSIDE skills/:
        #   /home/admin/.openclaw/skills/
        #   ├── skills-repo/        ← NFS submount of bolt_shared/skills-repo (RO)
        #   ├── skills-local/       ← NFS mount of user's skills-local (RW)
        #   └── {link_name}  →  skills-repo/path/to/skill  or  skills-local/skill-name
        #
        # On management host (NAS), skills/skills-repo/ and skills/skills-local/ are
        # the actual directories. _resolve_symlink_for_management() handles translation.
        #
        # source.relative_to() is pure path arithmetic — no filesystem access.
        try:
            if protocol == "git":
                source_relative = Path("skills-repo") / source.relative_to(self.repo_dir)
            else:
                # Local skills: relative path from skills/ to skills/skills-local/
                source_relative = Path("./skills-local") / source.relative_to(self.local_dir)
            logger.info(f"[SkillService.activate_skill] Creating symlink: {target_link} -> {source_relative}")
        except ValueError:
            # Fallback to absolute path if relative calculation fails
            source_relative = source.resolve()
            logger.debug(f"[SkillService.activate_skill] Using absolute path (relative calculation failed): {target_link} -> {source_relative}")

        if (
            not self.runtime_uses_pool_paths
            and (target_link.exists() or target_link.is_symlink())
        ):
            # Phase 4: engine-view path — 让 engine 在 VM 内删，不要宿主机 shutil.rmtree
            success = await self._delete_active_entry(device_fs, target_link)
            if not success:
                logger.error(
                    f"[SkillService.activate_skill] Failed to remove existing link at {target_link}"
                )
                return False

        # Pool bindpath validates every source before replacing targets. Keep an
        # existing runtime link in place until that single authoritative publish
        # succeeds; eagerly deleting it here would create a gap and would violate
        # fail-before-mutation when the requested mapping conflicts with an active
        # SkillSet mapping.

        # R2 修复: 不再 pathlib 本地写软链。
        # 软链建立由 device_sync (调 adapter bindpath) 单方面负责,跟线上 Arca 行为对齐。
        # 之前的 target_link.symlink_to(source_relative) 会跟 device_sync 形成双写,
        # 且在 adapter 不可达时留下不一致的本地软链 (TC-CAP-C016 残留根因之一)。
        logger.info(
            f"[SkillService.activate_skill] Success: marked skill active "
            f"(symlink will be created by device_sync bindpath): {target_link}"
        )
        return True

    async def deactivate_skill(self, skill_id: str, *, bolt_id: str | None = None, user_id: str | None = None) -> bool:
        """停用单个技能（删除软链接、目录或文件）

        会删除 skills 目录下的技能内容，但保护以下保留项目：
        - skills-repo: 技能仓库目录
        - skills-local: 本地上传技能目录
        - .current_skill_set: 当前技能集标记文件
        - skill_sets.json: 技能集配置文件
        """
        logger.info(f"[SkillService.deactivate_skill] Start: skill_id={skill_id}")

        # Protect reserved names (directories and config files) from being deactivated
        if skill_id in self.RESERVED_SKILL_NAMES:
            logger.warning(f"[SkillService.deactivate_skill] Cannot deactivate reserved item: {skill_id}")
            return False

        target_path = self.active_dir / skill_id
        logger.info(f"[SkillService.deactivate_skill] target_path={target_path}, exists={target_path.exists()}, is_symlink={target_path.is_symlink()}")

        # 尝试 link_name 格式转换（兼容两种格式）
        if not target_path.exists() and not target_path.is_symlink():
            link_name = self.get_link_name(skill_id)
            target_path = self.active_dir / link_name
            logger.info(f"[SkillService.deactivate_skill] Trying link name: {link_name}, new target: {target_path}")

        # 检查是否存在（文件、目录、或断开的软链接）
        if not target_path.exists() and not target_path.is_symlink():
            # 幂等成功：技能已不存在，无需停用
            logger.debug(f"[SkillService.deactivate_skill] Skill not found (already deactivated): {skill_id}")
            return False

        # Phase 4: engine-view path — 让 engine 在 VM 内删
        device_fs = self._device_fs_factory(bolt_id, user_id)
        success = await self._delete_active_entry(device_fs, target_path)
        if success:
            logger.info(f"[SkillService.deactivate_skill] Success: removed {skill_id}")
            return True
        else:
            logger.warning(f"[SkillService.deactivate_skill] Failed: {skill_id}")
            return False

    async def activate_skills_batch(
        self,
        skill_paths: list[str],
        *,
        user_id: str | None = None,
        bolt_id: str | None = None,
    ) -> dict[str, Any]:
        """批量激活技能。

        Args:
            skill_paths: 技能路径列表，格式为 git://path 或 local://name
            user_id: 用户 ID（透传给 activate_skill 用于 device_fs 路由）
            bolt_id: Bot ID（透传给 activate_skill 用于 device_fs 路由）
        """
        results = {"success": [], "failed": []}

        # Run all activations concurrently — propagate user_id/bolt_id so each
        # activate_skill picks the right DeviceFileSystem plugin (BAAS vs ARCA
        # vs Local). Otherwise inner calls land on LocalDeviceFileSystem
        # fallback regardless of the bot's actual device binding.
        activations = await asyncio.gather(
            *[
                self.activate_skill(skill_path, user_id=user_id, bolt_id=bolt_id)
                for skill_path in skill_paths
            ],
            return_exceptions=True,
        )

        for skill_path, success in zip(skill_paths, activations):
            if isinstance(success, Exception):
                results["failed"].append({
                    "path": skill_path,
                    "error": str(success)
                })
            elif success:
                try:
                    protocol, source = self.parse_skill_path(skill_path)
                    if protocol == "git":
                        relative_path = skill_path[6:]
                        skill_id = relative_path
                        link_name = self.get_link_name(relative_path)
                    else:
                        skill_id = skill_path[8:]
                        link_name = Path(skill_id).name
                    results["success"].append({
                        "id": skill_id,
                        "link_name": link_name,
                        "path": skill_path
                    })
                except ValueError:
                    results["success"].append({
                        "id": skill_path,
                        "link_name": skill_path.replace('/', '_'),
                        "path": skill_path
                    })
            else:
                results["failed"].append({
                    "path": skill_path,
                    "error": "Failed to activate skill"
                })

        return results

    async def deactivate_all_skills(self) -> dict[str, Any]:
        """停用所有技能"""
        results = {"success": [], "failed": []}

        for skill in self.get_active_skills():
            if await self.deactivate_skill(skill.id):
                results["success"].append(skill.id)
            else:
                results["failed"].append(skill.id)

        return results

    # ========================================================================
    # 缓存机制（使用全局缓存）
    # ========================================================================

    def _get_cached(self, cache_key: str) -> Any:
        """获取缓存数据（使用全局缓存键）"""
        return self._market_cache.get(cache_key)

    def _set_cache(self, cache_key: str, data: Any) -> None:
        """设置缓存数据（使用全局缓存键）"""
        self._market_cache.set(cache_key, data)

    def invalidate_market_cache(self) -> None:
        """使市场缓存失效"""
        self._market_cache.invalidate()

    def _build_market_tree_sync(self) -> list[dict[str, Any]]:
        """Build market tree from filesystem (sync, without cache lookup).

        This is used by GitSyncService for atomic cache refresh (overwrite, not invalidate).
        """
        # SkillTreeNode is defined in this file (line 293)
        tree = []
        market_repo_dir = self._get_market_repo_dir()

        if not market_repo_dir.exists():
            logger.warning(f"[_build_market_tree_sync] Market repo dir not found: {market_repo_dir}")
            return tree

        # Build without cache lookups
        skills_index = self._build_skills_index()

        for item in sorted(market_repo_dir.iterdir(), key=lambda x: x.name):
            if item.name.startswith('.') or not item.is_dir():
                continue

            try:
                node = self._build_tree_node_cached(item, skills_index)
                if node:
                    tree.append(node.to_dict())
            except Exception as e:
                logger.warning(f"[_build_market_tree_sync] Failed to build node for {item}: {e}")

        return tree

    def _list_git_skills_sync(self) -> list[dict[str, Any]]:
        """List git skills from database (sync, without cache lookup).

        This is used by GitSyncService for atomic cache refresh.
        """
        all_skills = self._skill_repo.list_skills(user_id=None, bolt_id='default')

        # Filter git:// skills
        git_skills = []
        for skill in all_skills:
            git_path = skill.get('git_path', '')
            if git_path and git_path.startswith('git://'):
                git_skills.append(skill)

        return git_skills

    def _refresh_market_cache(self) -> dict[str, Any]:
        """Refresh market cache with atomic overwrite (no invalidate window).

        This method builds new cache data and overwrites old values atomically,
        preventing cache miss windows during refresh.
        """
        logger.info("[SkillService._refresh_market_cache] Starting atomic cache refresh...")

        result = {"cache_refreshed": True, "tree_items": 0, "skills_count": 0, "method": "atomic_overwrite"}

        try:
            # Build new cache data (without reading from cache)
            tree = self._build_market_tree_sync()
            result["tree_items"] = len(tree)
            logger.info(f"[SkillService._refresh_market_cache] Built tree: {len(tree)} items")

            # 刷新不同排序的缓存
            if hasattr(self._skill_repo, 'list_git_skills_with_order'):
                skills_latest = self._skill_repo.list_git_skills_with_order(orderby='latest')
                skills_hotest = self._skill_repo.list_git_skills_with_order(orderby='hotest')

                # Atomic overwrite (no invalidate)
                self._set_cache('market_tree', tree)
                self._set_cache('market_skills_list_latest', skills_latest)
                self._set_cache('market_skills_list_hotest', skills_hotest)
                self._set_cache('market_skills_list_default', skills_latest)  # 默认使用最新排序

                result["skills_count"] = len(skills_latest)
                logger.info(f"[SkillService._refresh_market_cache] Built skills lists: latest={len(skills_latest)}, hotest={len(skills_hotest)}")
            else:
                # 降级：使用原有逻辑
                skills = self._list_git_skills_sync()
                result["skills_count"] = len(skills)
                self._set_cache('market_tree', tree)
                self._set_cache('market_skills_list_default', skills)
                logger.info(f"[SkillService._refresh_market_cache] Built skills list: {len(skills)} skills")

            logger.info("[SkillService._refresh_market_cache] Cache atomically overwritten")

        except Exception as e:
            logger.error(f"[SkillService._refresh_market_cache] Failed: {e}")
            result["cache_refreshed"] = False
            result["error"] = str(e)

        return result

    # ========================================================================
    # 市场功能 - 浏览仓库中的技能 (文件系统)
    # ========================================================================

    def _get_market_repo_dir(self) -> Path:
        """获取技能市场扫描使用的仓库目录

        始终使用 global_repo_dir（全局共享仓库）。
        个人 repo_dir 已不再更新（历史数据），不再使用。
        """
        if self.global_repo_dir and self.global_repo_dir.exists():
            return self.global_repo_dir
        return self.repo_dir

    def _resolve_sync_scan_target(self) -> Path:
        """Resolve the scan target for sync_skills_from_git.

        Strict-cloud / lenient-local semantics now live on the
        injected ``SkillRepoSyncPlugin`` — the prod impl raises if
        ``GitSyncService.config.skills_target`` is missing (incident
        2026-05-13: refuses NAS fallback to avoid the partial-rsync
        race); the local impl returns the market repo dir fallback
        directly. Mode is invisible to this service (Rule 14).

        See ``plugins/skill_repo_sync.py:SkillRepoSyncPlugin.get_scan_target``
        for the full contract.
        """
        return self.skill_repo_sync.get_scan_target(self._get_market_repo_dir())

    def get_market_tree(self) -> list[dict[str, Any]]:
        """获取技能市场树形结构（带缓存）"""
        # 尝试从缓存获取
        cached = self._get_cached('market_tree')
        if cached is not None:
            logger.info(f"[SkillService.get_market_tree] Cache HIT, returning {len(cached)} items from cache")
            return cached

        logger.info("[SkillService.get_market_tree] Cache MISS, building tree...")
        tree = []
        market_repo_dir = self._get_market_repo_dir()

        if not market_repo_dir.exists():
            logger.warning(f"[SkillService.get_market_tree] Market repo dir not found: {market_repo_dir}")
            return tree

        # 预扫描所有技能并构建索引（用于后续快速查找）
        skills_index = self._build_skills_index()

        for item in sorted(market_repo_dir.iterdir(), key=lambda x: x.name):
            if item.name.startswith('.') or not item.is_dir():
                continue

            node = self._build_tree_node_cached(item, skills_index)
            if node:
                tree.append(node.to_dict())

        # 存入缓存
        self._set_cache('market_tree', tree)
        logger.info(f"[SkillService.get_market_tree] Cache SET, built {len(tree)} items")
        return tree

    def _build_skills_index(self) -> dict[str, dict[str, Any]]:
        """预构建所有技能的索引，避免重复解析文件"""
        cached_index = self._get_cached('skills_flat')
        if cached_index:
            return cached_index

        index = {}
        market_repo_dir = self._get_market_repo_dir()
        if not market_repo_dir.exists():
            return index

        def scan_directory(dir_path: Path, parent_path: str = ""):
            try:
                for item in dir_path.iterdir():
                    if item.name.startswith('.') or not item.is_dir():
                        continue

                    rel_path = str(item.relative_to(market_repo_dir))

                    # 只有包含 SKILL.md 的目录才被认为是技能
                    if SkillParser.has_skill_file(item):
                        # 是技能目录，解析并缓存
                        skill_info = SkillParser.parse(item)
                        if skill_info:
                            index[rel_path] = skill_info
                    else:
                        # 是普通目录，递归扫描
                        scan_directory(item, rel_path)
            except Exception as e:
                logger.warning(f"[SkillService] Error scanning directory {dir_path}: {e}")

        scan_directory(market_repo_dir)

        # 缓存索引
        self._set_cache('skills_flat', index)
        return index

    def _build_tree_node_cached(self, path: Path, skills_index: dict[str, dict[str, Any]]) -> SkillTreeNode | None:
        """使用缓存索引递归构建树节点"""
        if not path.exists():
            return None

        market_repo_dir = self._get_market_repo_dir()
        rel_path = str(path.relative_to(market_repo_dir))

        # 先从索引中查找
        if rel_path in skills_index:
            skill_info = skills_index[rel_path]
            return SkillTreeNode(
                name=skill_info.get("name", path.name),
                path=rel_path,
                type="skill",
                skill_info=skill_info
            )

        # 检查是否是技能目录（不在索引中的情况）- 只有 SKILL.md 才算
        if SkillParser.has_skill_file(path):
            skill_info = SkillParser.parse(path)
            if skill_info:
                # 添加到索引
                skills_index[rel_path] = skill_info
                return SkillTreeNode(
                    name=skill_info.get("name", path.name),
                    path=rel_path,
                    type="skill",
                    skill_info=skill_info
                )

        node = SkillTreeNode(
            name=path.name,
            path=rel_path,
            type="dir"
        )

        try:
            for child in sorted(path.iterdir(), key=lambda x: x.name):
                if child.name.startswith('.') or not child.is_dir():
                    continue
                child_node = self._build_tree_node_cached(child, skills_index)
                if child_node:
                    node.children.append(child_node)
        except Exception as e:
            logger.warning(f"[SkillService] Error reading directory {path}: {e}")

        return node

    def _build_tree_node(self, path: Path) -> SkillTreeNode | None:
        """递归构建树节点（原始方法，保留用于兼容）"""
        if not path.exists():
            return None

        market_repo_dir = self._get_market_repo_dir()

        # 只有包含 SKILL.md 的目录才被认为是技能
        if SkillParser.has_skill_file(path):
            skill_info = SkillParser.parse(path)
            return SkillTreeNode(
                name=skill_info.get("name", path.name) if skill_info else path.name,
                path=str(path.relative_to(market_repo_dir)),
                type="skill",
                skill_info=skill_info
            )

        node = SkillTreeNode(
            name=path.name,
            path=str(path.relative_to(market_repo_dir)),
            type="dir"
        )

        try:
            for child in sorted(path.iterdir(), key=lambda x: x.name):
                if child.name.startswith('.') or not child.is_dir():
                    continue
                child_node = self._build_tree_node(child)
                if child_node:
                    node.children.append(child_node)
        except Exception as e:
            logger.error("[SkillService] Error reading directory %s: %s", path, e)

        return node

    def get_skills_in_path(self, target_path: str = "", enrich_with_db: bool = True, bolt_id: str | None = None) -> list[dict[str, Any]]:
        """获取指定路径下的所有技能 (平铺列表，带缓存)

        Args:
            target_path: 目标路径
            enrich_with_db: 是否关联 DB ID (默认 True)
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        # 如果没有指定路径，使用缓存的全量数据
        if not target_path:
            cached = self._get_cached('skills_list')
            if cached is not None:
                return cached

            # 构建全量技能列表
            skills_index = self._build_skills_index()
            skills = self._convert_index_to_list(skills_index, enrich_with_db=enrich_with_db, bolt_id=bolt_id)

            # 存入缓存
            self._set_cache('skills_list', skills)
            return skills

        # 指定了路径，从索引中过滤
        skills_index = self._build_skills_index()
        prefix = target_path.rstrip('/') + '/'
        filtered_skills = []

        for rel_path, skill_info in skills_index.items():
            if rel_path.startswith(prefix) or rel_path == target_path:
                skill_dict = self._convert_skill_info_to_dict(rel_path, skill_info)
                if enrich_with_db:
                    skill_dict = self._enrich_skill_with_db_id(skill_dict, bolt_id=bolt_id)
                filtered_skills.append(skill_dict)

        return filtered_skills

    def _convert_index_to_list(self, skills_index: dict[str, dict[str, Any]], enrich_with_db: bool = True, bolt_id: str | None = None) -> list[dict[str, Any]]:
        """将技能索引转换为列表格式

        Args:
            skills_index: 技能索引
            enrich_with_db: 是否关联 DB ID
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        skills = []
        for rel_path, skill_info in skills_index.items():
            skill_dict = self._convert_skill_info_to_dict(rel_path, skill_info)
            if enrich_with_db:
                skill_dict = self._enrich_skill_with_db_id(skill_dict, bolt_id=bolt_id)
            skills.append(skill_dict)
        return skills

    def _convert_skill_info_to_dict(self, rel_path: str, skill_info: dict[str, Any]) -> dict[str, Any]:
        """将技能信息转换为API响应格式"""
        # 提取目录名作为ID
        item_name = Path(rel_path).name
        link_name = self.get_link_name(rel_path)
        market_repo_dir = self._get_market_repo_dir()

        return {
            "id": link_name,
            "link_name": link_name,
            "name": skill_info.get("name", item_name),
            "description": skill_info.get("description", ""),
            "version": skill_info.get("version", "1.0.0"),
            "category": skill_info.get("category", "general"),
            "icon": self._get_icon_for_category(skill_info.get("category", "general")),
            "full_path": str(market_repo_dir / rel_path),
        }

    def _enrich_skill_with_db_id(self, market_skill: dict, bolt_id: str | None = None) -> dict:
        """为市场技能关联 DB ID

        从 market_skill 生成 link_name，查询 DB 获取已存在的技能，
        如果找到则使用 DB 中的数字 ID，否则 id 为 None。

        Args:
            market_skill: 市场技能数据
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        link_name = market_skill.get('link_name')
        market_repo_dir = self._get_market_repo_dir()
        if not link_name:
            # 从 full_path 生成 link_name
            full_path = market_skill.get('full_path', '')
            if full_path:
                try:
                    rel_path = Path(full_path).relative_to(market_repo_dir)
                    link_name = self.get_link_name(str(rel_path))
                except ValueError:
                    link_name = None

        if link_name:
            # 尝试从 DB 查询已存在的技能
            db_skill = self._skill_repo.get_by_link_name(link_name, bolt_id=bolt_id)
            if db_skill:
                # 使用 DB 中的数字 ID
                market_skill['id'] = str(db_skill['id'])
            else:
                # 技能不在 DB 中
                market_skill['id'] = None
            market_skill['link_name'] = link_name
        else:
            market_skill['id'] = None

        return market_skill

    def search_skills_in_repo(self, query: str) -> list[dict[str, Any]]:
        """在仓库中搜索技能"""
        results = []
        query_lower = query.lower()

        all_skills = self.get_skills_in_path("")
        for skill in all_skills:
            if (
                query_lower in skill.get("name", "").lower()
                or query_lower in skill.get("description", "").lower()
            ):
                results.append(skill)

        return results

    # ========================================================================
    # Git 仓库同步
    # ========================================================================

    def _get_lock_base_dir(self) -> Path:
        """Get directory for lock files.

        In cloud mode with global shared repo, use the parent dir of global_repo_dir
        (e.g., /aidesktop/aidesktop_{env}/bolt_shared/)
        Otherwise use repo_dir.
        """
        if self.use_global_repo and self.global_repo_dir:
            # Use bolt_shared dir for lock files
            return self.global_repo_dir.parent
        return self.repo_dir

    def sync_repo_with_lock(
        self,
        min_interval: int = 300,
        lock_timeout: int = None
    ) -> dict[str, Any]:
        """带分布式锁的同步技能仓库

        委托给 GitSyncService，已经内置了双锁机制。

        Args:
            min_interval: 最小同步间隔（秒），默认5分钟（用于兼容，实际由 GitSyncService 控制）
            lock_timeout: 锁超时时间（秒）（用于兼容，实际由 GitSyncService 控制）

        Returns:
            Dict with keys:
                - success: bool
                - synced: bool - 是否实际执行了同步
                - message: str
        """
        import asyncio

        local_skills_root = self.skill_repo_sync.get_local_skills_root()
        if isinstance(local_skills_root, Path):
            logger.info(
                "[sync_repo_with_lock] Local skills root detected, "
                "delegating to SkillRepoSyncPlugin"
            )
            result = self.sync_repo()
            result.setdefault("error", None)
            return result

        # Resolve the cycle-broken GitSyncService lazily; see __init__ comment.
        git_sync_service = self._git_sync_service_factory()

        logger.info("[sync_repo_with_lock] Delegating to GitSyncService")

        try:
            # 检查是否有正在运行的事件循环
            try:
                loop = asyncio.get_running_loop()
                # 如果有正在运行的循环，使用 run_coroutine_threadsafe
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor():
                    future = asyncio.run_coroutine_threadsafe(
                        git_sync_service.sync(),
                        loop
                    )
                    result = future.result(timeout=300)
            except RuntimeError:
                # 没有正在运行的事件循环，创建新的
                result = asyncio.run(git_sync_service.sync())

            return {
                "success": result.get("success", False),
                "synced": result.get("success", False),
                # 透传底层 error 键：路由层据此区分"锁被占用"（友好提示）与真实失败（500）。
                # 不透传会导致 sync_market 把 "Distributed lock held" 当成 500 错误抛出。
                "error": result.get("error"),
                "message": "Sync completed" if result.get("success") else result.get("error", "Sync failed"),
                "subtrees": result.get("subtrees", {})
            }
        except Exception as e:
            logger.error(f"[sync_repo_with_lock] Error: {e}")
            return {
                "success": False,
                "synced": False,
                "message": f"Sync error: {str(e)}"
            }

    def get_sync_status(self) -> dict[str, Any]:
        """获取同步状态"""
        import time

        # Use appropriate dir for sync status
        status_base_dir = self._get_lock_base_dir()

        last_sync_file = status_base_dir / ".last_sync"
        last_sync = None
        next_sync_in = 0

        if last_sync_file.exists():
            try:
                last_sync = float(last_sync_file.read_text().strip())
                elapsed = time.time() - last_sync
                if elapsed < 300:  # 5分钟间隔
                    next_sync_in = int(300 - elapsed)
            except (OSError, ValueError):
                pass

        return {
            "last_sync": last_sync,
            "next_sync_in": next_sync_in,
            "can_sync": next_sync_in <= 0,
            "sync_interval": 300
        }

    def sync_repo(self) -> dict[str, Any]:
        """同步技能仓库，委托给 SkillRepoSyncPlugin。"""
        import asyncio

        logger.info("[SkillService.sync_repo] Delegating to SkillRepoSyncPlugin")

        try:
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor():
                    future = asyncio.run_coroutine_threadsafe(
                        self.skill_repo_sync.sync(),
                        loop
                    )
                    result = future.result(timeout=300)
            except RuntimeError:
                result = asyncio.run(self.skill_repo_sync.sync())

            return {
                "success": result.get("success", False),
                "synced": result.get("success", False),
                "fetch": result.get("fetch", False),
                "message": "Sync completed" if result.get("success") else result.get("error", "Sync failed"),
                "subtrees": result.get("subtrees", {}),
            }
        except Exception as e:
            logger.error(f"[SkillService.sync_repo] Error: {e}")
            return {"success": False, "synced": False, "message": f"Sync error: {str(e)}"}

    # ========================================================================
    # README 内容获取
    # ========================================================================

    async def get_skill_readme(
        self,
        skill_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        *,
        device_owner_id: str | None = None,
    ) -> str | None:
        """获取技能的 README/SKILL.md 内容

        通过 skill_id 查询数据库获取 skill 记录，然后使用记录中的 bolt_id 和 git_path
        直接定位文件，不依赖前端传递的 bot_id。``user_id`` 是当前操作者，
        ``device_owner_id``（如传入）仅用于定位 Bot 的设备绑定。
        """
        try:
            logger.info(
                "[get_skill_readme] skill_id=%s, user_id=%s, bolt_id=%s, "
                "device_owner_id=%s",
                skill_id, user_id, bolt_id, device_owner_id,
            )
            # 从数据库获取 skill 信息（优先用 ID 查询，其次用 link_name）
            skill = None
            if skill_id.isdigit():
                try:
                    skill = self._skill_repo.get_by_id(skill_id)
                except Exception as e:
                    logger.warning(f"[get_skill_readme] Failed to query database for skill_id={skill_id}: {e}")
            else:
                # 尝试通过 link_name 查询（优先尝试当前 bot，找不到再全局查）
                try:
                    skill = self.get_skill_by_link_name(skill_id, bolt_id=bolt_id)
                except Exception as e:
                    logger.warning(f"[get_skill_readme] Failed to query database for link_name={skill_id}: {e}")

            # 如果没找到，且 link_name 查询时指定了 bolt_id，尝试全局查找
            if skill is None and not skill_id.isdigit() and bolt_id is not None:
                try:
                    skill = self.get_skill_by_link_name(skill_id, bolt_id=None)
                except Exception as e:
                    logger.warning(f"[get_skill_readme] Failed to query global for link_name={skill_id}: {e}")

            logger.info(f"[get_skill_readme] DB lookup result: skill_id={skill_id}, found={skill is not None}")
            if skill:
                git_path = skill.get('git_path', '')
                db_bolt_id = skill.get('bolt_id')
                bolt_id = db_bolt_id or bolt_id
                skill_metadata_owner_id = skill.get('user_id') or user_id
                device_user_id = device_owner_id or skill_metadata_owner_id
                logger.info(
                    f"[get_skill_readme] DB found, git_path={git_path}, "
                    f"db_bolt_id={db_bolt_id}, effective_bolt_id={bolt_id}, "
                    f"skill_metadata_owner_id={skill_metadata_owner_id}, "
                    f"device_user_id={device_user_id}"
                )

                if git_path.startswith('local://'):
                    # 本地 skill，使用绝对路径
                    local_path_str = git_path[8:]  # 去掉 local:// 前缀
                    local_path = Path(local_path_str)
                    logger.info(f"[get_skill_readme] Looking for local skill: {local_path}")

                    # 通过 DeviceFileSystem 读取，自动适配 local/arca/teclaw
                    device_fs = self._device_fs_factory(bolt_id, device_user_id)
                    # teclaw: skills-local/<name> → workspace/skills-local/<name>;
                    # 非 teclaw: identity（主机路径原样）。
                    skill_base = self._local_skill_path_adapter(str(local_path))

                    # 尝试 SKILL.md
                    content = await device_fs.read_file(f"{skill_base}/SKILL.md")
                    if content:
                        try:
                            return content.decode("utf-8")
                        except UnicodeDecodeError:
                            return content.decode("gbk", errors="replace")
                    # 尝试 README.md
                    content = await device_fs.read_file(f"{skill_base}/README.md")
                    if content:
                        try:
                            return content.decode("utf-8")
                        except UnicodeDecodeError:
                            return content.decode("gbk", errors="replace")
                    logger.warning(f"[get_skill_readme] Skill file not found: {skill_base}")
                    # Local skill 没找到，继续尝试 repo（兜底）
                elif git_path.startswith('git://'):
                    # Git skill，从 repo 查找
                    relative_path = git_path[6:]  # 去掉 git:// 前缀
                    skill_path = self._get_market_repo_dir() / relative_path
                    logger.info(f"[get_skill_readme] git:// path: {skill_path}, exists={skill_path.exists()}")
                    if skill_path.exists():
                        skill_file = SkillParser.find_skill_file(skill_path)
                        if skill_file:
                            try:
                                return skill_file.read_text(encoding="utf-8")
                            except UnicodeDecodeError:
                                return skill_file.read_text(encoding="gbk", errors="replace")
                            except Exception as e:
                                logger.error(f"[SkillService] Error reading repo readme: {e}")
                    logger.info("[get_skill_readme] Falling through to _get_readme_from_repo")
                    return self._get_readme_from_repo(skill_id)
                else:
                    logger.warning(f"[get_skill_readme] Unknown git_path scheme: git_path={git_path}")

            # 数据库无记录，直接从 repo 查找
            logger.warning(f"[get_skill_readme] skill not found, calling _get_readme_from_repo: skill_id={skill_id}")
            return self._get_readme_from_repo(skill_id)
        except Exception as e:
            logger.error(f"[get_skill_readme] Unexpected error: skill_id={skill_id}, error={type(e).__name__}: {e}")
            raise

    def _get_readme_from_repo(self, skill_id: str) -> str | None:
        """从仓库中查找技能的 README"""
        logger.info(f"[_get_readme_from_repo] skill_id={skill_id}")
        if '_' in skill_id and '/' not in skill_id:
            relative_path = self.get_relative_path_from_link_name(skill_id)
        else:
            relative_path = skill_id

        market_repo_dir = self._get_market_repo_dir()
        skill_path = market_repo_dir / relative_path
        logger.info(f"[_get_readme_from_repo] relative_path={relative_path}, skill_path={skill_path}, exists={skill_path.exists()}")
        if not skill_path.exists():
            skill_path = self._find_skill_in_repo(skill_id)
            logger.info(f"[_get_readme_from_repo] _find_skill_in_repo result: {skill_path}")

        if not skill_path:
            return None

        skill_file = SkillParser.find_skill_file(skill_path)
        if skill_file:
            try:
                return skill_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return skill_file.read_text(encoding="gbk", errors="replace")
            except Exception as e:
                logger.error("[SkillService] Error reading repo readme: %s", e)

        return None

    def _find_skill_in_repo(self, skill_name: str) -> Path | None:  # noqa: F811  intentional shadow — see line 188
        """在仓库中递归查找技能目录"""
        market_repo_dir = self._get_market_repo_dir()

        def find_recursive(path: Path) -> Path | None:
            for item in path.iterdir():
                if item.name.startswith('.'):
                    continue
                if item.is_dir():
                    if item.name == skill_name:
                        # 只有包含 SKILL.md 的目录才被认为是技能
                        if SkillParser.has_skill_file(item):
                            return item
                    result = find_recursive(item)
                    if result:
                        return result
            return None

        return find_recursive(market_repo_dir)

    # ========================================================================
    # 数据库操作 - Skill Metadata CRUD (需要 db Session)
    # ========================================================================

    def _require_db(self):
        """检查并返回 db session（已弃用，保留用于兼容性）"""
        # 现在使用 repository 模式，不需要 db session
        return None

    def _parse_skill_md_for_db(self, skill_path: Path) -> dict[str, Any] | None:
        """解析技能文件，返回数据库需要的格式"""
        base_info = SkillParser.parse(skill_path)
        if not base_info:
            return None

        return {
            "name": base_info.get("name", skill_path.name),
            "description": base_info.get("description", ""),
            "version": base_info.get("version", "1.0.0"),
            "category": base_info.get("category", "general"),
            "tags": base_info.get("tags", []),
            "input_schema": "",
            "output_schema": "",
        }

    def _parse_skill_from_git(self, skill_path: str) -> dict[str, Any] | None:
        """从 git 路径解析技能信息（用于获取 config 定义）。

        宿主文件系统读取路径，适用于 git:// 及挂载了 NAS 的 arca/baas local://
        技能。teclaw（engine 拥有文件，backend 无挂载）走异步的
        :meth:`parse_local_skill_config` 经 device_fs 读取，**不**走此方法。
        """
        try:
            protocol, source = self.parse_skill_path(skill_path)
            if not source.exists():
                return None
            return SkillParser.parse(source)
        except Exception as e:
            logger.warning(f"[_parse_skill_from_git] Failed to parse {skill_path}: {e}")
            return None

    async def parse_local_skill_config(
        self, git_path: str, bolt_id: str | None, user_id: str | None
    ) -> dict[str, Any] | None:
        """Parse a local skill's SKILL.md config by reading it through device_fs.

        For teclaw the skill files live only in the bot's container (no backend
        NAS mount), so the host-FS :meth:`_parse_skill_from_git` finds nothing.
        This reads ``SKILL.md`` via the device filesystem at the adapter-expanded
        path (``workspace/skills-local/<name>/SKILL.md``) and parses the same
        ``config`` schema. Returns ``None`` for non-``local://`` paths or when the
        file is missing/unreadable.
        """
        if not git_path.startswith("local://"):
            return None
        try:
            rel = self._local_skill_path_adapter(git_path[len("local://"):])
            device_fs = self._device_fs_factory(bolt_id, user_id)
            content = await device_fs.read_file(f"{rel}/SKILL.md")
            if content is None:
                logger.warning(f"[parse_local_skill_config] SKILL.md not found: {rel}")
                return None
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("gbk", errors="replace")
            return SkillParser.parse_content(text)
        except Exception as e:
            logger.warning(f"[parse_local_skill_config] Failed to parse {git_path}: {e}")
            return None

    # Import models here to avoid circular import at module level
    def _get_skill_model(self):
        from agentclaw.community.core.models import Skill
        return Skill

    # ----- Create -----
    def create_skill(
        self,
        name: str,
        description: str,
        skill_path: str,
        category: str = "general",
        tags: list[str] = None,
        input_schema: str = "",
        output_schema: str = "",
        is_public: bool = False,
        user_id: str | None = None,
        bolt_id: str | None = None
    ):
        """创建技能元数据

        Args:
            name: 技能名称（不能包含下划线）
            description: 技能描述
            skill_path: 技能路径，格式为 git://path 或 local://name
            category: 分类
            tags: 标签列表
            input_schema: 输入 schema
            output_schema: 输出 schema
            is_public: 是否公开
            user_id: 用户 ID
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        if "_" in name:
            raise ValueError("Skill name cannot contain underscore '_'")

        # Protect reserved names (directories and config files)
        if name in self.RESERVED_SKILL_NAMES:
            raise ValueError(f"Skill name '{name}' is reserved and cannot be used")

        # 验证 skill_path 格式
        if not skill_path.startswith(("git://", "local://")):
            raise ValueError("skill_path must start with git:// or local://")

        # 验证对应的技能文件存在
        # 对于 local:// 路径，通过 DeviceFileSystem 验证（自动适配 local/arca）
        if skill_path.startswith("local://"):
            local_path_str = skill_path[8:]
            from pathlib import Path as _Path
            if _Path(local_path_str).exists():
                pass  # local path verified synchronously
            else:
                # For remote devices the file may only exist on the device;
                # upload_skill already wrote the files, so we trust the path.
                logger.debug(f"[create_skill] Local path not found on host, assuming device-side: {local_path_str}")
        elif skill_path.startswith("git://"):
            try:
                protocol, source_path = self.parse_skill_path(skill_path)
            except ValueError as e:
                raise ValueError(f"Invalid skill_path: {e}")

            if not source_path.exists():
                raise ValueError(f"Skill directory not found: {source_path}")

            # Only check for SKILL.md (not README.md)
            if not (source_path / "SKILL.md").exists():
                raise ValueError(f"SKILL.md not found in {source_path}")

        skill_data = {
            'name': name,
            'description': description,
            'git_path': skill_path,
            'category': category,
            'tags': json.dumps(tags or []),
            'input_schema': input_schema,
            'output_schema': output_schema,
            'is_public': is_public,
            'is_builtin': False,
            'user_id': user_id,
            'bolt_id': bolt_id,
            'gmt_created': datetime.utcnow(),
            'gmt_modified': datetime.utcnow()
        }

        return self._skill_repo.create(skill_data)

    def _extract_zip_files(self, zip_content: bytes) -> list[dict[str, Any]]:
        """Extract files from ZIP content.

        Args:
            zip_content: ZIP file content as bytes

        Returns:
            List of file dicts with filename, relative_path, and content
        """
        files = []
        with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zf:
            for name in zf.namelist():
                # Skip directories and hidden files
                if name.endswith('/') or '/.' in name or name.startswith('.'):
                    continue
                content = zf.read(name)
                files.append({
                    'filename': os.path.basename(name),
                    'relative_path': name,
                    'content': content
                })
        return files

    @staticmethod
    def _is_ignored_upload_path(relative_path: str) -> bool:
        parts = relative_path.split("/")
        name = parts[-1]
        return (
            name == ".DS_Store"
            or parts[0] == "__MACOSX"
            or "__pycache__" in parts
            or name.endswith((".pyc", ".pyo"))
        )

    @staticmethod
    def _validate_upload_path(relative_path: str) -> None:
        if not relative_path:
            raise ValueError("Invalid upload path: empty path.")
        if relative_path.startswith("/"):
            raise ValueError("Invalid upload path: absolute path is not allowed.")
        if "\\" in relative_path:
            raise ValueError("Invalid upload path: backslash path separator is not allowed.")
        parts = relative_path.split("/")
        if any(part == "" for part in parts):
            raise ValueError("Invalid upload path: empty path segment is not allowed.")
        if ".." in parts:
            raise ValueError("Invalid upload path: parent directory '..' is not allowed.")

    @staticmethod
    def _has_required_skill_field(content: str, field_name: str) -> bool:
        return re.search(rf"^[ \t]*{re.escape(field_name)}[ \t]*:", content, re.MULTILINE) is not None

    @staticmethod
    def _extract_upload_scalar_field(content: str, field_name: str) -> str:
        match = re.search(rf"^[ \t]*{re.escape(field_name)}[ \t]*:[ \t]*([^\r\n]*)", content, re.MULTILINE)
        if not match:
            return ""
        return match.group(1).strip().strip("\"'")

    def _prepare_upload_plan(
        self,
        uploaded_files: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        candidates: list[dict[str, Any]] = []

        for file_info in uploaded_files:
            raw_path = file_info.get("relative_path") or file_info.get("filename") or ""
            relative_path = str(raw_path)
            if relative_path.endswith("/"):
                continue
            self._validate_upload_path(relative_path)
            if self._is_ignored_upload_path(relative_path):
                continue
            candidates.append({
                "filename": file_info.get("filename", ""),
                "relative_path": relative_path,
                "content": file_info.get("content", b""),
            })

        if not candidates:
            raise ValueError("No files uploaded")

        skill_md_files = [
            file_info for file_info in candidates
            if os.path.basename(file_info["relative_path"]).upper() == "SKILL.MD"
        ]
        if not skill_md_files:
            raise ValueError("SKILL.md is required.")
        if len(skill_md_files) > 1:
            raise ValueError(
                f"Only one skill can be uploaded at a time. Found {len(skill_md_files)} SKILL.md files."
            )

        skill_md_file = skill_md_files[0]
        skill_md_path = skill_md_file["relative_path"]
        skill_root = os.path.dirname(skill_md_path)
        processed_files: list[dict[str, Any]] = []

        for file_info in candidates:
            relative_path = file_info["relative_path"]
            if skill_root:
                root_prefix = f"{skill_root}/"
                if not relative_path.startswith(root_prefix):
                    raise ValueError("Upload contains files outside the skill root directory.")
                relative_path_without_root = relative_path[len(root_prefix):]
            else:
                relative_path_without_root = relative_path

            if not relative_path_without_root:
                continue
            processed_files.append({
                "relative_path": relative_path_without_root,
                "content": file_info["content"],
            })

        raw_bytes = skill_md_file.get("content", b"")
        try:
            content_str = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content_str = raw_bytes.decode("gbk", errors="replace")
        skill_info = SkillParser.parse_content(content_str) if content_str else {}
        if not self._has_required_skill_field(content_str, "name"):
            raise ValueError("SKILL.md must contain required field: name.")
        skill_name = self._extract_upload_scalar_field(content_str, "name")
        if not skill_name:
            raise ValueError("SKILL.md field 'name' cannot be empty.")
        if not self._has_required_skill_field(content_str, "description"):
            raise ValueError("SKILL.md must contain required field: description.")
        if skill_root:
            folder_name = os.path.basename(skill_root)
            if folder_name != skill_name:
                raise ValueError(
                    "Skill folder name must match SKILL.md field 'name'. "
                    f"Folder name: '{folder_name}', SKILL.md name: '{skill_name}'."
                )

        if not re.match(r'^[a-zA-Z0-9-]+$', skill_name):
            raise ValueError(
                f"Skill name '{skill_name}' is invalid. Only English letters, numbers, and '-' are allowed"
            )
        if skill_name in self.RESERVED_SKILL_NAMES:
            raise ValueError(f"Skill name '{skill_name}' is reserved and cannot be used")
        skill_info["name"] = skill_name

        return skill_name, skill_info, processed_files

    def get_or_create_skill(self, skill_path: str) -> Any | None:
        """Get existing skill by path or create from filesystem.

        Args:
            skill_path: Skill path (git://path or local://name)

        Returns:
            Skill object or None if not found
        """
        # Check if already exists in DB
        existing = self.get_skill_by_path(skill_path)
        if existing:
            return existing

        # Parse path and check filesystem
        try:
            protocol, source_path = self.parse_skill_path(skill_path)
        except ValueError:
            return None

        if not source_path.exists():
            return None

        # Parse skill info from filesystem
        skill_info = SkillParser.parse(source_path)
        if not skill_info:
            return None

        # Create new skill using repository
        skill_data = {
            'name': skill_info.get("name", source_path.name),
            'description': skill_info.get("description", ""),
            'git_path': skill_path,
            'category': skill_info.get("category", "general"),
            'tags': json.dumps(skill_info.get("tags", [])),
            'is_public': (protocol == "git"),
            'is_builtin': False,
            'user_id': None,
            'gmt_created': datetime.utcnow(),
            'gmt_modified': datetime.utcnow(),
        }
        return self._skill_repo.create(skill_data)

    async def upload_skill(
        self,
        uploaded_files: list[dict[str, Any]],
        user_id: str | None = None,
        bolt_id: str | None = None
    ):
        """
        上传本地技能

        Args:
            uploaded_files: 上传的文件列表，每个文件是一个 dict:
                - filename: 文件名
                - content: 文件内容（bytes）
                - relative_path: 相对路径（文件夹上传时使用）
            user_id: Bot owner ID，用于设备文件系统路由和 Skill 元数据
            bolt_id: Bot ID，为空时默认使用 'default'

        Returns:
            创建的技能对象

        Raises:
            ValueError: 如果验证失败
        """
        logger.info(
            f"[SkillService.upload_skill] Start: file_count={len(uploaded_files)}, "
            f"user_id={user_id}, bolt_id={bolt_id}"
        )

        if not uploaded_files:
            logger.error("[SkillService.upload_skill] No files uploaded")
            raise ValueError("No files uploaded")

        # Check if single ZIP file
        if len(uploaded_files) == 1 and uploaded_files[0]['filename'].endswith('.zip'):
            logger.info("[SkillService.upload_skill] Extracting ZIP file")
            zip_content = uploaded_files[0]['content']
            uploaded_files = self._extract_zip_files(zip_content)

        logger.info(f"[SkillService.upload_skill] Processing {len(uploaded_files)} files")

        # 打印所有文件信息
        for idx, file_info in enumerate(uploaded_files):
            logger.debug(
                f"[SkillService.upload_skill] File {idx}: "
                f"filename={file_info.get('filename')}, relative_path={file_info.get('relative_path')}"
            )

        skill_name, skill_info, processed_files = self._prepare_upload_plan(uploaded_files)

        # ===== 通过 DeviceFileSystem 写入文件（自动适配 local/arca/teclaw） =====
        device_fs = self._device_fs_factory(bolt_id, user_id)
        # POOL_ACTIVE 后 DB locator 已经是 Pool canonical 绝对路径。重传同名
        # 本地技能时必须继续使用该 locator；否则会写到 Legacy bridge 后又以
        # Legacy locator 新建一条重复记录。查询必须始终带 Bot owner：历史
        # collaborator metadata 由离线 DB 订正处理，在线请求不从不完整 locator
        # 猜测记录归属，避免 ``default`` / desktop / teclaw 场景跨 owner 命中。
        existing_skill = self._skill_repo.get_bot_local_by_name(
            bot_id=bolt_id or "default",
            name=skill_name,
            user_id=user_id,
        )
        existing_locator = (
            str(existing_skill["git_path"])[len("local://") :]
            if existing_skill is not None
            else ""
        )
        locator_skill_dir = (
            Path(existing_locator)
            if existing_locator.startswith("/")
            else self.local_dir / skill_name
        )
        # During cutover the existing DB locator can still point at Legacy.
        # Resolve it before both file I/O and persistence so a same-name upload
        # becomes a controlled copy-forward into canonical Pool rather than
        # continuing to write Legacy.
        skill_dir_str = self._local_skill_locator_adapter(
            str(locator_skill_dir)
        )
        engine_skill_dir_str = self._local_skill_path_adapter(skill_dir_str)
        logger.info(
            f"[SkillService.upload_skill] Skill directory: {skill_dir_str} "
            f"(engine: {engine_skill_dir_str})"
        )

        try:
            # 先清理已存在的目录
            await device_fs.delete_tree(engine_skill_dir_str)

            # 逐文件写入（DeviceFileSystem 自动创建父目录）
            for file_info in processed_files:
                relative_path = file_info["relative_path"]
                content = file_info["content"]
                file_path = f"{engine_skill_dir_str}/{relative_path}"
                await device_fs.write_file(file_path, content)
                logger.debug(f"[SkillService.upload_skill] Saved file: {file_path}")

            logger.info(f"[SkillService.upload_skill] Using skill name from SKILL.md: '{skill_name}'")

            # Check if skill with same path already exists (区分 Bot)
            skill_path = f"local://{skill_dir_str}"
            if existing_skill:
                # Update existing skill metadata using repository
                update_data = {
                    'name': skill_name,
                    'description': skill_info.get("description", ""),
                    'category': skill_info.get("category", "general"),
                    'tags': json.dumps(skill_info.get("tags", [])),
                    'git_path': skill_path,
                    'gmt_modified': datetime.utcnow()
                }
                if user_id:
                    update_data['user_id'] = user_id
                updated = self._skill_repo.update(existing_skill['id'], update_data)
                logger.info(
                    f"[SkillService.upload_skill] Updated existing skill: {skill_name} "
                    f"(id: {existing_skill['id']})"
                )
                return updated
            else:
                # Create new database record
                skill = self.create_skill(
                    name=skill_name,
                    description=skill_info.get("description", ""),
                    skill_path=skill_path,
                    category=skill_info.get("category", "general"),
                    tags=skill_info.get("tags", []),
                    is_public=False,  # 本地技能默认不公开
                    user_id=user_id,
                    bolt_id=bolt_id
                )
                logger.info(f"[SkillService.upload_skill] Created new skill: {skill_name} (id: {skill.get('id')})")
                return skill

        except Exception as e:
            # 回滚：通过 DeviceFileSystem 清理已创建的文件
            try:
                await device_fs.delete_tree(engine_skill_dir_str)
            except Exception:
                logger.warning("[SkillService.upload_skill] Cleanup failed for %s", engine_skill_dir_str)
            # Re-raise with more context
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"Upload processing error: {str(e)}")

    # ----- Read -----
    def get_skill(self, skill_id: str, user_id: str | None = None):
        """通过 ID 获取技能"""
        return self._skill_repo.get_by_id(skill_id)

    def get_skill_by_path(self, skill_path: str, bolt_id: str | None = None, user_id: str | None = None):
        """通过 skill_path 获取技能

        Args:
            skill_path: 技能路径
            bolt_id: Bot ID，为空时默认使用 'default'
            user_id: 用户ID，用于用户隔离
        """
        # 需要列出所有技能并查找匹配的 git_path
        skills = self._skill_repo.list_skills(bolt_id=bolt_id)
        for skill in skills:
            if skill.get('git_path') == skill_path:
                # 用户隔离：如果指定了 user_id，必须匹配
                if user_id is not None and skill.get('user_id') != user_id:
                    continue
                return skill
        return None

    def get_skill_by_name(self, name: str, user_id: str | None = None):
        """通过技能名称获取技能"""
        return self._skill_repo.get_by_name_global(name, user_id=user_id)

    def get_skill_by_link_name(self, link_name: str, bolt_id: str | None = None):
        """通过 link_name 获取技能 (e.g., infra_demo_odps-sql-generator)

        Args:
            link_name: 技能链接名称
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        # 优先使用 repository 的 get_by_link_name 方法
        if hasattr(self._skill_repo, 'get_by_link_name'):
            result = self._skill_repo.get_by_link_name(link_name, bolt_id=bolt_id)
            if result:
                return result

        # Fallback: 遍历所有技能，支持从 git_path 生成 link_name 匹配
        skills = self._skill_repo.list_skills(bolt_id=bolt_id)
        for skill in skills:
            # 优先使用已有的 link_name 字段
            skill_link_name = skill.get('link_name')
            if skill_link_name and skill_link_name == link_name:
                return skill

            # 兼容：从 git_path 生成 link_name 进行匹配
            git_path = skill.get('git_path', '')
            if git_path.startswith('git://'):
                relative_path = git_path[6:]  # Remove "git://"
                generated_link_name = self.get_link_name(relative_path)
                if generated_link_name == link_name:
                    return skill

        return None

    def list_skills(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        is_public: bool | None = None,
        user_id: str | None = None,
        bolt_id: str | None = None
    ):
        """列出技能（支持过滤）

        Args:
            category: 分类过滤
            tags: 标签过滤
            is_public: 是否公开过滤
            user_id: 用户ID过滤
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        skills = self._skill_repo.list_skills(user_id, bolt_id=bolt_id)

        # Filter by category (client-side)
        if category:
            skills = [s for s in skills if s.get('category') == category]

        # Filter by is_public (client-side)
        if is_public is not None:
            skills = [s for s in skills if s.get('is_public') == is_public]

        # Filter by tags (client-side for JSON field)
        if tags:
            filtered = []
            for skill in skills:
                skill_tags = json.loads(skill['tags']) if skill.get('tags') else []
                if any(tag in skill_tags for tag in tags):
                    filtered.append(skill)
            skills = filtered

        # Sort by gmt_created desc
        skills.sort(key=lambda x: x.get('gmt_created', ''), reverse=True)

        return skills

    def search_skills_db(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 20,
        bolt_id: str | None = None
    ):
        """搜索技能（数据库）

        Args:
            query: 搜索关键词
            user_id: 用户ID过滤
            limit: 返回结果数量限制
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        skills = self._skill_repo.list_skills(user_id, bolt_id=bolt_id)
        query_lower = query.lower()

        results = []
        for skill in skills:
            if (
                query_lower in (skill.get('name') or '').lower()
                or query_lower in (skill.get('description') or '').lower()
                or query_lower in (skill.get('category') or '').lower()
            ):
                results.append(skill)

        return results[:limit]

    def search_market_skills(
        self,
        query: str,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """搜索市场技能（优先使用缓存，缓存未命中时查数据库）

        复用 list_git_skills() 的缓存结果进行搜索，
        缓存未命中时查询数据库作为兜底。

        Args:
            query: 搜索关键词
            limit: 返回结果数量限制

        Returns:
            List[Dict]: 匹配的 git 技能列表（不含 local:// 技能）
        """
        logger.info(f"[SkillService.search_market_skills] Searching for '{query}' with limit={limit}")

        # 尝试利用缓存获取所有 git 技能
        all_git_skills = self.list_git_skills()

        # 缓存未命中时，查数据库作为兜底
        if not all_git_skills:
            logger.info("[SkillService.search_market_skills] Cache empty, querying database as fallback")
            all_skills = self._skill_repo.list_skills(user_id=None, bolt_id=None)
            # 过滤 git:// 技能，排除 local:// 技能
            all_git_skills = [
                s for s in all_skills
                if s.get('git_path', '').startswith('git://')
            ]

        query_lower = query.lower()
        results = []
        for skill in all_git_skills:
            if (
                query_lower in (skill.get('name') or '').lower()
                or query_lower in (skill.get('description') or '').lower()
                or query_lower in (skill.get('category') or '').lower()
            ):
                results.append(skill)

        logger.info(f"[SkillService.search_market_skills] Found {len(results)} matching skills")
        return results[:limit]

    def list_user_skills(self, user_id: str | None = None, bolt_id: str | None = None):
        """列出用户上传的技能

        Args:
            user_id: 用户ID
            bolt_id: Bot ID，为空时默认使用 'default'
        """
        return self._skill_repo.list_skills(user_id, bolt_id=bolt_id)

    def list_git_skills(
        self,
        path: str | None = None,
        bolt_id: str | None = None,
        orderby: str | None = None
    ) -> list[dict[str, Any]]:
        """列出 marketplace 中的技能 (git_path 以 git:// 开头)

        Args:
            path: 可选的路径过滤，如传入则只返回 git_path 包含该路径的技能
            bolt_id: Bot ID，传入 None 表示不区分 bolt（marketplace 全局），传入具体值则按 bolt 过滤
            orderby: 排序方式，'latest'(最新) 或 'hotest'(最热)，默认按创建时间倒序

        Returns:
            List[Dict]: 技能列表，包含数据库中的所有字段
        """
        import time
        start_time = time.time()

        # 缓存键：根据排序方式区分
        cache_key = f"market_skills_list_{orderby or 'default'}"

        # 尝试从缓存获取
        cached_skills = self._get_cached(cache_key)
        if cached_skills is not None:
            logger.info(f"[SkillService.list_git_skills] Cache HIT (orderby={orderby}), returning {len(cached_skills)} skills from cache")
            git_skills = cached_skills
        else:
            logger.info(f"[SkillService.list_git_skills] Cache MISS (orderby={orderby}), querying database...")
            # 缓存未命中，查询数据库
            if orderby and hasattr(self._skill_repo, 'list_git_skills_with_order'):
                git_skills = self._skill_repo.list_git_skills_with_order(orderby=orderby)
            else:
                # 原有逻辑
                all_skills = self._skill_repo.list_skills(user_id=None, bolt_id=bolt_id)
                git_skills = []
                for skill in all_skills:
                    git_path = skill.get('git_path', '')
                    if git_path and git_path.startswith('git://'):
                        git_skills.append(skill)

            # 存入缓存
            self._set_cache(cache_key, git_skills)
            logger.info(f"[SkillService.list_git_skills] Cache SET (orderby={orderby}), cached {len(git_skills)} skills")

        # 如果指定了 path，在内存中过滤
        if path:
            filtered_skills = []
            path_prefix = path.rstrip('/') + '/'
            for skill in git_skills:
                git_path = skill.get('git_path', '')
                rel_path = git_path[6:]  # 去掉 git:// 前缀
                # 精确匹配路径边界：skill 路径必须在目录下（以 path/ 开头）
                if rel_path.startswith(path_prefix):
                    filtered_skills.append(skill)
            logger.info(f"[SkillService.list_git_skills] Filtered by path='{path}', returning {len(filtered_skills)} skills")
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"[API_PERF] list_git_skills total_cost={elapsed:.2f}ms cache={'hit' if cached_skills else 'miss'}")
            return filtered_skills

        elapsed = (time.time() - start_time) * 1000
        logger.info(f"[API_PERF] list_git_skills total_cost={elapsed:.2f}ms orderby={orderby} cache={'hit' if cached_skills else 'miss'} rows={len(git_skills)}")
        return git_skills

    # ----- Update -----
    def update_skill(
        self,
        skill_id: str,
        user_id: str | None = None,
        **kwargs
    ):
        """更新技能"""
        skill = self.get_skill(skill_id, user_id)
        if not skill:
            return None

        allowed_fields = ["name", "description", "category", "tags",
                         "input_schema", "output_schema", "is_public",
                         "risk_tags", "mcp_dependencies"]

        update_data = {}
        for key, value in kwargs.items():
            if key in allowed_fields:
                if key in ("tags", "risk_tags", "mcp_dependencies") and isinstance(value, list):
                    value = json.dumps(value)
                update_data[key] = value

        update_data['gmt_modified'] = datetime.utcnow()
        return self._skill_repo.update(skill_id, update_data)

    def _can_delete_skill(
        self,
        skill: dict,
        user_id: str | None = None,
        authorized_bot_owner_id: str | None = None,
        collaborator_authorization_verified: bool = False,
    ) -> bool:
        """检查用户是否有权限删除技能

        只有以下用户可以删除技能：
        1. 技能的创建者（user_id 匹配）
        2. 指定的管理员用户
        3. 已在 HTTP adapter 经协作者拦截器授权、且当前 Service 也绑定到
           该 Skill 所属 Bot owner 的协作者
        """
        if not user_id:
            return False

        # 管理员可以删除任何技能
        if user_id in skill_admin():
            return True

        # 技能的创建者可以删除自己的技能
        skill_user_id = skill.get('user_id')
        if skill_user_id and str(skill_user_id) == str(user_id):
            return True

        # ``authorized_bot_owner_id`` 不是客户端参数，只能由已经完成
        # CollaboratorPermissionInterceptor 校验的 adapter 注入。二次校验
        # 它与 Skill metadata 和本 Service 的设备 owner 一致，避免调用方仅凭
        # 伪造 owner 值跨 Bot 删除。
        if (
            skill_user_id
            and authorized_bot_owner_id
            and collaborator_authorization_verified
            and str(skill_user_id) == str(authorized_bot_owner_id)
            and str(self._device_owner_id or "") == str(authorized_bot_owner_id)
        ):
            return True

        return False

    async def _delete_active_entry(self, device_fs, active_path: Path) -> bool:
        """通过 device_fs 删除 active_dir 下的一个 entry（软链/文件/目录均可）。

        Phase 4 引入：三处（delete_skill step 1, activate_skill 旧链替换,
        deactivate_skill）都通过 engine 在 VM 内删，而非宿主机 shutil.rmtree
        — active_path 是 engine-view 路径，宿主机直接动手要么 noop 要么删错。

        Args:
            device_fs: DeviceFileSystem 实例
            active_path: active_dir 下的目标 Path（Backend 视角，会作为字符串传给 engine）

        Returns:
            bool: 删除是否成功（不存在视为成功；其他失败返回 False，由 caller 决定后续行为）
        """
        try:
            success = await device_fs.delete_tree(str(active_path))
            if success:
                logger.info(f"[SkillService._delete_active_entry] Deleted via device_fs: {active_path}")
            else:
                logger.warning(f"[SkillService._delete_active_entry] device_fs.delete_tree returned False: {active_path}")
            return success
        except Exception as e:
            logger.warning(
                f"[SkillService._delete_active_entry] Exception deleting {active_path}: {e}",
                exc_info=True,
            )
            return False

    # ----- Delete -----
    async def delete_skill(
        self,
        skill_id: str,
        user_id: str | None = None,
        authorized_bot_owner_id: str | None = None,
        collaborator_authorization_verified: bool = False,
    ) -> bool:
        """删除技能 - 同时删除数据库记录和物理文件

        Args:
            skill_id: 技能ID
            user_id: 当前操作用户ID（用于权限验证）
            authorized_bot_owner_id: 已完成协作者授权时，由 adapter 注入的
                Bot owner；不接受任何外部请求透传
            collaborator_authorization_verified: adapter 从 fail-closed
                协作者拦截器取得的可信授权结论

        Returns:
            bool: 删除是否成功

        Raises:
            ValueError: 用户无权删除此技能
        """
        skill = self.get_skill(skill_id, user_id)
        if not skill:
            logger.warning(f"[SkillService] Skill not found: {skill_id}")
            return False

        # 权限检查：只有技能所有者或管理员可以删除
        if not self._can_delete_skill(
            skill,
            user_id,
            authorized_bot_owner_id=authorized_bot_owner_id,
            collaborator_authorization_verified=collaborator_authorization_verified,
        ):
            skill_owner = skill.get('user_id')
            logger.warning(f"[SkillService] Permission denied: user={user_id} attempted to delete skill={skill_id} owned by={skill_owner}")
            raise ValueError("无权删除此技能：您不是该技能的创建者，且没有管理员权限")

        git_path = skill.get('git_path') or ''
        published_center_uuid = (
            skill.get("skill_uuid")
            if git_path.startswith("center://")
            and str(skill.get("status") or "").upper() == "PUBLISHED"
            else None
        )
        references = self._skill_repo.list_skill_set_references(
            skill_id,
            skill_uuid=published_center_uuid,
        )
        if references:
            raise SkillReferencedBySkillSetError(
                [str(ref["skill_set_id"]) for ref in references]
            )

        # 获取技能名称和路径
        skill_name = skill.get('name')
        bolt_id = skill.get('bolt_id')
        skill_user_id = skill.get('user_id') or user_id
        device_owner_id = self._device_owner_id or skill_user_id
        is_shared_source = (
            not skill.get('user_id')
            and git_path.startswith(("git://", "center://"))
        )

        logger.info(f"[SkillService] Deleting skill: id={skill_id}, name={skill_name}, git_path={git_path}")
        logger.info(f"[SkillService] local_dir: {self.local_dir}, active_dir: {self.active_dir}")

        device_fs = None
        if not is_shared_source:
            try:
                device_fs = self._device_fs_factory(bolt_id, device_owner_id)
            except Exception as e:
                if self.runtime_uses_pool_paths:
                    raise SkillDeleteConsistencyError(
                        "failed to resolve device filesystem before delete"
                    ) from e
                logger.warning(
                    "[SkillService] Legacy runtime has no available device "
                    "filesystem; keeping historical metadata-only delete",
                    exc_info=True,
                )

        # 1. 先收敛 active entry。已激活 Skill 的 entry 删除失败时必须 fail closed，
        # 否则继续删除 source/DB 会把它变成 dangling link。未激活时 entry
        # 本来就不存在，仍保持幂等成功。
        link_name = self.get_link_name(skill_name) if skill_name else None
        logger.info(f"[SkillService] link_name: {link_name}")
        if link_name and device_fs is not None:
            active_link = self.active_dir / link_name
            try:
                active_entry_exists = await device_fs.exists(str(active_link))
            except Exception as e:
                if self.runtime_uses_pool_paths:
                    raise SkillDeleteConsistencyError(
                        f"failed to inspect active skill entry before delete: {active_link}"
                    ) from e
                logger.warning(
                    "[SkillService] Legacy runtime could not inspect active entry: %s",
                    active_link,
                    exc_info=True,
                )
                active_entry_exists = True
            if active_entry_exists:
                active_deleted = await self._delete_active_entry(
                    device_fs, active_link
                )
                if not active_deleted and self.runtime_uses_pool_paths:
                    raise SkillDeleteConsistencyError(
                        f"failed to delete active skill entry: {active_link}"
                    )

        # 2. 删除物理文件（仅 local:// 技能）— 通过 DeviceFileSystem
        logger.info(f"[SkillService] Checking git_path: {git_path}, starts_with_local={git_path.startswith('local://')}")
        if git_path.startswith('local://') and device_fs is not None:
            # teclaw: skills-local/<name> → workspace/skills-local/<name>;
            # 非 teclaw: identity（主机路径原样）。
            local_path_str = self._local_skill_path_adapter(git_path[8:])  # 去掉 local:// 前缀
            try:
                local_source_exists = await device_fs.exists(local_path_str)
            except Exception as e:
                if self.runtime_uses_pool_paths:
                    raise SkillDeleteConsistencyError(
                        f"failed to inspect local skill source before delete: {local_path_str}"
                    ) from e
                logger.warning(
                    "[SkillService] Legacy runtime could not inspect local source: %s",
                    local_path_str,
                    exc_info=True,
                )
                local_source_exists = True
            if local_source_exists:
                try:
                    success = await device_fs.delete_tree(local_path_str)
                except Exception as e:
                    if self.runtime_uses_pool_paths:
                        raise SkillDeleteConsistencyError(
                            f"failed to delete local skill source: {local_path_str}"
                        ) from e
                    logger.warning(
                        "[SkillService] Legacy runtime failed to delete local source: %s",
                        local_path_str,
                        exc_info=True,
                    )
                    success = False
                if not success:
                    if self.runtime_uses_pool_paths:
                        raise SkillDeleteConsistencyError(
                            f"failed to delete local skill source: {local_path_str}"
                        )
                    logger.warning(
                        "[SkillService] Legacy runtime did not delete local source: %s",
                        local_path_str,
                    )
                else:
                    logger.info(f"[SkillService] Deleted skill files: {local_path_str}")
        elif not git_path.startswith('local://'):
            logger.info("[SkillService] Not a local skill, skipping physical delete")

        # 3. 删除数据库记录
        return self._skill_repo.delete(skill_id)

    # ----- Sync -----
    def sync_skills_from_git(
        self,
        user_id: str | None = None,
        git_renames: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """从 Git 仓库同步技能到数据库

        Args:
            user_id: 用户 ID（保留用于兼容性，Git market skills 是系统共享的）
            git_renames: Git detected skill directory renames, old git:// path
                to new git:// path. Rename entries update existing DB rows
                before normal create/delete processing.

        Git market skills are system-wide and have no user_id or bolt_id.
        """
        results = {"created": 0, "updated": 0, "deleted": 0, "failed": 0, "skipped": 0, "errors": []}
        git_renames = git_renames or {}

        # Resolve scan target. In cloud mode, _resolve_sync_scan_target raises
        # if the local atomic repo is missing — we MUST NOT fall back to NAS
        # because that would re-trigger the 2026-05-13 race. Abort gracefully
        # and let the next sync recover once GitSyncService bootstraps.
        try:
            scan_target = self._resolve_sync_scan_target()
        except RuntimeError as e:
            logger.error("[sync_skills_from_git] Aborted: %s", e)
            results["errors"].append(str(e))
            return results

        if not scan_target.exists():
            results["errors"].append(f"Skills repo directory not found: {scan_target}")
            return results

        git_paths_in_repo: set = set()
        skills_from_git: list[dict[str, Any]] = []

        def scan_directory(directory: Path, relative_path: str = "") -> None:
            try:
                for item in sorted(directory.iterdir()):
                    if item.name.startswith("."):
                        continue

                    item_relative = f"{relative_path}/{item.name}" if relative_path else item.name

                    if item.is_dir():
                        skill_md = item / "SKILL.md"

                        # 只有包含 SKILL.md 的目录才被认为是技能
                        if skill_md.exists():
                            skill_info = self._parse_skill_md_for_db(item)
                            if skill_info:
                                # Determine category from first-level directory
                                parts = item_relative.split("/")
                                if len(parts) >= 1 and parts[0]:
                                    skill_info["category"] = parts[0].lower()

                                skill_path = f"git://{item_relative}"
                                git_paths_in_repo.add(skill_path)
                                skills_from_git.append({
                                    "skill_path": skill_path,
                                    "info": skill_info
                                })
                        else:
                            scan_directory(item, item_relative)
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Error scanning {directory}: {str(e)}")

        scan_directory(scan_target)

        # Get all existing git-based skills from DB (no bolt_id filter for market skills)
        existing_skills = self._skill_repo.list_skills(bolt_id=None)
        existing_by_path: dict[str, dict] = {
            s.get('git_path'): s for s in existing_skills
            if s.get('git_path') and s.get('git_path').startswith("git://")
        }
        skills_by_path: dict[str, dict[str, Any]] = {
            s["skill_path"]: s for s in skills_from_git
        }
        processed_git_paths: set[str] = set()
        processed_existing_paths: set[str] = set()

        def build_update_data(
            existing: dict,
            skill_path: str,
            skill_info: dict,
            link_name: str,
            *,
            force_git_path: bool = False,
        ) -> tuple[bool, dict]:
            has_changes = False
            update_data = {}

            if force_git_path and existing.get('git_path') != skill_path:
                update_data['git_path'] = skill_path
                has_changes = True

            # 检查 name 和 description 是否变化
            if existing.get('name') != skill_info["name"]:
                update_data['name'] = skill_info["name"]
                has_changes = True
            if existing.get('description') != skill_info["description"]:
                update_data['description'] = skill_info["description"]
                has_changes = True

            # 普通同步只在空/general 时更新类目；rename 表示路径已移动，
            # 类目也应跟随新 git_path 的一级目录。
            current_category = existing.get('category') or ""
            if (
                force_git_path
                or not current_category
                or current_category == "general"
            ) and current_category != skill_info["category"]:
                update_data['category'] = skill_info["category"]
                has_changes = True

            # 检查 tags 是否需要更新（空时才更新）
            raw_tags = existing.get('tags')
            current_tags = raw_tags if isinstance(raw_tags, list) else (json.loads(raw_tags) if raw_tags else [])
            if not current_tags and skill_info["tags"]:
                update_data['tags'] = json.dumps(skill_info["tags"])
                has_changes = True

            # Try to add link_name if not exists
            if not existing.get('link_name'):
                try:
                    update_data['link_name'] = link_name
                    has_changes = True
                except Exception:
                    pass

            return has_changes, update_data

        # Apply git renames first. A successful rename keeps ac_skill.id and
        # skill_uuid stable, then normal create/delete skips both paths.
        for old_path, new_path in git_renames.items():
            existing = existing_by_path.get(old_path)
            skill_data = skills_by_path.get(new_path)
            if not existing or not skill_data:
                continue

            relative_path = new_path[6:] if new_path.startswith("git://") else new_path
            link_name = self.get_link_name(relative_path)
            has_changes, update_data = build_update_data(
                existing,
                new_path,
                skill_data["info"],
                link_name,
                force_git_path=True,
            )
            processed_git_paths.add(new_path)
            processed_existing_paths.add(old_path)
            if not has_changes:
                results["skipped"] += 1
                continue
            update_data['gmt_modified'] = datetime.utcnow()
            try:
                self._skill_repo.update(existing['id'], update_data)
                logger.info(
                    "[sync_skills_from_git] RENAME skill: id=%s, "
                    "old_git_path=%s, new_git_path=%s, fields=%s",
                    existing['id'],
                    old_path,
                    new_path,
                    sorted(update_data.keys()),
                )
                results["updated"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(
                    f"Failed to rename {old_path} -> {new_path}: {str(e)}"
                )

        # Process each skill from git
        for skill_data in skills_from_git:
            skill_path = skill_data["skill_path"]
            skill_info = skill_data["info"]
            if skill_path in processed_git_paths:
                continue

            # Generate link_name from git_path (e.g., git://infra/demo/skill -> infra_demo_skill)
            relative_path = skill_path[6:] if skill_path.startswith("git://") else skill_path  # Remove "git://"
            link_name = self.get_link_name(relative_path)

            if skill_path in existing_by_path:
                # Update existing skill
                existing = existing_by_path[skill_path]
                has_changes, update_data = build_update_data(
                    existing,
                    skill_path,
                    skill_info,
                    link_name,
                )

                # 只有有变化时才执行更新
                if has_changes:
                    update_data['gmt_modified'] = datetime.utcnow()
                    try:
                        self._skill_repo.update(existing['id'], update_data)
                        logger.info(
                            "[sync_skills_from_git] UPDATE skill: id=%s, "
                            "name=%s, git_path=%s, fields=%s",
                            existing['id'],
                            skill_info["name"],
                            skill_path,
                            sorted(update_data.keys()),
                        )
                        results["updated"] += 1
                    except Exception as e:
                        results["failed"] += 1
                        results["errors"].append(f"Failed to update {skill_path}: {str(e)}")
                else:
                    results["skipped"] += 1
            else:
                # Create new skill using repository
                # Git market skills have no user_id or bolt_id (they are shared)
                skill_data = {
                    'name': skill_info["name"],
                    'description': skill_info["description"],
                    'git_path': skill_path,
                    'category': skill_info["category"],
                    'tags': json.dumps(skill_info["tags"]),
                    'is_public': True,
                    'is_builtin': False,
                    'user_id': None,
                    'gmt_created': datetime.utcnow(),
                    'gmt_modified': datetime.utcnow()
                }
                # Try to add link_name (ignore if field doesn't exist)
                try:
                    skill_data['link_name'] = link_name
                except Exception:
                    pass

                try:
                    created_row = self._skill_repo.create(skill_data)
                    new_id = (
                        created_row.get("id")
                        if isinstance(created_row, dict)
                        else created_row
                    )
                    logger.info(
                        "[sync_skills_from_git] CREATE skill: new_id=%s, "
                        "name=%s, git_path=%s",
                        new_id,
                        skill_info["name"],
                        skill_path,
                    )
                    results["created"] += 1
                except Exception as e:
                    results["failed"] += 1
                    results["errors"].append(f"Failed to create {skill_path}: {str(e)}")

        # Delete skills that no longer exist in git
        for skill_path, skill in existing_by_path.items():
            if skill_path in processed_existing_paths:
                continue
            if skill_path not in git_paths_in_repo:
                logger.warning(
                    "[sync_skills_from_git] DELETE orphan skill: id=%s, "
                    "name=%s, git_path=%s",
                    skill['id'],
                    skill.get('name'),
                    skill_path,
                )
                self._skill_repo.delete(skill['id'])
                results["deleted"] += 1

        # 刷新市场缓存（同步完成后）
        logger.info(f"[sync_skills_from_git] Sync completed: created={results['created']}, updated={results['updated']}, deleted={results['deleted']}, skipped={results['skipped']}, failed={results['failed']}")
        self._refresh_market_cache()

        return results

    # ========================================================================
    # 类目树同步
    # ========================================================================

    def sync_categories_from_git(self) -> dict[str, Any]:
        """扫描 git 仓库 skills 目录，把中间层级目录写入 ac_skill_category 表。

        规则：
        - 含 SKILL.md 的目录是技能，不写入类目表
        - 不含 SKILL.md 的目录是类目节点，写入类目表
        - 路径格式：/business/aml/（物化路径，唯一标识）
        """
        results = {"created": 0, "skipped": 0, "errors": []}

        repo = self._category_repo
        if repo is None:
            results["errors"].append("SkillCategoryRepository not available")
            return results

        market_repo_dir = self._get_market_repo_dir()
        if not market_repo_dir.exists():
            results["errors"].append(f"Market repo dir not found: {market_repo_dir}")
            return results

        # 收集所有类目节点：path -> {code, parent_code, level}
        categories: dict[str, dict] = {}

        def _scan(directory: Path, relative: str = "") -> None:
            try:
                for item in sorted(directory.iterdir()):
                    if item.name.startswith(".") or not item.is_dir():
                        continue

                    child_rel = f"{relative}/{item.name}" if relative else item.name

                    # 含 SKILL.md → 技能，跳过
                    if (item / "SKILL.md").exists():
                        continue

                    # 不含 SKILL.md → 类目节点
                    path = f"/{child_rel}/"
                    parts = child_rel.split("/")
                    code = parts[-1]
                    # 一级类目 parent_code="ROOT"，二级及以下用父级 code
                    parent_code = "ROOT" if len(parts) == 1 else parts[-2]
                    level = len(parts)

                    categories[path] = {
                        "code": code, "parent_code": parent_code,
                        "level": level, "name": code,
                    }

                    # 递归扫描子目录
                    _scan(item, child_rel)
            except Exception as exc:
                results["errors"].append(f"Error scanning {directory}: {exc}")

        _scan(market_repo_dir)

        # 写入数据库（已存在则跳过）
        sort_counter = 0
        for path in sorted(categories):
            cat = categories[path]
            existing = repo.get_by_path(path)
            if existing:
                results["skipped"] += 1
                continue
            try:
                repo.create(
                    code=cat["code"], name=cat["name"],
                    parent_code=cat["parent_code"], path=path,
                    level=cat["level"], sort_order=sort_counter,
                )
                results["created"] += 1
            except Exception as exc:
                results["errors"].append(f"Failed to create category {path}: {exc}")
            sort_counter += 1

        logger.info(
            "[sync_categories_from_git] completed: created=%d, skipped=%d, errors=%d",
            results["created"], results["skipped"], len(results["errors"]),
        )
        return results

    # ========================================================================
    # 工具方法
    # ========================================================================

    @staticmethod
    def _get_icon_for_category(category: str) -> str:
        """根据分类获取图标"""
        icons = {
            'file': '📄',
            'code': '💻',
            'web': '🌐',
            'search': '🔍',
            'database': '🗄️',
            'api': '🔌',
            'tool': '🛠️',
            'security': '🔒',
            'analysis': '📊',
            'general': '🔧',
            'infra': '🏗️',
            'business': '💼',
        }
        return icons.get(category.lower(), '🔧')

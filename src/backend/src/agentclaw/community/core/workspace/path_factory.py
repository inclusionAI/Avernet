"""
工作区路径计算工厂。

从 services/openclawserver/server/config.py 迁移。
新架构统一从此处 import，不再引用旧 config。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin

logger = get_logger()

# 默认 aidesktop 根目录
DEFAULT_AIDESKTOP_ROOT = Path("/aidesktop")

# SQLite 本地模式固定根目录
SQLITE_PERSONAL_ROOT = Path.home() / ".moltis"

# NAS挂载根目录
DEFAULT_ARCA_ROOT = Path("/home/admin/.merge_nas")

# Desktop bot 桌面版的 engine-view skills 根目录（VM 内 virtiofs mount path）。
# 跟 BAAS 同事 / Phase 2a/2b ocwn 编排约定：engine 在 VM 内看到的 skills 根永远是
# `/home/admin/.openclaw/workspace/skills`。Backend 通过 BaasDeviceFileSystem 调
# engine HTTP API 直接用这个路径，不再经过 OSS view 与 `_convert_path` 转写。
# 仅 desktop bot 走这条路径（ac_bots.bot_type == "desktop"）；service bot 即使
# device_provider 也是 baas，仍然走云端 OSS-view 分支。
# 改路径前请同步 BAAS 同事 + ocwn (Phase 2a) + engine `_convert_path` 处。
BAAS_ENGINE_SKILLS_ROOT = Path("/home/admin/.openclaw/workspace/skills")

# Desktop bot 桌面版的 engine-view skills 根目录（VM 内 virtiofs mount path）。
# 跟 BAAS 同事 / Phase 2a/2b ocwn 编排约定：engine 在 VM 内看到的 skills 根永远是
# `/home/admin/.openclaw/workspace/skills`。Backend 通过 BaasDeviceFileSystem 调
# engine HTTP API 直接用这个路径，不再经过 OSS view 与 `_convert_path` 转写。
# 仅 desktop bot 走这条路径（ac_bots.bot_type == "desktop"）；service bot 即使
# device_provider 也是 baas，仍然走云端 OSS-view 分支。
# 改路径前请同步 BAAS 同事 + ocwn (Phase 2a) + engine `_convert_path` 处。
BAAS_ENGINE_SKILLS_ROOT = Path("/home/admin/.openclaw/workspace/skills")


def _get_config_value(key: str, default_value: Path, env_var: Optional[str] = None) -> Path:
    """从环境变量或 application.yaml 读取配置值。

    优先级:
    1. 环境变量（env_var 指定）
    2. configs/application-dev.yaml 或 application.yaml 的 user_config.{key}
    3. default_value

    Args:
        key: user_config 下的 key 名
        default_value: 找不到时的默认值
        env_var: 环境变量名（可选）

    Returns:
        Path
    """
    if env_var:
        env_value = os.getenv(env_var)
        if env_value:
            if env_value.startswith("~"):
                env_value = str(Path.home()) + env_value[1:]
            logger.info(f"[_get_config_value] {key} loaded from env {env_var}: {env_value}")
            return Path(env_value)

    try:    
        import yaml

        # Overlay selection mirrors the yaml provider's: the test suite
        # (DEPLOY_PROFILE=test, SERVER_ENV unset) reads the neutral community
        # application-test.yaml instead of the corp application-dev.yaml. An
        # explicitly set SERVER_ENV always wins (dev/stable → dev overlay; else base
        # only), so tests that set SERVER_ENV to exercise a specific env are unaffected.
        profile = (os.getenv("DEPLOY_PROFILE") or "").lower()
        # Match yaml_provider._select_overlay_name's env resolution exactly (incl. the
        # REAL_SERVER_ENV fallback the container supplies) so the two sites never
        # disagree on the overlay within one process.
        env = (os.getenv("SERVER_ENV") or os.getenv("REAL_SERVER_ENV") or "").lower()
        if env == "" and profile in ("test", "corp_test"):
            config_names = ["application-test.yaml", "application.yaml"]
        elif env in ("dev", "stable", ""):
            config_names = ["application-dev.yaml", "application.yaml"]
        else:
            config_names = ["application.yaml"]

        # B11: configs live in the community subtree (agentclaw/community/configs);
        # a deploy's assembled runtime `configs/` (cwd) holds them too.
        config_dirs = [
            Path.cwd() / "configs",
            Path(__file__).resolve().parents[2] / "configs",  # agentclaw/community/configs
        ]

        for config_dir in config_dirs:
            for config_name in config_names:
                config_path = config_dir / config_name
                if config_path.exists():
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            config = yaml.safe_load(f)
                            if config and "user_config" in config:
                                user_config = config["user_config"]
                                if key in user_config:
                                    value = user_config[key]
                                    if value.startswith("~"):
                                        value = str(Path.home()) + value[1:]
                                    logger.info(f"[_get_config_value] {key} loaded from {config_path}: {value}")
                                    return Path(value)
                    except Exception as e:
                        logger.warning(f"[_get_config_value] Failed to read {config_path}: {e}")
                        continue
    except ImportError:
        logger.warning(f"[_get_config_value] yaml not installed, using default for {key}")
    except Exception as e:
        logger.warning(f"[_get_config_value] Error reading config for {key}: {e}")

    logger.info(f"[_get_config_value] {key} using default: {default_value}")
    return default_value


def _get_aidesktop_root() -> Path:
    """读取 aidesktop_root（优先 AIDESKTOP_ROOT 环境变量，其次 yaml，默认 /aidesktop）。"""
    return _get_config_value("aidesktop_root", DEFAULT_AIDESKTOP_ROOT, "AIDESKTOP_ROOT")


def _get_aidesktop_env_folder() -> str:
    """Return the physical workspace folder selected by the active Profile."""
    explicit = os.getenv("WORKSPACE_ENV_FOLDER")
    if explicit:
        return explicit

    from agentclaw.community.utils.env_utils import get_current_env

    return f"aidesktop_{get_current_env()}"


def get_bolt_base_dir() -> Path:
    """bolt_data 根目录: {aidesktop_root}/aidesktop_{env}/bolt_data"""
    return _get_aidesktop_root() / _get_aidesktop_env_folder() / "bolt_data"


def get_bolt_shared_dir() -> Path:
    """bolt_shared 共享目录: {aidesktop_root}/aidesktop_{env}/{dir_name}

    dir_name 可被 yaml user_config.git_sync.bolt_shared_dir_name 覆盖，默认 "bolt_shared"。
    """
    aidesktop_root = _get_aidesktop_root()
    aidesktop_env_folder = _get_aidesktop_env_folder()
    dir_name = "bolt_shared"

    try:
        import yaml

        # B11: configs live in the community subtree (agentclaw/community/configs);
        # a deploy's assembled runtime `configs/` (cwd) holds them too.
        config_dirs = [
            Path.cwd() / "configs",
            Path(__file__).resolve().parents[2] / "configs",  # agentclaw/community/configs
        ]
        for config_dir in config_dirs:
            config_path = config_dir / "application.yaml"
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                        if config and "user_config" in config:
                            git_sync = config["user_config"].get("git_sync", {})
                            dir_name = git_sync.get("bolt_shared_dir_name", "bolt_shared")
                            logger.info(f"[get_bolt_shared_dir] bolt_shared_dir_name={dir_name} from {config_path}")
                            break
                except Exception as e:
                    logger.warning(f"[get_bolt_shared_dir] Failed to read {config_path}: {e}")
    except ImportError:
        logger.warning("[get_bolt_shared_dir] yaml not installed, using default")
    except Exception as e:
        logger.warning(f"[get_bolt_shared_dir] Error reading config: {e}")

    return aidesktop_root / aidesktop_env_folder / dir_name


def get_global_skills_repo_dir() -> Path:
    """全局 skills-repo 目录: {bolt_shared}/skills-repo"""
    return get_bolt_shared_dir() / "skills-repo"


def get_global_skills_default_dir() -> Path:
    """全局 skills-default 目录: {bolt_shared}/skills-default"""
    return get_bolt_shared_dir() / "skills-default"


def get_bot_dir(entity_id: str, bot_id: str, entity_type: str = "staff") -> Path:
    """Bot 根目录: {bolt_base}/{entity_type}_{entity_id}/{bot_id}"""
    return get_bolt_base_dir() / f"{entity_type}_{entity_id}" / bot_id


def get_bot_engine_dir(
    entity_id: str,
    bot_id: str,
    engine_type: str = "openclaw",
    entity_type: str = "staff",
) -> Path:
    """Bot 引擎工作目录: {bot_dir}/{engine_type}"""
    return get_bot_dir(entity_id, bot_id, entity_type) / engine_type


def get_bot_engine_config_dir(
    entity_id: str,
    bot_id: str,
    engine_type: str = "openclaw",
    entity_type: str = "staff",
) -> Path:
    """Bot 引擎配置目录: {bot_dir}/{engine_type}_conf"""
    return get_bot_dir(entity_id, bot_id, entity_type) / f"{engine_type}_conf"


# Bot 远端 NAS 挂载目录
def get_bot_nas_dir(
    entity_id: str,
    bot_id: str,
    engine_type: str,
    entity_type: str = "staff",
) -> Path:
    """Bot 远端 NAS 挂载目录: DEFAULT_ARCA_ROOT/get_bot_nas_storage_id"""
    return DEFAULT_ARCA_ROOT / get_bot_nas_storage_id(entity_id, bot_id, engine_type, entity_type)


def get_bot_nas_storage_id(
    entity_id: str,
    bot_id: str,
    engine_type: str,
    entity_type: str = "staff",
) -> str:
    """Bot 远端 NAS 挂载目录（相对路径）: /{env}_{entity_type}_{entity_id}_{engine_type}_{bot_id}

    注意：返回值不包含 DEFAULT_ARCA_ROOT 前缀，是相对于 NAS 根目录的路径。
    调用方如需完整绝对路径，需自行拼接 DEFAULT_ARCA_ROOT。
    """
    from agentclaw.community.utils.env_utils import get_current_env

    data_env = get_current_env()
    return f"{data_env}_{entity_type}_{entity_id}_{engine_type}_{bot_id}"


def get_entity_identity_dir(
    entity_id: str,
    entity_type: str = "staff",
    engine_type: str = "openclaw",
) -> Path:
    """实体身份目录（RULES.md、OKR.md 等所在位置）。

    路径规则:
    - staff + moltis/aicoding: {bolt_base}/{entity_type}_{entity_id}/default/{engine_type}
    - staff + openclaw: {bolt_base}/{entity_type}_{entity_id}/default/{engine_type}/workspace
    - proj/team: {bolt_base}/{entity_type}_{entity_id}/data
    """
    entity_dir = f"{entity_type}_{entity_id}"
    base = get_bolt_base_dir()
    if entity_type == "staff":
        if engine_type in ("moltis", "aicoding", "claude_code"):
            result = base / entity_dir / "default" / engine_type
            logger.debug(
                "[get_entity_identity_dir] engine=%s → flat path: %s",
                engine_type, result,
            )
            return result
        else:
            result = base / entity_dir / "default" / engine_type / "workspace"
            logger.debug(
                "[get_entity_identity_dir] engine=%s → workspace path: %s",
                engine_type, result,
            )
            return result
    else:
        result = base / entity_dir / "data"
        logger.debug(
            "[get_entity_identity_dir] entity_type=%s → data path: %s",
            entity_type, result,
        )
        return result


# singlebox 多 bot 改造: per-bot skills/ 骨架的 host 候选源。
# 跟 LocalDeviceSyncPlugin._REPO_SOURCE_CANDIDATES + _find_repo_source 保持
# 同源逻辑;那边维护共享根 ~/.openclaw/workspace/skills/skills-repo,这边维护
# per-bot 的 test-bots/.../<bot>/openclaw/workspace/skills/skills-repo。
_PER_BOT_REPO_SOURCE_CANDIDATES = (
    Path.home() / "aiworkbench" / "skills-repo",
)


def _find_per_bot_repo_source() -> Optional[Path]:
    """Return the first existing skills-repo source dir (host side)."""
    for c in _PER_BOT_REPO_SOURCE_CANDIDATES:
        if c.is_dir():
            return c
    try:
        shared = get_global_skills_repo_dir()
        if shared.is_dir():
            return shared
    except Exception:
        pass
    return None


def _oss_view_to_host(per_bot_skills_oss: Path) -> Optional[Path]:
    """把 OSS-view 路径 ``/aidesktop/aidesktop_<env>/...`` 翻译成宿主真实路径。

    singlebox: 通过 ``LOCAL_AIDESKTOP_ROOT`` env (由 backend.sh 注入,
    跟 baas 的 LOCAL_AIDESKTOP_ROOT 同源) 把 ``/aidesktop`` 前缀替换。
    env 未设 → None (caller 兜底,跳过 mkdir 避免误建 /aidesktop)。
    """
    host_root_str = os.getenv("LOCAL_AIDESKTOP_ROOT")
    if not host_root_str:
        return None
    if host_root_str.startswith("~"):
        host_root_str = str(Path.home()) + host_root_str[1:]
    host_root = Path(host_root_str)
    oss_str = str(per_bot_skills_oss)
    if not oss_str.startswith("/aidesktop/"):
        # 已经是宿主路径,直接返回
        return per_bot_skills_oss
    rel = oss_str[len("/aidesktop/"):]
    return host_root / rel


def _maybe_local_translate(p: Path) -> Path:
    """LOCAL 模式下,把 OSS-view 路径翻译为宿主路径; 否则原样返回。

    供 WorkspacePathFactory 内部所有返回 path 的方法使用 — backend 内部
    需要 host path 才能 mkdir/写文件; engine adapter 那边自己拼 OSS-view,
    不走 path_factory。

    单元测试或非 singlebox 部署 (LOCAL_AIDESKTOP_ROOT 未注入) 会 fallback
    回原 OSS-view, 保持改前行为, 不破单测。
    """
    from agentclaw.community.utils.env_utils import is_local_mode
    if not is_local_mode():
        return p
    host = _oss_view_to_host(p)
    return host if host is not None else p


def _ensure_per_bot_skills_skeleton(per_bot_skills: Path) -> None:
    """singlebox per-bot skills/ 骨架: skills-local 真目录 + skills-repo symlink.

    Idempotent — 已存在或已 symlink 则跳过。

    Args:
        per_bot_skills: per-bot 的 skills/ 根 (OSS-view, 形如
          ``/aidesktop/aidesktop_singlebox/.../<bot>/openclaw/workspace/skills``)。
          函数内部翻译为宿主真实路径再操作。

    线上 / desktop bot 不会调到这里 (caller 已用 is_local_mode() 守门)。
    """
    host_per_bot = _oss_view_to_host(per_bot_skills)
    if host_per_bot is None:
        logger.warning(
            "[ensure_per_bot_skills_skeleton] LOCAL_AIDESKTOP_ROOT not set — "
            "skip skeleton for %s (skill 关联会失效)",
            per_bot_skills,
        )
        return
    per_bot_skills = host_per_bot
    try:
        per_bot_skills.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("[ensure_per_bot_skills_skeleton] mkdir %s failed: %s", per_bot_skills, e)
        return

    # skills-local: 真目录
    local = per_bot_skills / "skills-local"
    if not local.exists():
        try:
            local.mkdir(parents=True, exist_ok=True)
            logger.info("[ensure_per_bot_skills_skeleton] mkdir skills-local: %s", local)
        except OSError as e:
            logger.warning("[ensure_per_bot_skills_skeleton] mkdir skills-local %s failed: %s", local, e)

    # skills-repo: symlink → host source (~/aiworkbench/skills-repo)
    repo = per_bot_skills / "skills-repo"
    source = _find_per_bot_repo_source()
    if source is None:
        # source 不存在 → 兜底建空目录, skill 关联会失败但不阻塞 bot 启动
        if not repo.exists():
            try:
                repo.mkdir(parents=True, exist_ok=True)
                logger.warning(
                    "[ensure_per_bot_skills_skeleton] no host skills-repo source — "
                    "created empty %s (skill 激活会失败)",
                    repo,
                )
            except OSError:
                pass
        return

    if repo.is_symlink() and repo.resolve(strict=False) == source.resolve():
        return  # 已经对的 symlink, 幂等

    # 旧空目录 / 错指 symlink — 清掉重建
    if repo.is_symlink() or repo.is_file():
        try:
            repo.unlink()
        except OSError:
            pass
    elif repo.is_dir():
        # 真目录: 只 rmtree 空目录 (避免误删 dev 期间手动放的内容)
        try:
            repo.rmdir()  # 非空会 raise OSError, 安全
        except OSError:
            logger.warning(
                "[ensure_per_bot_skills_skeleton] %s is non-empty dir — skip symlink",
                repo,
            )
            return

    try:
        repo.symlink_to(source)
        logger.info("[ensure_per_bot_skills_skeleton] symlink %s → %s", repo, source)
    except OSError as e:
        logger.warning("[ensure_per_bot_skills_skeleton] symlink %s → %s failed: %s", repo, source, e)


class WorkspacePathFactory:
    """路径工厂，委托给模块级纯函数。

    是否走主机本地共享 skills 根目录，由注入的
    :class:`SkillRepoSyncPlugin.get_local_skills_root` 决定：
    - 返回非 ``None``（local 实现）→ skills-local/skills-repo 走主机根 + 后缀。
    - 返回 ``None``（prod 实现）→ 按 entity/bot 计算每 bot 的 OSS-view 路径。
    """

    @inject
    def __init__(self, skill_repo_sync: SkillRepoSyncPlugin) -> None:
        self._skill_repo_sync = skill_repo_sync

    def get_entity_identity_dir(
        self, entity_id: str, entity_type: str = "staff", engine_type: str = "openclaw"
    ) -> Path:
        return _maybe_local_translate(get_entity_identity_dir(entity_id, entity_type, engine_type))

    def get_bot_engine_dir(
        self, entity_id: str, bot_id: str, engine_type: str = "openclaw", entity_type: str = "staff"
    ) -> Path:
        return _maybe_local_translate(get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type))

    def get_bolt_engine_config_dir(
        self, entity_id: str, bot_id: str, engine_type: str = "openclaw", entity_type: str = "staff"
    ) -> Path:
        return _maybe_local_translate(get_bot_engine_config_dir(entity_id, bot_id, engine_type, entity_type))

    def get_bot_nas_dir(
        self, entity_id: str, bot_id: str, engine_type: str, entity_type: str = "staff"
    ) -> Path:
        # NAS 路径 (/home/admin/.merge_nas/...) 跟 aidesktop 无关, 不翻译。
        return get_bot_nas_dir(entity_id, bot_id, engine_type, entity_type)

    def get_bot_nas_storage_id(
        self, entity_id: str, bot_id: str, engine_type: str, entity_type: str = "staff"
    ) -> str:
        return get_bot_nas_storage_id(entity_id, bot_id, engine_type, entity_type)

    def get_bot_workspace_dir(
        self, entity_id: str, bot_id: str, engine_type: str = "openclaw", entity_type: str = "staff"
    ) -> Path:
        return _maybe_local_translate(get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type) / "workspace")

    def get_bot_data_dir(
        self, entity_id: str, bot_id: str, engine_type: str = "openclaw", entity_type: str = "staff"
    ) -> Path:
        return self.get_bot_workspace_dir(entity_id, bot_id, engine_type, entity_type) / "data"

    def get_engine_workspace_data_dir(
        self, entity_id: str, bot_id: str, engine_type: str = "openclaw", entity_type: str = "staff"
    ) -> Path:
        """Engine workspace data directory.

        通过注入的 ``SkillRepoSyncPlugin`` 探测 host-side 共享 root：
        - 返回非 ``None`` (local) → 共享的 ``~/.moltis/workspace/data`` 路径，
          所有 engine 共享同一目录（local 模式无 per-bot 隔离）。
        - 返回 ``None`` (prod) → per-bot per-engine 目录。
        """
        if self._skill_repo_sync.get_local_skills_root() is not None:
            return SQLITE_PERSONAL_ROOT / "workspace" / "data"
        return get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type)

    def get_bot_skills_dir(
        self, entity_id: str, bot_id: str, engine_type: str = "openclaw", entity_type: str = "staff"
    ) -> Path:
        # singlebox 多 bot 改造: LOCAL 不再走 SHARED_ROOT, 直接走 PER_BOT, 避免多 bot
        # 共宿主时撞同一个 ~/.openclaw/workspace/skills 根。
        # 注意: backend 内部用 host path (用于 mkdir/写文件);
        # engine adapter 用 OSS-view path (由 get_symlink_mappings 自己拼装,
        # 不走 path_factory)。
        from agentclaw.community.utils.env_utils import is_local_mode
        if is_local_mode():
            oss_view = get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type) / "workspace" / "skills"
            host = _oss_view_to_host(oss_view) or oss_view
            logger.info(
                "[path_factory.get_bot_skills_dir] entity=%s bot=%s engine=%s → PER_BOT(LOCAL) host=%s",
                entity_id, bot_id, engine_type, host,
            )
            # Lazy 骨架: skills-local 真目录 (bot 私有 skill 上传内容) +
            # skills-repo symlink → ~/aiworkbench/skills-repo (全局 git repo).
            # idempotent: 已存在/已 symlink 则跳过。
            _ensure_per_bot_skills_skeleton(oss_view)
            return host
        local_root = self._skill_repo_sync.get_local_skills_root()
        if local_root is not None:
            logger.warning(
                "[path_factory.get_bot_skills_dir] entity=%s bot=%s engine=%s → SHARED_ROOT %s "
                "(local_skills_root non-None,bot_id IGNORED — multi-bot 会冲撞共享根)",
                entity_id, bot_id, engine_type, local_root,
            )
            return local_root
        per_bot = get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type) / "workspace" / "skills"
        logger.info(
            "[path_factory.get_bot_skills_dir] entity=%s bot=%s engine=%s → PER_BOT %s",
            entity_id, bot_id, engine_type, per_bot,
        )
        return per_bot

    def get_bot_skills_local_dir(
        self,
        entity_id: str,
        bot_id: str,
        engine_type: str = "openclaw",
        entity_type: str = "staff",
        *,
        is_desktop: bool = False,
        is_teclaw: bool = False,
    ) -> Path:
        """Local skills directory.

        Modes (in order of precedence):
        0. teclaw bot (``is_teclaw=True``, ie. ``device_provider == "teclaw"``):
           the engine **owns** the files, so the backend records and addresses a
           teclaw local skill by a **minimal logical** path — just ``skills-local``
           (relative). A thin adapter
           (``teclaw_paths.to_local_skill_engine_path``) expands it to the engine
           namespace (``workspace/skills-local/...``) at the device-fs seam; the DB
           ``git_path`` stays ``local://skills-local/<name>``. No host/OSS-view
           layout applies. This takes precedence over the host-path branches below.
        1. Desktop bot (``is_desktop=True``, ie. ``ac_bots.bot_type == "desktop"``):
           engine-view path inside the VM
           (``/home/admin/.openclaw/workspace/skills/skills-local``). Backend writes
           via BaaS invoke-http; engine sees this path directly. No OSS-view layer
           in this link — engine ``_convert_path`` falls through passthrough.
        2. Shared host root (``skill_repo_sync.get_local_skills_root()`` returns
           non-``None``, ie. local-dev impl): root + ``skills-local``.
        3. Per-bot cloud OSS-view path (``get_local_skills_root()`` returns
           ``None``, ie. prod impl). ``service`` bots also live in this branch
           even when their ``device_provider == "baas"`` — desktop-vs-not is
           decided by ``bot_type``, not by ``device_provider``.
        """
        if is_teclaw:
            # Minimal logical path (engine owns the files). Literal "skills-local"
            # matches the other branches here; the canonical name lives at
            # ``config_compose.teclaw_paths.LOCAL_SKILLS_DIRNAME`` (not imported —
            # workspace must not depend on config_compose, which depends on it).
            result = Path("skills-local")
            logger.info(
                "[path_factory.get_bot_skills_local_dir] entity=%s bot=%s → TECLAW(logical) %s",
                entity_id, bot_id, result,
            )
            return result
        if is_desktop:
            result = BAAS_ENGINE_SKILLS_ROOT / "skills-local"
            logger.info(
                "[path_factory.get_bot_skills_local_dir] entity=%s bot=%s → DESKTOP_BOT %s",
                entity_id, bot_id, result,
            )
            return result
        # singlebox 多 bot 改造: LOCAL+non-desktop 不再走 SHARED_ROOT (用户上传的 skill
        # 必须按 bot 隔离), 直接 PER_BOT。skills-repo 那条仍走 SHARED_ROOT (NAS 全局 git
        # repo 是合理的共享)。
        from agentclaw.community.utils.env_utils import is_local_mode
        if is_local_mode():
            result = self.get_bot_skills_dir(entity_id, bot_id, engine_type, entity_type) / "skills-local"
            logger.info(
                "[path_factory.get_bot_skills_local_dir] entity=%s bot=%s engine=%s → PER_BOT(LOCAL+non-desktop) %s",
                entity_id, bot_id, engine_type, result,
            )
            return result
        local_root = self._skill_repo_sync.get_local_skills_root()
        if local_root is not None:
            result = local_root / "skills-local"
            logger.warning(
                "[path_factory.get_bot_skills_local_dir] entity=%s bot=%s engine=%s → SHARED_ROOT %s "
                "(local_skills_root non-None,bot_id IGNORED)",
                entity_id, bot_id, engine_type, result,
            )
            return result
        result = self.get_bot_skills_dir(entity_id, bot_id, engine_type, entity_type) / "skills-local"
        logger.info(
            "[path_factory.get_bot_skills_local_dir] entity=%s bot=%s engine=%s → PER_BOT %s",
            entity_id, bot_id, engine_type, result,
        )
        return result

    def get_bot_skills_repo_dir(
        self,
        entity_id: str,
        bot_id: str,
        engine_type: str = "openclaw",
        entity_type: str = "staff",
        *,
        is_desktop: bool = False,
    ) -> Path:
        """Skills repository directory.

        Same precedence as :meth:`get_bot_skills_local_dir`. See that method's
        docstring for the rationale on routing by ``bot_type == "desktop"``
        rather than ``device_provider == "baas"`` (service bots' device_provider
        is also baas but they use the cloud OSS-view path).
        """
        if is_desktop:
            result = BAAS_ENGINE_SKILLS_ROOT / "skills-repo"
            logger.info(
                "[path_factory.get_bot_skills_repo_dir] entity=%s bot=%s → DESKTOP_BOT %s",
                entity_id, bot_id, result,
            )
            return result
        local_root = self._skill_repo_sync.get_local_skills_root()
        if local_root is not None:
            result = local_root / "skills-repo"
            logger.info(
                "[path_factory.get_bot_skills_repo_dir] entity=%s bot=%s engine=%s → SHARED_ROOT %s "
                "(skills-repo 全局共享是合理的)",
                entity_id, bot_id, engine_type, result,
            )
            return result
        result = self.get_bot_skills_dir(entity_id, bot_id, engine_type, entity_type) / "skills-repo"
        logger.info(
            "[path_factory.get_bot_skills_repo_dir] entity=%s bot=%s engine=%s → PER_BOT %s",
            entity_id, bot_id, engine_type, result,
        )
        return result


def _get_rsync_target_dir() -> Path:
    """rsync 目标目录（与旧 config.RSYNC_TARGET_DIR 语义相同）。"""
    return get_global_skills_repo_dir()


RSYNC_TARGET_DIR = _get_rsync_target_dir()

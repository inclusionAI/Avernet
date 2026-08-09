"""Service Bot router.

Provides Bot build/migration API:
- POST /api/service-bot/build - Build a new bot from source bot
- GET /api/service-bot/read-only/tree - 查询沙箱目录树与只读规则

根据 README.md 分层规范：
- router 只处理 HTTP 协议关注点：路径参数、Query、Body 解析，HTTP 错误码映射
- 调用 core/service_bot/services/ 中的业务逻辑
- Request/Response 类型来自同级 schemas.py
"""
import fnmatch
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.service_bot.schemas import (
    ApiResponse,
    BotBuildRequest,
    BotRsyncExcludesResponse,
    ReadOnlyRuleItem,
    ReadOnlyTreeItem,
    ReadOnlyTreeResponse,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_build_service import BotBuildServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import CollaboratorPermissionInterceptor, with_interceptors
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildServiceError,
    BotBuildMigrationError,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry, ReadOnlyRule
from agentclaw.community.core.devices.services import device_info
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/service-bot", tags=["service-bot"])


@router.post(
    "/build",
    response_model=ApiResponse,
    summary="Bot 构建迁移",
)
async def build_bot(
    req: BotBuildRequest = ...,
    user: AuthenticatedUser = Depends(get_current_user),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    build_service: BotBuildServiceProtocol = Injected(BotBuildServiceProtocol),
) -> ApiResponse:
    """
    Bot 构建迁移。

    POST /api/service-bot/build
    Body: {
        "bot_id": "xxx",
        "version": "v1"
    }

    执行步骤：
    1. Step 1: 查询 AgentClaw 层 Bot 信息
    2. Step 2: Bot 实例迁移（rsync 复制 + MCP 配置生成）
    """
    try:
        user_id = user.staffId
        bot_id = req.bot_id

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[build_bot] Starting build: bot_id={bot_id}, "
            f"user_id={user_id}, version={req.version}"
        )

        bot = bot_service.get_bot(bot_id=bot_id, user_id=user_id)
        if not bot:
            return ApiResponse(success=False, message=f"Bot不存在: {bot_id}", error_code=404, data=None)

        result = build_service.build(
            bot=bot,
            version=req.version,
        )

        logger.info(
            f"[build_bot] Build completed: success={result.get('success')}, "
            f"migration_path={result.get('migration_path')}"
        )

        return ApiResponse(success=True, data=result, message="Bot构建迁移成功")

    except BotBuildMigrationError as e:
        logger.error(f"[build_bot] Migration failed: {e}")
        return ApiResponse(success=False, message=f"迁移失败: {str(e)}", error_code=500, data=None)
    except BotBuildServiceError as e:
        logger.error(f"[build_bot] Service error: {e}")
        return ApiResponse(success=False, message=f"Bot构建失败: {str(e)}", error_code=500, data=None)
    except Exception as e:
        logger.error(f"[build_bot] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"Bot构建失败: {str(e)}", error_code=500, data=None)


# ---------------------------------------------------------------------------
# Read-only rules / directory tree
# ---------------------------------------------------------------------------


def _normalize_read_only_tree_path(path: str) -> str:
    """Normalize and validate read-only tree query sub-path.

    The API accepts only relative paths under the engine sandbox base path.
    """
    if not path:
        return ""
    if "\x00" in path:
        raise ValueError(f"Invalid path: {path!r}")
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Invalid path: {path}")
    normalized = str(p)
    return "" if normalized == "." else normalized


def _match_readonly_rules(path: str, rules: list[dict]) -> bool:
    """Check if *path* matches any rule in *rules*.

    Supports exact match, directory-prefix match and glob patterns (e.g.
    ``*.md``, ``agents/*/agent/models.json``).
    """
    normalized_path = path.rstrip("/") or path
    for rule in rules:
        rule_path = str(rule.get("path", "")).rstrip("/")
        if not rule_path:
            continue
        rule_type = rule.get("rule_type", "file")
        # Exact match
        if normalized_path == rule_path:
            return True
        # Directory rules apply to descendants too. This matters when the UI
        # lazily queries a selected directory via the `path` query parameter.
        if rule_type == "directory" and normalized_path.startswith(f"{rule_path}/"):
            return True
        # Glob match
        if rule_type == "glob" and fnmatch.fnmatch(normalized_path, rule_path):
            return True
    return False


def _materialize_rule(rule: ReadOnlyRule, base_path: str) -> dict:
    """将 provider 规则转为可匹配的绝对路径规则。"""
    rule_path = rule.path
    if rule_path.startswith("/"):
        abs_path = rule_path
    else:
        abs_path = f"{base_path.rstrip('/')}/{rule_path}"
    return {"path": abs_path, "rule_type": rule.rule_type}


@router.get(
    "/read-only/tree",
    response_model=ApiResponse,
    summary="查询沙箱目录树与只读规则",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_read_only_tree(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot owner ID"),
    path: str = Query("", description="Path"),
    recursive: bool = Query(False, description="是否递归列出子目录"),
    user: AuthenticatedUser = Depends(get_current_user),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    registry: EngineSandboxRegistry = Injected(EngineSandboxRegistry),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> ApiResponse:
    """查询沙箱中 ~/.openclaw 下的目录树，同时返回默认和用户自定义的只读规则。

    GET /api/service-bot/read-only/tree?bot_id=xxx&recursive=false
    """
    try:
        operator_id = user.staffId
        if not operator_id or operator_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        normalized_path = _normalize_read_only_tree_path(path)

        # 1. 获取 Bot 信息（含 ext 字段，用于读取自定义只读规则）
        bot = bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
        if not bot:
            return ApiResponse(success=False, message=f"Bot不存在: {bot_id}", error_code=404, data=None)

        # 2. 读取用户自定义只读规则
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            import json as _json
            try:
                ext = _json.loads(ext)
            except _json.JSONDecodeError:
                ext = {}
        custom_rules_raw = ext.get("read_only_rules", [])
        if not isinstance(custom_rules_raw, list):
            custom_rules_raw = []
        custom_rules = [
            ReadOnlyRuleItem(path=r["path"], rule_type=r.get("rule_type", "file"))
            for r in custom_rules_raw
            if isinstance(r, dict) and "path" in r
        ]

        # 3. 解析引擎 provider 与默认只读规则
        engine_type = resolve_engine_for_bot(bot_id, owner_id, bot_repo=bot_repo)
        provider = registry.resolve(engine_type)

        base_path = provider.get_base_path()
        default_rule_objs = provider.get_default_read_only_rules()
        default_rules_raw = [_materialize_rule(r, base_path) for r in default_rule_objs]
        default_rules = [
            ReadOnlyRuleItem(path=r["path"], rule_type=r["rule_type"])
            for r in default_rules_raw
        ]

        # 4. 列出目录树
        # The provider dispatches on the presence of ``device_fs``:
        # arca binding → device-fs (remote query); absent → walk the local
        # filesystem at ``base_path`` (application-dev.yaml points it at the
        # dev's home; prod leaves it at the sandbox mount). No mode flag.
        device_provider, sandbox_id = device_info.get_device_info(bot_id, owner_id, bot_repo)
        device_fs = (
            device_fs_dispatcher.for_bot(bot_id, owner_id)
            if device_provider == "arca"
            else None
        )
        items_data = await provider.list_directory(
            sub_path=normalized_path,
            recursive=recursive,
            device_fs=device_fs,
        )

        items: list[ReadOnlyTreeItem] = []
        for item in items_data:
            abs_path = f"{base_path.rstrip('/')}/{item.path}" if item.path else base_path
            items.append(ReadOnlyTreeItem(
                name=item.name,
                path=item.path,
                is_dir=item.is_dir,
                is_readonly_default=_match_readonly_rules(abs_path, default_rules_raw),
                is_readonly_custom=_match_readonly_rules(abs_path, custom_rules_raw),
            ))

        if device_fs is None and not items_data:
            logger.info(
                f"[get_read_only_tree] No remote sandbox binding for bot_id={bot_id} "
                "and local root produced no entries; returning rules only"
            )

        result = ReadOnlyTreeResponse(
            base_path=base_path,
            items=items,
            default_rules=default_rules,
            custom_rules=custom_rules,
        )

        return ApiResponse(success=True, data=result.model_dump(), message="OK")

    except ValueError as e:
        logger.warning(f"[get_read_only_tree] Invalid path: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)
    except Exception as e:
        logger.error(f"[get_read_only_tree] Error: {e}")
        return ApiResponse(success=False, message=f"查询目录树失败: {str(e)}", error_code=500, data=None)


# ---------------------------------------------------------------------------
# Rsync excludes configuration
# ---------------------------------------------------------------------------


@router.get(
    "/rsync-excludes",
    response_model=ApiResponse,
    summary="获取Bot的rsync excludes配置",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作
))
async def get_bot_rsync_excludes(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
) -> ApiResponse:
    """获取Bot的rsync excludes配置信息。

    返回三部分信息：
    1. default_excludes: 引擎默认的rsync排除规则
    2. custom_excludes: Bot ext中配置的自定义排除规则
    3. merged_excludes: 合并后的最终排除规则（默认值+自定义项，去重）

    GET /api/service-bot/rsync-excludes?bot_id=xxx&owner_id=xxx
    """
    try:
        # 1. 获取bot信息
        bot = bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
    except Exception as e:
        # Bot不存在时会抛出异常
        if "not found" in str(e).lower() or "不存在" in str(e):
            return ApiResponse(
                success=False,
                message=f"Bot不存在: {bot_id}",
                error_code=404,
                data=None
            )
        # 其他异常返回500
        logger.error(f"[get_bot_rsync_excludes] Error getting bot: {e}")
        return ApiResponse(
            success=False,
            message=f"获取rsync excludes配置失败: {str(e)}",
            error_code=500,
            data=None
        )

    if not bot:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None
        )

    try:

        # 2. 解析引擎类型
        engine_type = resolve_engine_for_bot(bot_id, owner_id, bot_repo=bot_repo)

        # 3. 获取引擎默认配置
        from agentclaw.community.core.workspace.engines.openclaw import _OPENCLAW_RSYNC_EXCLUDES
        from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_RSYNC_EXCLUDES

        if engine_type == "openclaw":
            default_excludes = list(_OPENCLAW_RSYNC_EXCLUDES)
        elif engine_type == "claude_code":
            default_excludes = list(_CLAUDE_CODE_RSYNC_EXCLUDES)
        else:
            default_excludes = []

        # 4. 解析Bot自定义配置
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext
        ext = bot.get("ext")
        custom_excludes = parse_build_rsync_excludes_from_ext(ext)

        # 5. 计算合并结果
        merged_excludes = list(default_excludes)
        if custom_excludes:
            for item in custom_excludes:
                if item not in merged_excludes:
                    merged_excludes.append(item)

        # 6. 构造响应
        result = BotRsyncExcludesResponse(
            bot_id=bot_id,
            engine_type=engine_type,
            default_excludes=default_excludes,
            custom_excludes=custom_excludes,
            merged_excludes=merged_excludes,
            excludes_source="default_only" if not custom_excludes else "default_plus_custom"
        )

        return ApiResponse(success=True, data=result.model_dump(), message="OK")

    except Exception as e:
        logger.error(f"[get_bot_rsync_excludes] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取rsync excludes配置失败: {str(e)}",
            error_code=500,
            data=None
        )

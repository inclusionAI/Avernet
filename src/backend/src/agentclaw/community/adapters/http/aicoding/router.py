"""
AICODING API routes — bindings management + workspace management.

Bindings: manages per-bot AI Coding bindings JSON file.
Workspace: workspace initialization, environment check, git clone.
"""
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from agentclaw.community.adapters.http.aicoding.schemas import (
    AiCodingBindingsContent,
    AiCodingBindingsResponse,
    AiCodingBindingsUpdateResponse,
    CodefuseTokenRequest,
    InitializeWorkspaceRequest,
    WorkflowItem,
    WorkflowListResponse,
)
from agentclaw.community.adapters.http.dependencies import get_request_context, RequestContext
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.api.workflow_catalog_service import WorkflowCatalogServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotServiceError,
)
from agentclaw.community.core.bot_management import codefuse_token as _codefuse_token
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.services.identity import VALID_ENTITY_TYPES
from agentclaw.community.api.workspace_service import WorkspaceServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.devices.services import device_info as device_info_lookup
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher

log = logging.getLogger("aicoding-api")

router = APIRouter(prefix="/api/aicoding", tags=["aicoding"])

# ──────────────── Constants ────────────────

BINDINGS_FILE_NAME = "aicoding.bindings.json"
DEFAULT_BINDINGS_CONTENT = '{"code_repos": [], "dima_spaces": []}'
ENGINE_TYPE = "aicoding"

# ──────────────── Helpers ────────────────


def _validate_entity_type(entity_type: str) -> None:
    if entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity_type: {entity_type}. Must be one of: {VALID_ENTITY_TYPES}",
        )


def _validate_json(content: str) -> None:
    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON for {BINDINGS_FILE_NAME}: {e}",
        )


def _get_bindings_path(
    entity_type: str, entity_id: str, bot_id: str, path_factory: WorkspacePathFactory,
) -> Path:
    bot_engine_dir = path_factory.get_bot_engine_dir(entity_id, bot_id, ENGINE_TYPE, entity_type)
    return bot_engine_dir / "workspace" / BINDINGS_FILE_NAME


async def _read(file_path: Path, bot_id: str, owner_id: str, bot_repo, resolver, dispatcher) -> str:
    try:
        device_provider, sandbox_id = device_info_lookup.get_device_info(bot_id, owner_id, bot_repo)
        if device_provider == "arca" and sandbox_id:
            ctx = resolver.resolve_for_bot(bot_id, owner_id)
            device_fs = dispatcher.dispatch(ctx)
            content_bytes = await device_fs.read_file(str(file_path))
            return content_bytes.decode("utf-8") if content_bytes else ""
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return ""
    except Exception as e:
        log.error(f"[aicoding._read] Error reading {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")


async def _write(file_path: Path, content: str, bot_id: str, owner_id: str, bot_repo, resolver, dispatcher) -> None:
    try:
        device_provider, sandbox_id = device_info_lookup.get_device_info(bot_id, owner_id, bot_repo)
        if device_provider == "arca" and sandbox_id:
            ctx = resolver.resolve_for_bot(bot_id, owner_id)
            device_fs = dispatcher.dispatch(ctx)
            await device_fs.write_file(str(file_path), content.encode("utf-8"))
            return
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except Exception as e:
        log.error(f"[aicoding._write] Error writing {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write file: {str(e)}")


# ──────────────── Endpoints ────────────────


@router.get(
    "/{entity_type}/{entity_id}/bot/{bot_id}/" + BINDINGS_FILE_NAME,
    response_model=AiCodingBindingsResponse,
)
async def get_bot_bindings(
    entity_type: str,
    entity_id: str,
    bot_id: str,
    user_id: Optional[str] = Query(None, description="Operator user ID; defaults to current user"),
    ctx: RequestContext = Depends(get_request_context),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> AiCodingBindingsResponse:
    operator_id = user_id or ctx.user_id
    log.info(
        f"[aicoding.get_bot_bindings] operator={operator_id}, entity={entity_type}/{entity_id}, bot={bot_id}"
    )
    _validate_entity_type(entity_type)
    file_path = _get_bindings_path(entity_type, entity_id, bot_id, path_factory)
    content = await _read(file_path, bot_id, operator_id, bot_repo, resolver, device_fs_dispatcher)
    if not content:
        content = DEFAULT_BINDINGS_CONTENT
    return AiCodingBindingsResponse(
        success=True,
        file_type=BINDINGS_FILE_NAME,
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        content=content,
        file_path=str(file_path),
    )


@router.put(
    "/{entity_type}/{entity_id}/bot/{bot_id}/" + BINDINGS_FILE_NAME,
    response_model=AiCodingBindingsUpdateResponse,
)
async def update_bot_bindings(
    entity_type: str,
    entity_id: str,
    bot_id: str,
    request: AiCodingBindingsContent,
    user_id: Optional[str] = Query(None, description="Operator user ID; defaults to current user"),
    ctx: RequestContext = Depends(get_request_context),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> AiCodingBindingsUpdateResponse:
    operator_id = user_id or ctx.user_id
    log.info(
        f"[aicoding.update_bot_bindings] operator={operator_id}, entity={entity_type}/{entity_id}, bot={bot_id}"
    )
    _validate_entity_type(entity_type)
    _validate_json(request.content)
    file_path = _get_bindings_path(entity_type, entity_id, bot_id, path_factory)
    await _write(file_path, request.content, bot_id, operator_id, bot_repo, resolver, device_fs_dispatcher)
    return AiCodingBindingsUpdateResponse(
        success=True,
        message=f"{entity_type}/{entity_id}/bot/{bot_id} {BINDINGS_FILE_NAME} updated successfully",
        file_type=BINDINGS_FILE_NAME,
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        file_path=str(file_path),
    )


# ── Workspace Endpoints ────────────────────────────────────────────────────

# 工作区路径前缀校验
WORKSPACE_PATH_PREFIX = "/workspace"


@router.post("/workspace/initialize")
async def initialize_workspace(
    req: InitializeWorkspaceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: WorkspaceServiceProtocol = Injected(WorkspaceServiceProtocol),
):
    """初始化工作区（含可选 Git 克隆）

    工作区路径: {bolt_base}/{entity_type}_{entity_id}/{bot_id}/aicoding{path}
    path 必须以 /workspace 开头
    """
    # 校验 path 必须以 /workspace 开头
    if not req.path.startswith(WORKSPACE_PATH_PREFIX):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: must start with '{WORKSPACE_PATH_PREFIX}'"
        )

    # 安全校验 path（防止路径遍历攻击）
    dangerous_patterns = ["..", "~", "\n", "\r"]
    for pattern in dangerous_patterns:
        if pattern in req.path:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid path: contains '{pattern}'"
            )

    # 安全校验 git_url
    dangerous_chars = [";", "|", "&", "$", "`", "\n", "\r"]

    if req.git_url:
        git_url = req.git_url.strip()
        for char in dangerous_chars:
            if char in git_url:
                raise HTTPException(
                    status_code=400, detail=f"Invalid git_url: contains '{char}'"
                )
        # 仅支持 HTTPS
        if not git_url.startswith("https://"):
            raise HTTPException(
                status_code=400, detail="Only HTTPS URLs are supported"
            )

    result = await service.initialize_workspace(
        user_id=user.staffId,
        bot_id=req.bot_id,
        path=req.path,
        entity_type=req.entity_type or "staff",
        git_url=req.git_url,
        branch=req.branch,
    )

    if result.get("error"):
        return {"success": False, "message": result["error"], "data": result}

    return {"success": True, "data": result}


# ── Workflow Catalog Endpoints ────────────────────────────────────────────


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    branch: Optional[str] = Query(
        None,
        description="workflows 仓库分支，默认 master；联调阶段可传 feat 分支",
        min_length=1,
        max_length=200,
    ),
    catalog: WorkflowCatalogServiceProtocol = Injected(WorkflowCatalogServiceProtocol),
) -> WorkflowListResponse:
    """列出 workflows 仓库下所有工作流（name + description）。

    仓库结构：workflows/{business|infra}/{域}/{name}/workflow.yaml
    内部已做聚合并缓存：tree 短缓存 60s；单个 yaml 按 blob SHA 长缓存，
    git 一旦更新 SHA 必变，会自动失效。
    """
    items = await catalog.list_workflows(branch=branch)
    return WorkflowListResponse(
        success=True,
        data=[WorkflowItem(**item) for item in items],
    )


# ── DIMA Workspace Endpoints ──────────────────────────────────────────────


class DimaWorkspaceResponse(BaseModel):
    """统一响应：DIMA 工作空间创建结果。"""

    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[dict[str, Any]] = None


@router.post(
    "/bot/{bot_id}/dima-workspace",
    response_model=DimaWorkspaceResponse,
)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$user_id",
))
async def create_bot_dima_workspace(
    bot_id: str,
    user_id: Optional[str] = Query(None, description="Bot owner user ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> DimaWorkspaceResponse:
    """为 applicationCoding bot 创建 DIMA 工作空间（幂等）。

    使用场景：bot 创建时 DIMA 调用失败，前端检测到 template_config 中
    缺少 ``dima_space_id``，可调用本接口手动触发创建。

    幂等：已存在 ``dima_space_id`` 时直接返回该 ID，不会重复创建。

    权限：bot owner 或 collaborator。``user_id`` 用于权限校验时定位 bot
    所有者；不传时默认使用当前登录用户。
    """
    operator_id = ctx.user_id
    if not operator_id or operator_id == "anonymous":
        return DimaWorkspaceResponse(
            success=False, message="无法获取用户信息", error_code=400, data=None,
        )

    resolved_user_id = user_id or operator_id
    log.info(
        "[aicoding.create_bot_dima_workspace] operator=%s, user_id=%s, bot=%s",
        operator_id, resolved_user_id, bot_id,
    )

    try:
        workspace_id = bot_service.ensure_hosted_workspace(bot_id, resolved_user_id)
    except BotNotFoundError:
        return DimaWorkspaceResponse(
            success=False, message=f"Bot不存在: {bot_id}", error_code=404, data=None,
        )
    except BotServiceError as e:
        return DimaWorkspaceResponse(
            success=False, message=str(e), error_code=400, data=None,
        )
    except Exception as e:
        log.error(
            "[aicoding.create_bot_dima_workspace] Failed for bot %s: %s",
            bot_id, e, exc_info=True,
        )
        # 透出 DIMA 原始错误（如空间名称已被占用、配额超限等），便于前端展示
        return DimaWorkspaceResponse(
            success=False, message=str(e), error_code=502, data=None,
        )

    if not workspace_id:
        return DimaWorkspaceResponse(
            success=False,
            message="DIMA 工作空间创建失败，请稍后重试",
            error_code=500,
            data=None,
        )

    return DimaWorkspaceResponse(
        success=True,
        data={"dima_space_id": workspace_id},
    )


# ──────────────── CodeFuse Token ────────────────

# CodeFuse token 解码 / 写入命令构建已下沉到 ``core.bot_management.codefuse_token``
# 供 device 层复用；这里保留薄封装，把解码的 ValueError 转成 HTTP 400，端点行为不变。
# ``_CODEFUSE_JSON_PATH`` 作为 re-export 保留（既有调用方 / 测试仍从本模块引用）。
_CODEFUSE_JSON_PATH = _codefuse_token.CODEFUSE_JSON_PATH


def _decode_auth_code(auth_code: str) -> tuple[str, str]:
    """Decode a CodeFuse SSO auth_code into (token, workid)；失败转 HTTP 400。"""
    try:
        return _codefuse_token.decode_auth_code(auth_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _build_codefuse_write_cmd(token: str, workid: str, owner_id: str = "") -> str:
    """构建写 codefuse.json 的 shell 命令（逻辑已下沉到 ``core.bot_management.codefuse_token``）。

    ``owner_id`` 为历史签名保留参数，实际未使用。
    """
    return _codefuse_token.build_codefuse_write_cmd(token, workid)


@router.put("/bots/{bot_id}/codefuse/auth")
async def save_codefuse_token(
    bot_id: str,
    request: CodefuseTokenRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    bot_repo: BotRepository = Injected(BotRepository),
    device_repo: DeviceBindingRepository = Injected(DeviceBindingRepository),
    baas_service: BaasServiceProtocol = Injected(BaasServiceProtocol),
    device_service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> dict:
    """Write CodeFuse token into the bot's container.

    Only the bot owner can call this endpoint.

    The ``token`` field is a base64-encoded auth_code from CodeFuse SSO callback.
    Decoded it yields ``{"t":"<token>","w":"<workid>"}``.  The endpoint:

    1. Decodes auth_code → extracts token + workid
    2. Validates token is non-empty, ≥16 chars, hex format
    3. Writes into ``/home/admin/.codefuse/fuse/codefuse.json`` (merges with
       existing file, forces token/workid/authType update)
    4. Verifies the file is readable
    """
    owner_id = user.staffId

    # ── Ownership check ──
    bot = bot_repo.get_by_id_and_owner(bot_id, owner_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot not found or no permission: {bot_id}")

    binding_id = bot.get("binding_id")
    if not binding_id:
        raise HTTPException(status_code=400, detail="Bot has no device binding")

    binding = device_repo.get_by_id(int(binding_id))
    if not binding:
        raise HTTPException(status_code=404, detail="Device binding not found")

    # ── Decode & validate auth_code ──
    token, workid = _decode_auth_code(request.token)
    cmd = _build_codefuse_write_cmd(token, workid, owner_id)

    # ── Execute command via the correct provider ──
    provider = binding.device_provider
    device_id = binding.device_id

    if provider == "baas":
        # BaaS / poolab: use bot-level exec API
        bot_uuid = (binding.device_props or {}).get("bot_uuid")
        if not bot_uuid:
            raise HTTPException(
                status_code=400, detail="Bot has no BaaS bot_uuid in device_props"
            )
        try:
            result = baas_service.exec_command_on_bot(
                bot_uuid=bot_uuid, cmd=cmd, timeout_seconds=30
            )
        except Exception as e:
            log.error("[save_codefuse_token] exec_command_on_bot failed: bot_uuid=%s error=%s", bot_uuid, e)
            raise HTTPException(status_code=502, detail=f"Failed to write token to container: {e}")

        exit_code = result.get("exit_code", -1) if isinstance(result, dict) else -1
        if exit_code != 0:
            log.warning("[save_codefuse_token] non-zero exit: bot_uuid=%s result=%s", bot_uuid, result)
            raise HTTPException(status_code=502, detail=f"Command exited with code {exit_code}")
    else:
        # Arca / local: use DeviceService.exec_shell (routed by device_id)
        try:
            device_service.exec_shell(device_id=device_id, shell_cmd=cmd)
        except Exception as e:
            log.error("[save_codefuse_token] exec_shell failed: device_id=%s error=%s", device_id, e)
            raise HTTPException(status_code=502, detail=f"Failed to write token to container: {e}")

    log.info("[save_codefuse_token] token written: bot_id=%s provider=%s", bot_id, provider)
    return {"success": True, "bot_id": bot_id, "provider": provider}

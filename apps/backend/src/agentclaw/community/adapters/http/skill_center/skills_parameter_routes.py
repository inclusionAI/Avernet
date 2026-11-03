"""Legacy skills parameter routes."""

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    SaveSkillParametersRequest,
    SkillParametersResponse,
)
from agentclaw.community.api.skill_parameter_service_factory import (
    SkillParameterServiceFactoryProtocol,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    with_interceptors,
)
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from fastapi import (
    Depends,
    HTTPException,
    Query,
)

from .skills_common import (
    _create_skill_parameter_service,
    _extract_parameter_permission,
    _resolve_parameter_bot,
    _resolve_teclaw_local_skill,
    _SkillParameterPermissionInterceptor,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.get("/{skill_id}/parameters", response_model=SkillParametersResponse)
@with_interceptors(
    _SkillParameterPermissionInterceptor(
        params_extractor=_extract_parameter_permission,
        extractor_params={"bot_id": "$bot_id", "entity_id": "$entity_id"},
        persist_audit_log=False,
    )
)
async def get_skill_parameters(
    skill_id: str,
    entity_id: str = Query(..., description="Entity ID (e.g., staff_xxx, proj_xxx)"),
    bot_id: str = Query(..., description="Bot ID"),
    engine_type: str = Query(..., description="Engine type (openclaw)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    skill_repo: SkillRepository = Injected(SkillRepository),
    parameter_service_factory: SkillParameterServiceFactoryProtocol = Injected(
        SkillParameterServiceFactoryProtocol
    ),
) -> SkillParametersResponse:
    """获取用户对技能的参数配置（全局，不绑定 skill_set）"""
    # 获取 skill_name
    skill = skill_repo.get_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    bot = _resolve_parameter_bot(
        skill=skill,
        requested_bot_id=bot_id,
        requested_entity_id=entity_id,
        bot_repo=bot_repo,
    )
    skill_name = skill.get("link_name") or skill.get("name")

    # 使用异步工厂函数获取参数服务
    parameter_service = await _create_skill_parameter_service(
        parameter_service_factory=parameter_service_factory,
        bot_id=str(bot["bot_id"]),
        owner_id=str(bot["owner_id"]),
    )
    parameters = parameter_service.get_skill_parameters(skill_name)

    return SkillParametersResponse(success=True, data={"parameters": parameters})


@router.post("/{skill_id}/parameters", response_model=SkillParametersResponse)
@with_interceptors(
    _SkillParameterPermissionInterceptor(
        params_extractor=_extract_parameter_permission,
        extractor_params={"bot_id": "$bot_id", "entity_id": "$entity_id"},
        # Keep edit-lock enforcement and audit metadata, but never serialize
        # credential-bearing parameter values.
        audit_excluded_params={"request"},
    )
)
async def save_skill_parameters(
    skill_id: str,
    request: SaveSkillParametersRequest,
    entity_id: str = Query(..., description="Entity ID (e.g., staff_xxx, proj_xxx)"),
    bot_id: str = Query(..., description="Bot ID"),
    engine_type: str = Query(..., description="Engine type (openclaw)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_repo: SkillRepository = Injected(SkillRepository),
    parameter_service_factory: SkillParameterServiceFactoryProtocol = Injected(
        SkillParameterServiceFactoryProtocol
    ),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
) -> SkillParametersResponse:
    """保存用户对技能的参数配置"""
    # 获取 skill_name 和 parameter_schema (从 SKILL.md 解析)
    skill = skill_repo.get_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    bot = _resolve_parameter_bot(
        skill=skill,
        requested_bot_id=bot_id,
        requested_entity_id=entity_id,
        bot_repo=bot_repo,
    )
    trusted_bot_id = str(bot["bot_id"])
    trusted_owner_id = str(bot["owner_id"])
    trusted_entity_id = str(bot.get("entity_id") or trusted_owner_id)
    trusted_engine_type = str(bot.get("active_engine") or engine_type)
    trusted_runtime_engine_type = resolve_runtime_engine_for_bot(
        bot_id=trusted_bot_id,
        owner_id=trusted_owner_id,
        bot_repo=bot_repo,
    )
    trusted_entity_type = str(bot.get("entity_type") or "staff")
    skill_name = skill.get("link_name") or skill.get("name")

    # Resolve is_desktop for path construction
    is_desktop = bot.get("bot_type") == "desktop"

    # teclaw owns its local-skill files: parse SKILL.md from the container via
    # device_fs; non-teclaw keeps the host-FS read (byte-identical).
    is_teclaw, local_skill_adapter = _resolve_teclaw_local_skill(
        resolver,
        trusted_bot_id,
        trusted_owner_id,
    )

    # 从 SKILL.md 实时解析 parameters 定义
    parameter_schema = []
    service = skill_service_factory.create(
        active_dir=path_factory.get_bot_skills_dir(
            trusted_entity_id,
            trusted_bot_id,
            trusted_runtime_engine_type,
            trusted_entity_type,
        ),
        repo_dir=path_factory.get_bot_skills_repo_dir(
            trusted_entity_id,
            trusted_bot_id,
            trusted_runtime_engine_type,
            trusted_entity_type,
            is_desktop=is_desktop,
        ),
        local_dir=path_factory.get_bot_skills_local_dir(
            trusted_entity_id,
            trusted_bot_id,
            trusted_runtime_engine_type,
            trusted_entity_type,
            is_desktop=is_desktop,
            is_teclaw=is_teclaw,
        ),
        local_skill_path_adapter=local_skill_adapter,
        entity_id=trusted_entity_id,
        bot_owner_id=trusted_owner_id,
        bot_id=trusted_bot_id,
        engine_type=trusted_engine_type,
    )
    if is_teclaw:
        skill_info = await service.parse_local_skill_config(
            skill.get("git_path", ""),
            trusted_bot_id,
            trusted_owner_id,
        )
    else:
        skill_info = service._parse_skill_from_git(skill.get("git_path", ""))
    if skill_info:
        parameter_schema = skill_info.get("config", [])

    # 校验必填项
    if parameter_schema:
        for param in parameter_schema:
            if param.get("required") and not request.parameters.get(param["name"]):
                raise HTTPException(
                    status_code=400, detail=f"参数 {param['label']} 是必填项"
                )

    # 使用异步工厂函数获取参数服务
    parameter_service = await _create_skill_parameter_service(
        parameter_service_factory=parameter_service_factory,
        bot_id=trusted_bot_id,
        owner_id=trusted_owner_id,
    )
    await parameter_service.save_skill_parameters(skill_name, request.parameters)

    return SkillParametersResponse(success=True, data={"saved": True})

"""Legacy skillsets Router and historical import compatibility surface."""

import time
from pathlib import Path
from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.legacy_bot_address import (
    legacy_bot,
    legacy_path_params,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    AddMCPRequest,
    AddMCPResponse,
    AddSkillsRequest,
    AddSkillsResponse,
    CLIInSetResponse,
    CreateSkillSetRequest,
    FixGitPathRequest,
    FixGitPathResponse,
    InitAndSyncResponse,
    MCPServerInSetResponse,
    MessageResponse,
    SetDefaultSkillsFastRequest,
    SetDefaultSkillsFastResponse,
    SetDefaultSkillsRequest,
    SetDefaultSkillsResponse,
    SetSkillSetActiveRequest,
    SetSkillSetActiveResponse,
    SkillInSetResponse,
    SkillInSetSummary,
    SkillSetDetailResponse,
    SkillSetListResponse,
    SkillSetMCPsResponse,
    SkillSetResourceItem,
    SkillSetResourcesResponse,
    SkillSetResponse,
    SkillSetSkillsResponse,
    SkillSetsWithMCPsResponse,
    SkillSetWithMCPsItem,
    UpdateSkillSetRequest,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_engine_for_bot,
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.mcp.errors import McpIdentityUnresolvedError
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    build_passport_resource_scope,
)
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
    passport_mcp_items_from_codes,
    resolve_mcp_identity_modes,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    recover_legacy_skill_set_scope,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.passport import PassportPlugin, extract_cli_items
from fastapi import APIRouter, Depends, HTTPException, Query

from .skillsets_collection_routes import (
    create_skill_set,
    delete_skill_set,
    get_skill_set,
    list_skill_set_resources,
    list_skill_sets,
    list_skill_sets_with_mcps,
    update_skill_set,
)
from .skillsets_common import (
    _LEGACY_SKILL_SET_BATCH_ERROR_MESSAGES,
    _LEGACY_SKILL_SET_BATCH_PARTIAL_CONFLICTS,
    _get_path_params,
    _get_skill_set_path_params,
    _is_legacy_skill_set_batch_partial_failure,
    _legacy_actor,
    _legacy_skill_set,
    _legacy_skill_set_batch_failure,
    logger,
)
from .skillsets_default_routes import (
    init_and_sync_default_skills,
    set_default_skills,
    set_default_skills_fast,
)
from .skillsets_delivery_routes import (
    remove_cli_from_default_skill_set,
    set_skillset_active,
    sync_skills_to_device,
)
from .skillsets_mcp_routes import (
    add_mcp_to_skill_set,
    fix_git_path,
    get_skill_set_mcps,
    remove_mcp_from_skill_set,
)
from .skillsets_member_routes import (
    add_skills_to_set,
    ensure_default_skill_set,
    get_default_skill_set,
    get_skill_set_skills,
    remove_skill_from_set,
)

__all__ = [
    "legacy_bot",
    "legacy_path_params",
    "Path",
    "time",
    "Optional",
    "APIRouter",
    "HTTPException",
    "Depends",
    "Query",
    "CreateSkillSetRequest",
    "UpdateSkillSetRequest",
    "AddSkillsRequest",
    "SetDefaultSkillsRequest",
    "SetDefaultSkillsFastRequest",
    "FixGitPathRequest",
    "AddMCPRequest",
    "SetSkillSetActiveRequest",
    "SkillInSetSummary",
    "SkillSetResponse",
    "SkillSetListResponse",
    "SkillSetDetailResponse",
    "SkillInSetResponse",
    "SkillSetSkillsResponse",
    "CLIInSetResponse",
    "MCPServerInSetResponse",
    "SkillSetResourceItem",
    "SkillSetResourcesResponse",
    "SkillSetWithMCPsItem",
    "SkillSetsWithMCPsResponse",
    "AddSkillsResponse",
    "MessageResponse",
    "InitAndSyncResponse",
    "SetDefaultSkillsResponse",
    "SetDefaultSkillsFastResponse",
    "FixGitPathResponse",
    "SkillSetMCPsResponse",
    "AddMCPResponse",
    "SetSkillSetActiveResponse",
    "recover_legacy_skill_set_scope",
    "WorkspacePathFactory",
    "get_request_context",
    "RequestContext",
    "CollaboratorPermissionInterceptor",
    "with_interceptors",
    "SkillSetRepository",
    "DEFAULT_ENGINE_TYPE",
    "BotRepository",
    "resolve_engine_for_bot",
    "resolve_runtime_engine_for_bot",
    "McpIdentityUnresolvedError",
    "filter_passport_mcp_codes",
    "passport_mcp_items_from_codes",
    "resolve_mcp_identity_modes",
    "build_passport_resource_scope",
    "CallerIdentityRepositoryProtocol",
    "Injected",
    "SkillServiceFactoryProtocol",
    "SkillSetServiceFactoryProtocol",
    "SkillSetManagementServiceProtocol",
    "LocalSkillNotFoundError",
    "SkillSetControlPlaneConflictError",
    "SkillSetControlPlaneNotFoundError",
    "DeviceContextResolver",
    "DeviceSyncDispatcher",
    "PassportPlugin",
    "extract_cli_items",
    "get_logger",
    "logger",
    "router",
    "_LEGACY_SKILL_SET_BATCH_PARTIAL_CONFLICTS",
    "_LEGACY_SKILL_SET_BATCH_ERROR_MESSAGES",
    "_is_legacy_skill_set_batch_partial_failure",
    "_legacy_skill_set_batch_failure",
    "_legacy_actor",
    "_legacy_skill_set",
    "_get_path_params",
    "_get_skill_set_path_params",
    "list_skill_sets",
    "create_skill_set",
    "list_skill_sets_with_mcps",
    "list_skill_set_resources",
    "get_skill_set",
    "update_skill_set",
    "delete_skill_set",
    "get_skill_set_skills",
    "add_skills_to_set",
    "remove_skill_from_set",
    "ensure_default_skill_set",
    "get_default_skill_set",
    "init_and_sync_default_skills",
    "set_default_skills",
    "set_default_skills_fast",
    "fix_git_path",
    "get_skill_set_mcps",
    "add_mcp_to_skill_set",
    "remove_mcp_from_skill_set",
    "remove_cli_from_default_skill_set",
    "sync_skills_to_device",
    "set_skillset_active",
]


from .skillsets_collection_routes import router as _collection_router
from .skillsets_member_routes import router as _member_router
from .skillsets_default_routes import router as _default_router
from .skillsets_mcp_routes import router as _mcp_router
from .skillsets_delivery_routes import router as _delivery_router

router = APIRouter(prefix="/api/skillsets", tags=["skillsets"], routes=[
    *_collection_router.routes,
    *_member_router.routes,
    *_default_router.routes,
    *_mcp_router.routes,
    *_delivery_router.routes,
])

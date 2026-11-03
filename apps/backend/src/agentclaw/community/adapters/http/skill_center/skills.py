"""Legacy skills Router and historical import compatibility surface."""

import asyncio
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.legacy_bot_address import (
    legacy_bot,
    legacy_path_params,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateSkillFailedItem,
    ActivateSkillSetRequest,
    ActivateSkillSetResponse,
    ActivateSkillsRequest,
    ActivateSkillsResponse,
    ActivateSkillsResults,
    ActivateSkillSuccessItem,
    ActiveSkillSetsResponse,
    ActiveSkillsResponse,
    AddSkillMemberRequest,
    BatchAddMembersResponse,
    BatchAddMembersResult,
    BatchAddSkillMembersRequest,
    CreateSkillRequest,
    CurrentSkillSetResponse,
    DeactivateSkillSetRequest,
    DeactivateSkillSetResponse,
    DownloadUrlResponse,
    FileContentResponse,
    FileStructureResponse,
    MarketListResponse,
    MarketSearchResponse,
    MarketTagsResponse,
    MarketTreeResponse,
    MessageResponse,
    PaginatedSkillListResponse,
    PublishSkillRequest,
    PublishStatusResponse,
    SaveSkillParametersRequest,
    SearchRequest,
    SearchResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillMemberListResponse,
    SkillMemberOperationResponse,
    SkillMemberResponse,
    SkillMetadataResponse,
    SkillParametersResponse,
    SkillReadmeResponse,
    SwitchSkillSetResponse,
    SyncSkillSetRequest,
    SyncSkillsRequest,
    SyncSkillsResponse,
    SyncSkillsResult,
    SyncStatusResponse,
    UpdateMCPDependenciesRequest,
    UpdateRiskTagsRequest,
    UpdateSkillMemberRoleRequest,
    UpdateSkillRequest,
    UploadSkillErrorResponse,
    UploadSkillResponse,
    VersionListResponse,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.repository_catalog_service import (
    RepositoryCatalogServiceProtocol,
)
from agentclaw.community.api.runtime_layout_probe_service import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeServiceProtocol,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.api.skill_member_service import SkillMemberServiceProtocol
from agentclaw.community.api.skill_parameter_service_factory import (
    SkillParameterServiceFactoryProtocol,
)
from agentclaw.community.api.skill_publish_service import SkillPublishServiceProtocol
from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.access.admin_scopes import skill_admin
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    InterceptedResponse,
    InterceptorContext,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.interceptor.extractors import (
    PermissionParams,
)
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_engine_for_bot,
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.config_compose.teclaw_paths import (
    to_local_skill_engine_path,
)
from agentclaw.community.core.devices.services.device_context import (
    BotNotFoundError,
    DeviceNotBoundError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillActiveError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillInvalidPackageError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillRuntimeUnavailableError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
    SkillAssetInUseError,
    SkillReferencedBySkillSetError,
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    recover_legacy_skill_set_scope,
)
from agentclaw.community.core.skill_center.skill_metadata import (
    SkillManifestError,
    SkillManifestErrorCode,
)
from agentclaw.community.core.skill_center.upload_error_codes import (
    SkillUploadErrorCode,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugin_api.sandbox_runtime import (
    SandboxRuntimeUnavailableError,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterMarketSearchRequest,
)
from agentclaw.community.utils.env_utils import get_current_env
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from .skills_activation_routes import (
    activate_skill_set,
    deactivate_skill_set,
    get_active_skill_sets,
    get_active_skills,
    get_current_skill_set,
    sync_skill_set,
)
from .skills_asset_routes import (
    delete_skill,
    get_skill,
    get_skill_id_by_link_name,
    update_skill,
    update_skill_mcp_dependencies,
    update_skill_risk_tags,
)
from .skills_common import (
    _EXTRA_FIELDS,
    _FILESYSTEM_LAYOUT_ENGINES,
    _MANIFEST_UPLOAD_ERROR_CODES,
    _PACKAGE_UPLOAD_ERROR_CODES,
    _PACKAGE_UPLOAD_MESSAGES,
    _RUNTIME_UPLOAD_ERROR_PATTERNS,
    _RUNTIME_UPLOAD_EXCEPTION_TYPES,
    _UPLOAD_SIGN_URL_EXPIRES,
    BOT_RUNTIME_UNAVAILABLE_MESSAGE,
    DEFAULT_ENGINE_TYPE,
    _build_upload_error_response,
    _build_uploaded_zip,
    _classify_upload_validation_error,
    _contains_zip_upload_error,
    _create_skill_parameter_service,
    _desktop_active_root_from_probe,
    _extract_parameter_permission,
    _extract_skill_mutation_permission,
    _FailClosedSkillMutationPermissionInterceptor,
    _get_path_params,
    _get_skill_by_id_or_link_name,
    _get_skill_set_path_params,
    _is_runtime_upload_error,
    _legacy_runtime_projection,
    _merge_skill_fields,
    _normalize_upload_error_message,
    _resolve_parameter_bot,
    _resolve_teclaw_local_skill,
    _SkillParameterPermissionInterceptor,
    _upload_error_chain,
    extract_from_skill_id,
    logger,
    parse_tags,
)
from .skills_market_routes import (
    activate_skills_batch,
    get_market_tree,
    get_sync_status,
    list_local_market_skills,
    list_market_skills,
    list_user_skills,
    search_market_skills,
    sync_market,
)
from .skills_member_routes import (
    add_skill_member,
    add_skill_members_batch,
    get_skill_members,
    remove_skill_member,
    update_skill_member_role,
)
from .skills_parameter_routes import (
    get_skill_parameters,
    save_skill_parameters,
)
from .skills_publication_routes import (
    get_market_tags_center,
    get_publish_status,
    get_skill_file_content,
    get_skill_file_structure,
    get_version_download_url,
    install_market_skill,
    list_skill_versions,
    publish_skill,
    publish_upgrade_skill,
    search_market_center,
)
from .skills_readme_routes import (
    get_skill_readme,
    sync_skills_from_git,
)
from .skills_upload_routes import (
    create_skill,
    list_skills,
    upload_skill,
)

__all__ = [
    "legacy_bot",
    "legacy_path_params",
    "asyncio",
    "io",
    "json",
    "os",
    "re",
    "time",
    "zipfile",
    "Path",
    "Any",
    "Dict",
    "Optional",
    "APIRouter",
    "Depends",
    "File",
    "Form",
    "HTTPException",
    "Query",
    "Request",
    "UploadFile",
    "run_in_threadpool",
    "get_request_context",
    "RequestContext",
    "AuthenticatedUser",
    "skill_admin",
    "get_current_user",
    "ActivateSkillFailedItem",
    "ActivateSkillSetRequest",
    "ActivateSkillSetResponse",
    "ActivateSkillsRequest",
    "ActivateSkillsResponse",
    "ActivateSkillsResults",
    "ActivateSkillSuccessItem",
    "ActiveSkillSetsResponse",
    "ActiveSkillsResponse",
    "AddSkillMemberRequest",
    "BatchAddMembersResponse",
    "BatchAddMembersResult",
    "BatchAddSkillMembersRequest",
    "CreateSkillRequest",
    "CurrentSkillSetResponse",
    "DeactivateSkillSetRequest",
    "DeactivateSkillSetResponse",
    "DownloadUrlResponse",
    "FileContentResponse",
    "FileStructureResponse",
    "MarketListResponse",
    "MarketSearchResponse",
    "MarketTagsResponse",
    "MarketTreeResponse",
    "MessageResponse",
    "PaginatedSkillListResponse",
    "PublishSkillRequest",
    "PublishStatusResponse",
    "SaveSkillParametersRequest",
    "SearchRequest",
    "SearchResponse",
    "SkillDetailResponse",
    "SkillListResponse",
    "SkillMemberListResponse",
    "SkillMemberOperationResponse",
    "SkillMemberResponse",
    "SkillMetadataResponse",
    "SkillParametersResponse",
    "SkillReadmeResponse",
    "SwitchSkillSetResponse",
    "SyncSkillSetRequest",
    "SyncSkillsRequest",
    "SyncSkillsResponse",
    "SyncSkillsResult",
    "SyncStatusResponse",
    "UpdateMCPDependenciesRequest",
    "UpdateRiskTagsRequest",
    "UpdateSkillMemberRoleRequest",
    "UpdateSkillRequest",
    "UploadSkillErrorResponse",
    "UploadSkillResponse",
    "VersionListResponse",
    "SkillUploadErrorCode",
    "SkillManifestError",
    "SkillManifestErrorCode",
    "recover_legacy_skill_set_scope",
    "BotServiceProtocol",
    "SkillSetManagementServiceProtocol",
    "LocalSkillDeleteServiceProtocol",
    "LocalSkillUploadServiceProtocol",
    "BotRepository",
    "resolve_engine_for_bot",
    "resolve_runtime_engine_for_bot",
    "LocalSkillActiveError",
    "LocalSkillEditBusyError",
    "LocalSkillEditLockUnavailableError",
    "LocalSkillEditPausedError",
    "LocalSkillInvalidPackageError",
    "LocalSkillLayoutRollbackError",
    "LocalSkillNotFoundError",
    "LocalSkillNotReadyError",
    "LocalSkillRuntimeSyncError",
    "LocalSkillRuntimeUnavailableError",
    "LocalSkillStorageError",
    "LocalSkillTooLargeError",
    "SkillAssetInUseError",
    "SkillReferencedBySkillSetError",
    "SkillRuntimeNameConflictError",
    "SkillSetControlPlaneConflictError",
    "SkillsPoolEditLockUnavailableError",
    "SkillsPoolEditPausedError",
    "SkillRepository",
    "WorkspacePathFactory",
    "Injected",
    "SkillMemberServiceProtocol",
    "SkillParameterServiceFactoryProtocol",
    "SkillPublishServiceProtocol",
    "RuntimeLayoutProbeResult",
    "RuntimeLayoutProbeServiceProtocol",
    "RuntimeLayoutProbeStatus",
    "SkillServiceFactoryProtocol",
    "DirectActivationServiceProtocol",
    "SkillQueryServiceProtocol",
    "RepositoryCatalogServiceProtocol",
    "to_local_skill_engine_path",
    "DeviceContextResolver",
    "BotNotFoundError",
    "DeviceNotBoundError",
    "get_logger",
    "DeviceAdapterEndpointNotFoundError",
    "DeviceAdapterHTTPStatusError",
    "DeviceAdapterTimeoutError",
    "SandboxRuntimeUnavailableError",
    "SkillCenterClient",
    "SkillCenterMarketSearchRequest",
    "get_current_env",
    "CollaboratorPermissionInterceptor",
    "InterceptedResponse",
    "InterceptorContext",
    "with_interceptors",
    "PermissionParams",
    "DEFAULT_ENGINE_TYPE",
    "_UPLOAD_SIGN_URL_EXPIRES",
    "logger",
    "router",
    "_legacy_runtime_projection",
    "BOT_RUNTIME_UNAVAILABLE_MESSAGE",
    "_RUNTIME_UPLOAD_EXCEPTION_TYPES",
    "_RUNTIME_UPLOAD_ERROR_PATTERNS",
    "_MANIFEST_UPLOAD_ERROR_CODES",
    "_PACKAGE_UPLOAD_ERROR_CODES",
    "_PACKAGE_UPLOAD_MESSAGES",
    "_FILESYSTEM_LAYOUT_ENGINES",
    "_desktop_active_root_from_probe",
    "_upload_error_chain",
    "_contains_zip_upload_error",
    "_classify_upload_validation_error",
    "_is_runtime_upload_error",
    "_normalize_upload_error_message",
    "_build_upload_error_response",
    "_build_uploaded_zip",
    "_build_upload_oss_path",
    "parse_tags",
    "_get_path_params",
    "_get_skill_set_path_params",
    "_resolve_teclaw_local_skill",
    "_get_skill_by_id_or_link_name",
    "upload_skill",
    "list_skills",
    "create_skill",
    "get_active_skills",
    "get_current_skill_set",
    "sync_skill_set",
    "activate_skill_set",
    "deactivate_skill_set",
    "get_active_skill_sets",
    "get_skill_readme",
    "sync_skills_from_git",
    "_EXTRA_FIELDS",
    "_merge_skill_fields",
    "list_local_market_skills",
    "get_market_tree",
    "list_market_skills",
    "activate_skills_batch",
    "search_market_skills",
    "sync_market",
    "get_sync_status",
    "list_user_skills",
    "get_skill",
    "update_skill",
    "update_skill_risk_tags",
    "_extract_skill_mutation_permission",
    "_FailClosedSkillMutationPermissionInterceptor",
    "update_skill_mcp_dependencies",
    "get_skill_id_by_link_name",
    "extract_from_skill_id",
    "delete_skill",
    "_SkillParameterPermissionInterceptor",
    "_resolve_parameter_bot",
    "_extract_parameter_permission",
    "_create_skill_parameter_service",
    "get_skill_parameters",
    "save_skill_parameters",
    "publish_skill",
    "publish_upgrade_skill",
    "get_publish_status",
    "list_skill_versions",
    "search_market_center",
    "get_market_tags_center",
    "install_market_skill",
    "get_version_download_url",
    "get_skill_file_structure",
    "get_skill_file_content",
    "get_skill_members",
    "add_skill_member",
    "add_skill_members_batch",
    "remove_skill_member",
    "update_skill_member_role",
]


def _build_upload_oss_path(
    skill_name: str, version: str = "1.0.0", timestamp: str = None
) -> str:
    from agentclaw.community.core.config import sofa

    ts = timestamp or str(int(time.time()))
    # 从配置读取 OSS 上传路径前缀，没配置则用默认值
    skill_config = sofa.sofa_config.user_config.get("skill", {}).get("oss", {})
    prefix = skill_config.get(
        "upload_prefix", "aidesktop/aidesktop_pre/bolt_shared/skills-upload"
    )
    return f"{prefix}/{skill_name}/{ts}/{version}.zip"



from .skills_upload_routes import router as _upload_router
from .skills_activation_routes import router as _activation_router
from .skills_readme_routes import router as _readme_router
from .skills_market_routes import router as _market_router
from .skills_asset_routes import router as _asset_router
from .skills_parameter_routes import router as _parameter_router
from .skills_publication_routes import router as _publication_router
from .skills_member_routes import router as _member_router

router = APIRouter(prefix="/api/skills", tags=["skills"], routes=[
    *_upload_router.routes,
    *_activation_router.routes,
    *_readme_router.routes,
    *_market_router.routes,
    *_asset_router.routes,
    *_parameter_router.routes,
    *_publication_router.routes,
    *_member_router.routes,
])

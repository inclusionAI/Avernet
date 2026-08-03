"""
Pydantic Request/Response models for the Skill Center APIs.

Single source of truth for all skill center HTTP contracts.
Consolidated from:
- api/skills.py (legacy, now deleted)
- core/skill_center/routers/skills.py
- core/skill_center/routers/skillsets.py
- core/skill_center/routers/skill_auth.py
- core/skill_center/routers/skill_scan.py
"""

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# ==================== Request Models (skills) ====================

class CreateSkillRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Skill name (no underscore)")
    description: str = Field("", description="Skill description")
    git_path: str = Field("", description="Path in Git repository")
    category: str = Field("general", description="Business category")
    tags: List[str] = Field(default_factory=list, description="List of tags")
    input_schema: str = Field("", description="Input schema description")
    output_schema: str = Field("", description="Output schema description")
    is_public: bool = Field(False, description="Whether skill is public")
    user_id: Optional[str] = Field(None, description="User ID (optional in Phase 1)")
    bot_id: Optional[str] = Field(None, description="Bot ID (optional, default: default)")
    source_type: Optional[str] = Field(None, description="Source type: git/upload/online (auto-derived if omitted)")
    category_path: Optional[str] = Field(None, description="类目完整路径(ac_skill_category.code)")
    package_url: Optional[str] = Field(None, description="上传到oss上的可访问的url")
    zip_url: Optional[str] = Field(None, description="用户上传的url")


class UpdateSkillRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    input_schema: Optional[str] = None
    output_schema: Optional[str] = None
    is_public: Optional[bool] = None
    risk_tags: Optional[List[Dict[str, Any]]] = None
    mcp_dependencies: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = Field(None, description="User ID for permission check")
    category_path: Optional[str] = None
    package_url: Optional[str] = None
    zip_url: Optional[str] = None


class UpdateRiskTagsRequest(BaseModel):
    risk_tags: List[Dict[str, Any]] = Field(..., description="Risk tag list")
    user_id: Optional[str] = Field(None, description="User ID for permission check")


class UpdateMCPDependenciesRequest(BaseModel):
    mcp_dependencies: List[Dict[str, Any]] = Field(..., description="MCP dependency list")
    user_id: Optional[str] = Field(None, description="User ID for permission check")


class ActivateRequest(BaseModel):
    source_path: str
    relative_path: Optional[str] = None


class ActivateSkillsRequest(BaseModel):
    skill_paths: List[str]


class SearchRequest(BaseModel):
    query: str
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(100, ge=1, le=100, description="每页数量")
    is_public: Optional[bool] = Field(None, description="是否公开过滤：True=仅公开，None=公开+用户参与的技能")


class SyncSkillsRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="User ID (optional in Phase 1)")


class SwitchSkillSetRequest(BaseModel):
    skill_set_id: str = Field(..., description="Target skill set ID")
    user_id: Optional[str] = Field(None, description="User ID (optional in Phase 1)")
    proxy_token: Optional[str] = Field(None, description="agentclawproxy token for remote sync")


class SyncSkillSetRequest(BaseModel):
    skill_set_id: str = Field(..., description="Skill set ID to sync")
    user_id: Optional[str] = Field(None, description="User ID (optional in Phase 1)")


class ActivateSkillSetRequest(BaseModel):
    skill_set_id: str = Field(..., description="Target skill set ID to activate")
    proxy_token: Optional[str] = Field(None, description="agentclawproxy token for remote sync")
    entity_id: Optional[str] = Field(None, description="Entity ID (pure ID, no prefix needed)")
    entity_type: Optional[str] = Field(None, description="Entity type (staff/proj/team, default: staff)")
    bot_id: Optional[str] = Field(None, description="Bot ID (default: default)")
    engine_type: Optional[str] = Field(None, description="Engine type (forced to openclaw)")


class DeactivateSkillSetRequest(BaseModel):
    skill_set_id: str = Field(..., description="Target skill set ID to deactivate")
    proxy_token: Optional[str] = Field(None, description="agentclawproxy token for remote sync")
    entity_id: Optional[str] = Field(None, description="Entity ID (pure ID, no prefix needed)")
    entity_type: Optional[str] = Field(None, description="Entity type (staff/proj/team, default: staff)")
    bot_id: Optional[str] = Field(None, description="Bot ID (default: default)")
    engine_type: Optional[str] = Field(None, description="Engine type (forced to openclaw)")


class SaveSkillParametersRequest(BaseModel):
    parameters: dict


# ==================== Request Models (skill members) ====================

class AddSkillMemberRequest(BaseModel):
    user_id: str = Field(..., description="用户 ID")
    role: str = Field("member", description="角色：admin | member")


class BatchAddSkillMembersRequest(BaseModel):
    members: List[Dict[str, str]] = Field(..., description="成员列表，每个成员为 {user_id: str, role: str}")


class UpdateSkillMemberRoleRequest(BaseModel):
    role: str = Field(..., description="新角色：admin | member")


# ==================== Request Models (skillsets) ====================

class CreateSkillSetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Skill set name (no underscore)")
    description: Optional[str] = Field(None, description="Skill set description")
    user_id: Optional[str] = Field(None, description="User ID (optional in Phase 1)")
    bot_id: Optional[str] = Field(None, description="Bot ID (optional, default: default)")


class UpdateSkillSetRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_default: Optional[bool] = None
    user_id: Optional[str] = Field(None, description="User ID for permission check")
    bot_id: Optional[str] = Field(None, description="Bot ID (optional, default: default)")


class AddSkillsToSetRequest(BaseModel):
    skill_ids: List[str] = Field(..., description="List of skill IDs to add")
    user_id: Optional[str] = Field(None, description="User ID for permission check")
    bot_id: Optional[str] = Field(None, description="Bot ID (optional, default: default)")


# AddSkillsRequest is the skillsets router version (same shape, kept for clarity)
class AddSkillsRequest(BaseModel):
    skill_ids: List[str] = Field(..., description="List of skill IDs to add")
    user_id: Optional[str] = Field(None, description="User ID for permission check")
    bot_id: Optional[str] = Field(None, description="Bot ID (optional, default: default)")


class RemoveSkillRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="User ID for permission check")


class SetDefaultSkillsRequest(BaseModel):
    skill_ids: List[str] = Field(..., description="List of skill IDs to set as default (replaces existing)")


class SetDefaultSkillsFastRequest(BaseModel):
    skill_ids: List[str] = Field(..., description="List of skill IDs to set as default (replaces existing)")
    use_local_repo: bool = Field(True, description="Use local /home/admin/aiworkbench/skills-repo for faster scan")


class FixGitPathRequest(BaseModel):
    dry_run: bool = Field(True, description="只检查不修改，默认 True")


class AddMCPRequest(BaseModel):
    server_code: str = Field(..., description="MCP server code from MCP Center")
    user_id: Optional[str] = Field(None, description="User ID for permission check")


class RemoveMCPRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="User ID for permission check")


class SetSkillSetActiveRequest(BaseModel):
    env: str = Field(..., description="环境标识")
    skillset_id: str = Field(..., description="技能集 ID")
    is_active: int = Field(..., description="激活状态 (0 或 1)")


# ==================== Request Models (skill_auth) ====================

class ApplyPermissionRequest(BaseModel):
    skill_id: str = Field(..., description="Skill ID")
    reason: str = Field("", description="申请原因")


class ApplySkillSetPermissionRequest(BaseModel):
    skill_set_id: str = Field(..., description="SkillSet ID")
    reason: str = Field("", description="申请原因")


class ApplyBotPermissionRequest(BaseModel):
    bot_id: str = Field(..., description="Bot ID (对应 ac_skill_set.bolt_id)")
    reason: str = Field("", description="申请原因")


# ==================== Request Models (skill_scan) ====================

class ScanGitRequest(BaseModel):
    git_url: str = Field(..., description="Git repository URL to scan")
    private_token: Optional[str] = Field(None, description="Git private token (optional)")
    max_workers: Optional[int] = Field(None, description="Max concurrent scans")


class ScanPathRequest(BaseModel):
    scan_path: str = Field(..., description="Path to directory containing skills")


class ScanSkillRequest(BaseModel):
    skill_path: str = Field(..., description="Path to the skill file")


class StartDailyTaskRequest(BaseModel):
    git_url: str = Field(..., description="Git repository URL to scan")
    private_token: Optional[str] = Field(None, description="Git private token (optional)")


class ScanCenterRequest(BaseModel):
    skill_uuids: List[str] = Field(..., description="要扫描的 skill UUID 列表")
    env: str = Field("dev", description="环境 dev/pre/prod")


# ==================== Response Models (skills) ====================

class SkillMetadataResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    git_path: Optional[str]
    link_name: Optional[str]
    category: Optional[str]
    tags: List[str]
    risk_tags: List[Dict[str, Any]] = []
    mcp_dependencies: List[Dict[str, Any]] = []
    input_schema: Optional[str]
    output_schema: Optional[str]
    is_public: bool
    is_builtin: bool
    user_id: Optional[str]
    bot_id: Optional[str] = "default"
    gmt_created: str
    gmt_modified: str
    status: Optional[str] = None
    version: Optional[int] = None
    skill_uuid: Optional[str] = None
    source_type: Optional[str] = None
    members: List[Dict[str, str]] = []
    category_path: Optional[str] = None
    package_url: Optional[str] = None
    zip_url: Optional[str] = None


class SkillListResponse(BaseModel):
    success: bool
    data: List[SkillMetadataResponse]
    count: int


class PaginatedSkillListResponse(BaseModel):
    success: bool
    data: List[SkillMetadataResponse]
    total: int
    limit: int
    offset: int


class SkillDetailResponse(BaseModel):
    success: bool
    data: Optional[SkillMetadataResponse] = None


class SkillReadmeResponse(BaseModel):
    success: bool
    data: Dict[str, str]


class ActivateResponse(BaseModel):
    success: bool
    message: str
    link_name: str


class DeactivateResponse(BaseModel):
    success: bool
    message: str


class RemoveResponse(BaseModel):
    success: bool
    message: str


class MarketTreeResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]


class MarketListResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    count: int


class ActivateSkillSuccessItem(BaseModel):
    id: str
    link_name: str
    path: str


class ActivateSkillFailedItem(BaseModel):
    path: str
    error: str


class ActivateSkillsResults(BaseModel):
    success: List[ActivateSkillSuccessItem]
    failed: List[ActivateSkillFailedItem]


class ActivateSkillsResponse(BaseModel):
    success: bool
    data: ActivateSkillsResults
    message: str


class SearchResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    count: int


class SyncSkillsResult(BaseModel):
    created: int
    updated: int
    deleted: int
    failed: int
    errors: List[str]


class SyncSkillsResponse(BaseModel):
    success: bool
    data: SyncSkillsResult
    message: str


class MessageResponse(BaseModel):
    success: bool
    message: str


class SwitchSkillSetResponse(BaseModel):
    success: bool
    message: str
    data: Dict[str, Any]


class ActivateSkillSetResponse(BaseModel):
    success: bool
    message: str
    data: Dict[str, Any]


class DeactivateSkillSetResponse(BaseModel):
    success: bool
    message: str
    data: Dict[str, Any]


class ActiveSkillSetsResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    count: int


class CurrentSkillSetResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]]


class ActiveSkillsResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    count: int


class SyncStatusResponse(BaseModel):
    success: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class UploadSkillResponse(BaseModel):
    success: bool
    data: Optional[SkillMetadataResponse] = None
    message: str


class SkillParametersResponse(BaseModel):
    success: bool
    data: dict


# ==================== Response Models (skillsets) ====================

class SkillInSetSummary(BaseModel):
    id: str
    name: str
    description: Optional[str]
    path: Optional[str] = None  # skill_path or git_path for activation


class SkillSetResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    is_default: bool
    is_builtin: bool
    user_id: Optional[str]
    bot_id: Optional[str] = "default"
    engine_type: Optional[str] = None
    gmt_created: str
    gmt_modified: str
    is_active: Optional[bool] = False
    skills: List[SkillInSetSummary] = []
    type: str = "custom"  # default, builtin, custom


class SkillSetListResponse(BaseModel):
    success: bool
    data: List[SkillSetResponse]
    count: int


class SkillSetDetailResponse(BaseModel):
    success: bool
    data: SkillSetResponse


class SkillInSetResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    category: Optional[str]
    git_path: Optional[str]
    tags: List[str]
    status: Optional[str] = None
    version: Optional[int] = None
    skill_uuid: Optional[str] = None
    source_type: Optional[str] = None


class SkillSetSkillsResponse(BaseModel):
    success: bool
    data: List[SkillInSetResponse]
    count: int


class AddSkillsResult(BaseModel):
    skill_id: str


class AddSkillsFailedResult(BaseModel):
    skill_id: str
    error: str


class AddSkillsResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class MCPServerInSetResponse(BaseModel):
    id: str
    server_code: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    status: str = "ONLINE"


class CLIInSetResponse(BaseModel):
    cli_code: str
    cli_name: Optional[str] = None
    cli_desc: Optional[str] = None


class SkillSetWithMCPsItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    is_default: bool
    user_id: Optional[str] = None
    bot_id: Optional[str] = "default"
    mcps: List[MCPServerInSetResponse] = []


class SkillSetsWithMCPsResponse(BaseModel):
    success: bool
    data: List[SkillSetWithMCPsItem]
    count: int


class SkillSetResourceItem(SkillSetWithMCPsItem):
    clis: List[CLIInSetResponse] = Field(default_factory=list)


class SkillSetResourcesResponse(BaseModel):
    success: bool
    data: List[SkillSetResourceItem]
    count: int


class InitAndSyncResponse(BaseModel):
    success: bool
    skill_set: Dict[str, Any]
    sync: Dict[str, Any]


class SetDefaultSkillsResponse(BaseModel):
    success: bool
    skill_set: Dict[str, Any]
    sync: Dict[str, Any]


class SetDefaultSkillsFastResponse(BaseModel):
    success: bool
    skill_set: Dict[str, Any]
    sync: Dict[str, Any]


class FixGitPathResponse(BaseModel):
    success: bool
    checked: int
    fixed: int
    unchanged: int
    details: List[Dict[str, Any]]


class SkillSetMCPsResponse(BaseModel):
    success: bool
    data: List[MCPServerInSetResponse]
    count: int


class AddMCPResponse(BaseModel):
    success: bool
    message: str
    server_code: Optional[str] = None
    error: Optional[str] = None
    sync_error: Optional[str] = None
    requires_api_key: bool = False
    requires_permission: bool = False


class SetSkillSetActiveResponse(BaseModel):
    success: bool
    message: str
    updated_count: int


# ==================== Response Models (skill_auth) ====================

class ToolPermissionDetail(BaseModel):
    code: str
    name: str


class MCPPermissionDetail(BaseModel):
    has_permission: bool
    access_level: str = ""
    tool_permissions: dict[str, ToolPermissionDetail] = {}
    error: str | None = None


class SkillPermissionResponse(BaseModel):
    success: bool = True
    skill_id: str
    skill_name: str
    authorized: bool
    mcp_details: dict[str, MCPPermissionDetail] = {}


class MCPApplyResult(BaseModel):
    success: bool
    process_url: str | None = None
    error: str | None = None
    already_authorized: bool = False


class ApplySkillPermissionResponse(BaseModel):
    success: bool = True
    skill_id: str
    skill_name: str
    all_authorized: bool
    apply_results: dict[str, MCPApplyResult] = {}


class SkillSetSkillDetail(BaseModel):
    skill_name: str
    authorized: bool
    mcp_details: dict[str, MCPPermissionDetail] = {}


class SkillSetPermissionResponse(BaseModel):
    success: bool = True
    skill_set_id: str
    authorized: bool
    skills: dict[str, SkillSetSkillDetail]
    mcp_summary: dict[str, MCPPermissionDetail]


class SkillSetApplySkillDetail(BaseModel):
    skill_name: str
    all_authorized: bool
    mcp_codes: list[str]


class ApplySkillSetPermissionResponse(BaseModel):
    success: bool = True
    skill_set_id: str
    all_authorized: bool
    skills: dict[str, SkillSetApplySkillDetail]
    apply_results: dict[str, MCPApplyResult]


class BotPermissionResponse(BaseModel):
    success: bool = True
    bot_id: str
    authorized: bool
    mcp_details: dict[str, MCPPermissionDetail] = {}


class ApplyBotPermissionResponse(BaseModel):
    success: bool = True
    bot_id: str
    all_authorized: bool
    apply_results: dict[str, MCPApplyResult] = {}


# ==================== Response Models (skill_scan) ====================

class ScanResultItem(BaseModel):
    skill_path: str
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None


class ScanResponse(BaseModel):
    success: bool
    total: int
    success_count: int
    results: List[ScanResultItem]
    message: str = ""


class ServiceStatusResponse(BaseModel):
    success: bool
    service_started: bool
    scheduler_started: bool
    daily_task_running: bool
    message: str


# ==================== Request/Response Models (skill_publish) ====================

class PublishSkillRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="User ID for permission check")
    zip_path: Optional[str] = Field(None, description="ZIP 包全路径，前端传入后跳过后端打包")
    package_url: Optional[str] = Field(None, description="上传到oss上的可访问的url")


class PublishStatusResponse(BaseModel):
    success: bool
    message: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)


class VersionListResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0


# ==================== Request/Response Models (market_center) ====================

class MarketSearchResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class MarketTagsResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]] = Field(default_factory=list)


class DownloadUrlResponse(BaseModel):
    success: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class FileStructureResponse(BaseModel):
    success: bool
    data: Any = Field(default_factory=dict)
    message: str = ""


class FileContentResponse(BaseModel):
    success: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


# ==================== Response Models (skill members) ====================

# ==================== Batch Sync Models ====================

class BatchSyncRequest(BaseModel):
    skill_codes: Optional[List[str]] = Field(None, description="指定技能列表，None=全量")
    force: bool = Field(False, description="跳过幂等检查，强制重新发布")
    skills_dir: Optional[str] = Field(None, description="技能仓库根目录，默认使用 GitSyncConfig 配置路径")
    batch_size: Optional[int] = Field(None, description="每批处理数量，None=不分批（全量处理）", ge=1)
    batch_index: int = Field(0, description="当前批次索引（从 0 开始）", ge=0)
    envs: Optional[List[str]] = Field(
        None,
        description="指定从哪些 env 捞数据发布，默认只当前环境；传 ['pre','prod'] 则两种都发布"
    )


class BatchSyncSkillResult(BaseModel):
    skill_code: str
    success: bool
    status: str = ""
    skipped: bool = False
    error: str = ""


class BatchSyncResponse(BaseModel):
    success: bool
    trace_id: str = ""
    total: int = 0
    skipped: int = 0
    success_count: int = 0
    failed: int = 0
    results: List[BatchSyncSkillResult] = Field(default_factory=list)
    report_path: str = ""
    batch_size: Optional[int] = Field(None, description="每批处理数量，None 表示未分批")
    batch_index: int = Field(0, description="当前批次索引")
    total_batches: int = Field(0, description="总批次数（分批模式下）")
    has_next: bool = Field(False, description="是否还有下一批")


class BatchSyncTaskResponse(BaseModel):
    """异步批量同步任务 — 立即返回，后台执行。"""
    task_id: str
    status: str = "running"
    message: str = ""


class BatchSyncGraphStatusResponse(BaseModel):
    """异步任务状态查询结果。"""
    task_id: str
    status: str  # running | done | error
    progress: str = ""  # e.g. "batch 3/27"
    result: Optional[Any] = None
    error: str = ""


class SkillMemberResponse(BaseModel):
    id: str
    skill_uuid: str
    user_id: str
    role: str
    gmt_create: str
    gmt_modified: str


class SkillMemberListResponse(BaseModel):
    success: bool
    data: List[SkillMemberResponse]
    count: int


class SkillMemberOperationResponse(BaseModel):
    success: bool
    data: SkillMemberResponse
    message: str


class BatchAddMembersResult(BaseModel):
    success: List[SkillMemberResponse]
    failed: List[Dict[str, Any]]


class BatchAddMembersResponse(BaseModel):
    success: bool
    data: BatchAddMembersResult
    message: str


# =============================================================================
# Skill Category Schemas
# =============================================================================


class CreateCategoryRequest(BaseModel):
    code: str = Field(..., max_length=256, description="当前类目Code（目录名）")
    name: str = Field(..., max_length=256, description="类目名称（中文名称，暂用英文占位）")
    parent_code: str = Field("", max_length=256, description="父类目编码，根节点为空字符串")
    sort_order: int = Field(0, description="同级排序序号")


class UpdateCategoryRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=256, description="类目名称")
    sort_order: Optional[int] = Field(None, description="同级排序序号")
    status: Optional[int] = Field(None, description="状态 1启用 0禁用")


class CategoryResponse(BaseModel):
    id: Optional[str] = None
    code: str
    name: str
    parent_code: str = ""
    path: Optional[str] = None
    level: int = 0
    sort_order: int = 0
    status: int = 1
    gmt_created: Optional[str] = None
    gmt_modified: Optional[str] = None


class CategoryTreeNode(BaseModel):
    code: str
    name: str
    parent_code: str = ""
    path: Optional[str] = None
    level: int = 0
    sort_order: int = 0
    status: int = 1
    children: List["CategoryTreeNode"] = []


class CategoryTreeResponse(BaseModel):
    success: bool
    data: List[CategoryTreeNode]


class CategoryDetailResponse(BaseModel):
    success: bool
    data: CategoryResponse


class CategoryListResponse(BaseModel):
    success: bool
    data: List[CategoryResponse]

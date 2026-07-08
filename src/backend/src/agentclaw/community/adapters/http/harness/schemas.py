"""Harness API Pydantic models.

Pure DTOs — no business logic, no imports from core/harness/.
"""
import threading
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

_T = TypeVar("_T")


class ConcurrentStore(Generic[_T]):
    """Thread-safe key-value store, analogous to Java's ConcurrentHashMap.

    Protects all reads and writes with a threading lock so that multiple
    uvicorn workers or thread-pool executors can safely access shared
    state without data races.
    """

    def __init__(self) -> None:
        self._data: dict[int, _T] = {}
        self._lock = threading.Lock()

    def get(self, key: int) -> _T | None:
        with self._lock:
            return self._data.get(key)

    def __contains__(self, key: int) -> bool:
        with self._lock:
            return key in self._data

    def __setitem__(self, key: int, value: _T) -> None:
        with self._lock:
            self._data[key] = value

    def values(self) -> list[_T]:
        with self._lock:
            return list(self._data.values())

    def keys(self) -> list[int]:
        with self._lock:
            return list(self._data.keys())

    def pop(self, key: int, default: _T | None = None) -> _T | None:
        with self._lock:
            return self._data.pop(key, default)


# ── shared ──────────────────────────────────────────────────

class PatchOperationItem(BaseModel):
    """Operation definition within a patch template.

    Fixed fields: op, target, template.
    Custom fields (e.g. src_md_content, dst_md_content) go into ``detail``.
    """

    op: str = Field(..., description="Operation type: update_md / rewrite_section / ...")
    target: str = Field(..., description="Target file name")
    template: str | None = Field(default=None, description="Instruction / template for this operation")
    detail: dict[str, Any] = Field(default_factory=dict, description="Custom fields (e.g. src_md_content, dst_md_content)")


class PreviewOperationItem(BaseModel):
    op: str = Field(..., description="Operation type: update_md / rewrite_section / ...")
    target: str = Field(..., description="Target file name")
    diff: str = Field(..., description="Unified diff text for this operation")
    op_summary: str = Field(..., description="One-line summary of this operation")
    content: str = Field(..., description="Full content text produced/replaced by this operation")
    template: str | None = Field(default=None, description="Instruction / template for this operation")
    detail: dict[str, Any] = Field(default_factory=dict, description="Custom fields (e.g. src_md_content, dst_md_content)")


# ── /diagnose — POST (start scan) ───────────────────────────

class DiagnoseRequest(BaseModel):
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str = Field(..., description="Entity ID (owner/workNo)")
    bot_id: str = Field(..., description="Bot ID")
    scan_type: str = Field(default="full", description="full / verify")
    layer: str = Field(default="L1", description="L1 / L2 / L3")
    trigger_source: str = Field(default="api", description="api / verify / schedule")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID, to read files from a specific publish version")


class DiagnoseStartResponse(BaseModel):
    success: bool = Field(default=True)
    scan_id: int
    bot_id: str
    entity_id: str
    status: str = Field(default="scanning")
    message: str = Field(default="Scan started")


# ── /diagnose/{scan_id} — GET (poll progress) ───────────────

class FileProgressItem(BaseModel):
    check_item: str = Field(..., description="Check item name, e.g. AGENTS.md / SAFETY.md")
    status: str = Field(..., description="pending / running / completed / error")
    cost: int | None = Field(None, description="Check cost in ms, null when not completed")
    result: str | None = Field(None, description="Check result: pass / warning / fail / error, null when not completed")
    result_detail: str | None = Field(None, description="Semicolon-joined short summaries of each finding, e.g. '角色定义缺失;结构不完整'")
    score: int | None = Field(None, description="Average score 0-100 for this file type, null when not completed")


class FindingDetail(BaseModel):
    rule_id: str = Field("", description="Diagnostic rule ID, e.g. D-AGENTS-001")
    name: str = Field(..., description="Finding rule name, e.g. AGENTS.md 角色定义诊断")
    message: str = Field(..., description="Detailed description and fix suggestion")
    risk_level: str = Field(..., description="critical / warning / info")
    result: str = Field("warning", description="fail / warning, derived from severity")
    score: int = Field(0, description="Finding-level score 0-100 from LLM diagnostic")
    suggested_template_ids: list[int] = Field(default_factory=list, description="Suggested patch template IDs")
    patch_id_list: list[int] = Field(default_factory=list, description="Applied patch IDs")


class GroupedFindingItem(BaseModel):
    check_item: str = Field(..., description="File type, e.g. AGENTS.md / SAFETY.md")
    all_patch_id_list: list[int] = Field(default_factory=list, description="All patch IDs across findings for this file")
    finding_details: list[FindingDetail] = Field(default_factory=list, description="Individual findings for this file")


class PatchProgressItem(BaseModel):
    """Progress item for a single patch being generated."""

    template_name: str = Field(..., description="Patch template name")
    target: str = Field(..., description="Target file type, e.g. AGENTS.md")
    status: str = Field(..., description="pending / patching / completed / error")
    patch_id: int | None = Field(None, description="Generated patch ID in ac_harness_patch")
    record_id: int | None = Field(None, description="Generated patch record ID in ac_harness_patch_record")
    error: str | None = Field(None, description="Error message if status=error")


class DiagnosePollResponse(BaseModel):
    success: bool = Field(default=True)
    scan_id: int
    bot_id: str
    entity_id: str
    status: str = Field(..., description="scanning / scan_completed / patching / completed / failed")
    scan_type: str = Field(default="full")
    layer: str = Field(default="L1")
    diagnose_progress: list[FileProgressItem] = Field(default_factory=list)
    patch_progress: list[PatchProgressItem] = Field(default_factory=list, description="Patch generation progress items")
    summary: dict[str, int] = Field(default_factory=dict, description="Finding counts: critical / warning / info")
    failed_reason: str | None = Field(None, description="Failure reason, only set when status=failed")


# ── /diagnose/{scan_id}/report — GET (full report) ──────────

class FindingItem(BaseModel):
    name: str = Field(..., description="Finding title, e.g. AGENTS.md 长度超标")
    detail: str = Field(..., description="Detailed description of the issue")
    risk_level: str = Field(..., description="critical / warning / info")
    result: str = Field("warning", description="fail / warning, diagnosis result")
    score: int = Field(0, description="Finding-level score 0-100 from LLM diagnostic")
    patch_id_list: list[str] = Field(default_factory=list, description="Associated patch IDs")


class DiagnoseReportResponse(BaseModel):
    success: bool = Field(default=True)
    scan_id: int
    bot_id: str
    entity_id: str
    status: str = Field(default="completed")
    scan_type: str = Field(default="full")
    layer: str = Field(default="L1")
    findings: list[FindingItem] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    health_score: int = Field(..., description="Health score 0-100")
    grade: str = Field(..., description="excellent / good / warning / critical")
    cost: int = Field(default=0, description="Total scan duration in ms")


# ── /diagnose/recent — GET (latest completed) ───────────────

class DiagnoseRecentResponse(BaseModel):
    success: bool = Field(default=True)
    found: bool = Field(..., description="Whether a completed scan exists")
    scan_id: int | None = None
    bot_id: str
    entity_id: str
    status: str | None = None
    scan_type: str | None = None
    layer: str | None = None
    findings: list[FindingItem] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    health_score: int | None = None
    grade: str | None = None
    cost: int | None = None
    gmt_create: str | None = None


# ── /diagnose/records — GET (paginated history) ─────────────

class RecordItem(BaseModel):
    scan_id: int
    bot_id: str
    entity_id: str
    scan_type: str
    layer: str
    health_score: int | None = None
    grade: str | None = None
    findings_summary: dict[str, int] | None = None
    trigger_source: str | None = None
    cost: int | None = None
    status: str
    gmt_create: str


class DiagnoseRecordsResponse(BaseModel):
    success: bool = Field(default=True)
    total: int
    page: int
    size: int
    items: list[RecordItem] = Field(default_factory=list)


# ── /patches ───────────────────────────────────────────────

class PatchesRequest(BaseModel):
    entity_type: str = Field(default="staff")
    entity_id: str = Field(...)
    bot_id: str = Field(...)
    layer: str = Field(default="L1", description="L1 / L2 / L3")


class PatchTemplateItem(BaseModel):
    name: str
    layer: str
    description: str | None = None
    version: int = 1
    risk_level: str = "low"
    operations: list[PatchOperationItem] = Field(default_factory=list)


class PatchesResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    entity_id: str
    templates: list[PatchTemplateItem] = Field(default_factory=list)


# ── /preview ───────────────────────────────────────────────

class PreviewRequest(BaseModel):
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str | None = Field(default=None, description="Entity ID, defaults from request context")
    bot_id: str = Field(..., description="Bot ID")
    scan_id: int | None = Field(default=None, description="Associated scan record ID for score comparison")
    file_type: str | None = Field(default=None, description="Target file type, e.g. AGENTS.md / SAFETY.md / SOUL.md; auto-derived from patch template if omitted")
    patch_id_list: list[int] = Field(..., description="Patch template IDs to preview")


class PreviewResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    scan_id: int | None = Field(None, description="Associated scan ID")
    risk_level: str = Field(..., description="Overall risk level: low / medium / high")
    final_content: str = Field(..., description="Full document content after all patches are applied")
    operations: list[PreviewOperationItem] = Field(default_factory=list)


# ── /apply ──────────────────────────────────────────────────

class ApplyRequest(BaseModel):
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str | None = Field(default=None, description="Entity ID, defaults from request context")
    bot_id: str = Field(..., description="Bot ID")
    patch_id_list: list[int] = Field(default_factory=list, description="ac_harness_patch table IDs to apply (alternative to record_id)")
    record_id: int | None = Field(default=None, description="ac_harness_patch_record table ID to apply (alternative to patch_id_list)")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID, to read/write files from a specific publish version")


class ApplyResponse(BaseModel):
    success: bool = Field(default=True)
    err_msg: str | None = Field(default=None, description="Error message when success=false")


# ── /generate-patches ───────────────────────────────────────

class GeneratePatchesRequest(BaseModel):
    bot_id: str = Field(..., description="Bot ID")
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str = Field(..., description="Entity ID (owner/workNo)")
    scan_id: int | None = Field(default=None, description="Optional scan_id; if omitted uses latest completed scan")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID, to read files from a specific publish version")


class GeneratePatchesResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    generated: int = Field(default=0, description="Number of patches generated")
    record_ids: list[int] = Field(default_factory=list)


# ── /patch-records ──────────────────────────────────────────

class PatchRecordItem(BaseModel):
    id: int
    bot_id: str
    entity_id: str
    patch_id: int
    layer: str
    status: str = Field(..., description="planned / previewed / applying / applied / verified / rolled_back / failed")
    target_files: list[str] = Field(default_factory=list)
    preview_diff: str | None = None
    backup_content: str | None = None
    applied_by: str | None = None
    applied_at: str | None = None
    failed_reason: str | None = None
    gmt_create: str | None = None


class PatchRecordListResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    entity_id: str
    items: list[PatchRecordItem] = Field(default_factory=list)


class PatchRecordApplyResponse(BaseModel):
    success: bool = Field(default=True)
    record_id: int
    status: str
    err_msg: str | None = None


class PatchRecordRollbackResponse(BaseModel):
    success: bool = Field(default=True)
    record_id: int
    status: str
    err_msg: str | None = None


# ── rollback ───────────────────────────────────────────────

class RollbackRequest(BaseModel):
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str | None = Field(default=None, description="Entity ID")
    bot_id: str = Field(..., description="Bot ID")
    patch_id: int = Field(..., description="Patch ID from ac_harness_patch table")
    file_type: str | None = Field(default=None, description="Target file type, auto-detected from patch operations if omitted")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID, to read/write files from a specific publish version")


# ── template management ─────────────────────────────────────

class TemplateCreateRequest(BaseModel):
    name: str = Field(..., description="Template name (unique within env)")
    layer: str = Field(default="L1", description="L1 / L2 / L3")
    target: dict[str, Any] = Field(default_factory=dict, description='{"files": [...], "sections": [...]}')
    description: str | None = Field(default=None)
    applicable_when: dict[str, Any] | None = Field(default=None, description='{"skill_sets_contains_any": [...]}')
    operations: list[PatchOperationItem] = Field(default_factory=list)
    risk_level: str = Field(default="low", description="low / medium / high")
    status: str = Field(default="active", description="active / deprecated / draft")


class TemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None)
    layer: str | None = Field(default=None)
    target: dict[str, Any] | None = Field(default=None)
    description: str | None = Field(default=None)
    applicable_when: dict[str, Any] | None = Field(default=None)
    operations: list[PatchOperationItem] | None = Field(default=None)
    risk_level: str | None = Field(default=None)
    status: str | None = Field(default=None)


class TemplateDetailResponse(BaseModel):
    success: bool = Field(default=True)
    id: int
    name: str
    layer: str
    target: dict[str, Any]
    version: int
    description: str | None = None
    applicable_when: dict[str, Any] | None = None
    operations: list[PatchOperationItem]
    risk_level: str
    status: str
    env: str
    gmt_create: str | None = None
    gmt_modified: str | None = None


class TemplateListResponse(BaseModel):
    success: bool = Field(default=True)
    total: int
    items: list[TemplateDetailResponse]


# ── /diagnose/dim-report — GET (dimension score report) ─────

class PatchItem(BaseModel):
    """Patch information for dim-report response."""

    patch_id: int = Field(..., description="补丁 ID")
    name: str = Field(..., description="补丁名称")
    description: str | None = Field(default=None, description="补丁描述")
    is_applied: bool = Field(default=False, description="是否已应用")
    layer: str | None = Field(default=None, description="层级 L1/L2/L3")
    operations: list[PatchOperationItem] = Field(default_factory=list, description="操作列表")
    gmt_create: str | None = Field(default=None, description="创建时间")
    is_patch: bool = Field(default=False, description="是否存在可执行修复 patch")
    advise: dict[str, Any] | None = Field(default=None, description="修复建议 JSON")
    is_advise: bool = Field(default=False, description="是否存在可展示修复建议")


class DimReportItem(BaseModel):
    scan_dim: str | None = Field(default=None, description="体检维度")
    health_score: int | None = Field(default=None, description="健康分（0-100）")
    grade: str | None = Field(default=None, description="评分等级：excellent / good / warning / critical")
    check_items: str | None = Field(default=None, description="检查项 JSON")
    findings: str | None = Field(default=None, description="漏洞明细 JSON")
    findings_summary: str | None = Field(default=None, description="漏洞摘要 JSON")
    trigger_source: str | None = Field(default=None, description="触发来源")
    status: str = Field(default="completed", description="状态")
    failed_reason: str | None = Field(default=None, description="失败原因")
    env: str | None = Field(default=None, description="环境标识")
    duration_ms: int | None = Field(default=None, description="诊断耗时（ms）")
    scan_type: str | None = Field(default=None, description="扫描类型")
    scan_report_type: str = Field(
        default="normal",
        description="报告类型：normal 普通扫描报告 / daily 日报 / weekly 周报",
    )
    patch_ids: str | None = Field(default=None, description="补丁 ID 列表 JSON")
    patches: list[PatchItem] = Field(default_factory=list, description="补丁详细信息列表")
    gmt_create: str | None = None


class DimReportResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    entity_id: str
    bot_publish_id: str | None = Field(default=None)
    items: list[DimReportItem] = Field(default_factory=list)


# ── /diagnose/dim-history — GET (dimension history list) ─────

class DimHistoryRecordItem(BaseModel):
    """Single scan record in dimension history list."""

    id: int = Field(..., description="记录 ID")
    bot_id: str = Field(..., description="Bot ID")
    entity_id: str = Field(..., description="Entity ID")
    scan_dim: str | None = Field(default=None, description="体检维度")
    scan_type: str | None = Field(default=None, description="扫描类型")
    scan_report_type: str = Field(
        default="normal",
        description="报告类型：normal 普通扫描报告 / daily 日报 / weekly 周报",
    )
    health_score: int | None = Field(default=None, description="健康分（0-100）")
    grade: str | None = Field(default=None, description="评分等级：excellent / good / warning / critical")
    check_items: str | None = Field(default=None, description="检查项 JSON")
    findings: str | None = Field(default=None, description="漏洞明细 JSON")
    findings_summary: str | None = Field(default=None, description="漏洞摘要 JSON")
    trigger_source: str | None = Field(default=None, description="触发来源")
    status: str = Field(default="completed", description="状态")
    failed_reason: str | None = Field(default=None, description="失败原因")
    env: str | None = Field(default=None, description="环境标识")
    duration_ms: int | None = Field(default=None, description="诊断耗时（ms）")
    patch_ids: str | None = Field(default=None, description="补丁 ID 列表 JSON")
    patches: list[PatchItem] = Field(default_factory=list, description="补丁详细信息列表")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")
    gmt_create: str | None = None
    gmt_modified: str | None = None


class DimHistoryResponse(BaseModel):
    """Response for dimension history list endpoint."""

    success: bool = Field(default=True)
    bot_id: str
    entity_id: str
    scan_dim: str | None = Field(default=None, description="体检维度筛选条件")
    bot_publish_id: str | None = Field(default=None)
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, description="当前页码")
    size: int = Field(default=20, description="每页大小")
    items: list[DimHistoryRecordItem] = Field(default_factory=list, description="历史记录列表")


# ── /diagnose/records — POST (batch create scan records) ─────

class ScanRecordCreateItem(BaseModel):
    scan_dim: str | None = Field(default=None, description="体检维度")
    health_score: int = Field(..., description="健康分（0-100）")
    score_grade: str | None = Field(default=None, description="评分等级：excellent / good / warning / critical")
    check_items: str | None = Field(default=None, description="检查项 JSON")
    findings: str | None = Field(default=None, description="漏洞明细 JSON")
    findings_summary: str | None = Field(default=None, description="漏洞摘要 JSON")
    duration_ms: int = Field(..., description="诊断耗时（ms）")
    scan_type: str | None = Field(default=None, description="扫描类型")
    patch_ids: str | None = Field(default=None, description="补丁 ID 列表 JSON")
    status: str = Field(default="completed", description="running / completed / failed")
    failed_reason: str | None = Field(default=None, description="失败原因")
    env: str | None = Field(default=None, description="环境标识")


class BatchCreateScanRecordRequest(BaseModel):
    bot_id: str = Field(..., description="Bot ID")
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")
    layer: str = Field(default="L1", description="L1 / L2 / L3")
    trigger_source: str = Field(default="api", description="api / verify / schedule")
    records: list[ScanRecordCreateItem] = Field(..., description="要写入的扫描维度记录列表")


class BatchCreateScanRecordResponse(BaseModel):
    success: bool = Field(default=True)
    ids: list[int] = Field(default_factory=list, description="生成的记录主键 ID 列表")
    total: int = Field(default=0, description="写入成功的记录数")


# ── /diagnose/records/offline-batch — POST (offline T+1 sync) ─────

class OfflineBatchPatchItem(BaseModel):
    """离线T+1批量写入单条 patch 记录（无id，数据库自增）"""
    template_id: int = Field(..., description="补丁模板 ID")
    name: str = Field(..., description="补丁名称")
    layer: str | None = Field(default=None, description="L1/L2/L3，默认使用 request.layer 或 'L1'")
    description: str | None = Field(default=None, description="补丁描述")
    scope: str | None = Field(default=None, description="补丁作用域，默认 bot_id")
    content: str = Field(..., description="补丁操作内容 JSON 字符串")
    is_applied: Literal["Y", "N"] = Field(default="N", description="是否已应用，Y/N")
    env: str | None = Field(default=None, description="环境标识，默认使用 record.env 或 'dev'")
    gmt_create: str | None = Field(default=None, description="指定创建时间,格式: 2026-05-13 00:00:00")
    gmt_modified: str | None = Field(default=None, description="指定修改时间")
    advise: dict[str, Any] | None = Field(default=None, description="修复建议 JSON，包含 advise_content Markdown")


class OfflineBatchPatchResultItem(BaseModel):
    """单条 patch 处理结果"""
    id: int | None = Field(default=None, description="最终写入的 patch id")
    source_id: int | None = Field(default=None, description="当前简化契约下固定为 null")
    action: str = Field(..., description="inserted/failed")
    reason: str | None = Field(default=None, description="失败原因")


class OfflineBatchScanRecordItem(BaseModel):
    """离线T+1批量写入单条记录"""
    scan_dim: str = Field(..., description="体检维度: 任务理解力/规划执行力/能力调用力/任务交付力")
    health_score: int = Field(..., ge=0, le=100, description="健康分 0-100")
    score_grade: str | None = Field(default=None, description="excellent/good/warning/critical")
    check_items: str | None = Field(default=None, description="检查项 JSON")
    findings: str | None = Field(default=None, description="漏洞明细 JSON")
    findings_summary: str | None = Field(default=None, description="{pass: N, warning: N, fail: N}")
    duration_ms: int = Field(..., description="诊断耗时（ms）")
    scan_type: str = Field(..., description="批次标识,如: offline_20260513")
    env: str = Field(default="dev", description="环境标识")
    status: str = Field(default="completed", description="running/completed/failed")
    failed_reason: str | None = Field(default=None, description="失败原因")
    gmt_create: str | None = Field(default=None, description="指定创建时间,格式: 2026-05-13 00:00:00")
    gmt_modified: str | None = Field(default=None, description="指定修改时间")
    patches: list[OfflineBatchPatchItem] = Field(default_factory=list, description="本 record 关联的补丁列表（无id，数据库自增）；不传或为空时回写 patch_ids=[]")


class OfflineBatchScanRecordRequest(BaseModel):
    bot_id: str = Field(..., description="Bot ID")
    entity_type: str = Field(default="staff", description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")
    layer: str = Field(default="L1", description="L1/L2/L3")
    trigger_source: str = Field(default="offline", description="offline/api/schedule")
    records: list[OfflineBatchScanRecordItem] = Field(..., description="维度记录列表")


class OfflineBatchResultItem(BaseModel):
    scan_dim: str
    scan_type: str
    action: str = Field(description="inserted/updated/failed")
    id: int | None = Field(default=None, description="记录ID")
    reason: str | None = Field(default=None, description="失败原因")
    patch_ids: list[int] = Field(default_factory=list, description="最终关联的 patch id 列表")
    patches: list[OfflineBatchPatchResultItem] = Field(default_factory=list, description="各 patch 处理明细")


class OfflineBatchScanRecordResponse(BaseModel):
    success: bool = Field(default=True)
    inserted: int = Field(default=0, description="本次新增记录数")
    updated: int = Field(default=0, description="本次更新记录数")
    total: int = Field(default=0, description="总处理记录数")
    patch_inserted: int = Field(default=0, description="本次新增 patch 数")
    patch_updated: int = Field(default=0, description="本次更新 patch 数")
    patch_failed: int = Field(default=0, description="本次失败 patch 数")
    details: list[OfflineBatchResultItem] = Field(default_factory=list, description="处理明细")


# ── activated skills / mcps ─────────────────────────────────

class ActivatedSkillsResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    entity_id: str
    count: int = Field(..., description="Number of activated skills")
    skills: list[dict[str, Any]] = Field(default_factory=list)


class ActivatedMCPSResponse(BaseModel):
    success: bool = Field(default=True)
    bot_id: str
    entity_id: str
    count: int = Field(..., description="Number of activated MCP servers")
    mcps: list[dict[str, Any]] = Field(default_factory=list)


# # ── LLM chat (for testing) ──────────────────────────────────
#
# class LLMChatRequest(BaseModel):
#     system: str | None = Field(default=None, description="System prompt / role instruction")
#     user: str = Field(..., description="User prompt content")
#     model: str | None = Field(default=None, description="Override default model")
#     timeout_ms: int | None = Field(default=None, description="Override default timeout in ms")
#
#
# class LLMChatResponse(BaseModel):
#     success: bool = Field(default=True)
#     response: str = Field(..., description="LLM generated text")
#     model: str | None = Field(default=None, description="Model used")
#     disabled: bool = Field(default=False, description="Whether LLM client is disabled (env missing)")

"""Harness Engineering API router.

Endpoints:
  POST   /api/harness/diagnose              — Start async scan, returns scan_id
  GET    /api/harness/diagnose/recent        — Get latest completed scan (fixed path)
  GET    /api/harness/diagnose/records        — List scan history (fixed path)
  GET    /api/harness/diagnose/{scan_id}     — Poll scan progress
  GET    /api/harness/diagnose/{scan_id}/report — Get full scan report
  GET    /api/harness/patches
  POST   /api/harness/preview
  POST   /api/harness/apply
  Templates (CRUD):
  POST   /api/harness/templates
  GET    /api/harness/templates
  GET    /api/harness/templates/{template_id}
  PUT    /api/harness/templates/{template_id}
  DELETE /api/harness/templates/{template_id}
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.adapters.http.harness.schemas import (
    DiagnoseRequest,
    DiagnoseStartResponse,
    DiagnosePollResponse,
    DiagnoseReportResponse,
    DiagnoseRecentResponse,
    DiagnoseRecordsResponse,
    FileProgressItem,
    FindingItem,
    RecordItem,
    PatchesResponse,
    PatchTemplateItem,
    PreviewRequest,
    PreviewResponse,
    PreviewOperationItem,
    ApplyRequest,
    ApplyResponse,
    PatchOperationItem,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    TemplateDetailResponse,
    TemplateListResponse,
    DimReportItem,
    DimReportResponse,
    DimHistoryRecordItem,
    DimHistoryResponse,
    BatchCreateScanRecordRequest,
    BatchCreateScanRecordResponse,
    OfflineBatchScanRecordRequest,
    OfflineBatchScanRecordResponse,
    ConcurrentStore,
    GeneratePatchesRequest,
    GeneratePatchesResponse,
    PatchRecordItem,
    PatchRecordListResponse,
    PatchRecordRollbackResponse,
    RollbackRequest,
    PatchProgressItem,
    PatchItem,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import harness_admin
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.harness.models import (
    PatchDefinition,
    PatchRecord,
    PatchRecord as DomainPatchRecord,
    PatchStatus,
    PatchTarget,
    PatchOperation,
    FindingsReport,
)
from agentclaw.community.core.harness.repository_protocol import (
    HarnessPatchRecordRepository,
    HarnessPatchRepository,
    HarnessScanRecordRepository,
)
from agentclaw.community.core.harness.services.content_scanner import ContentScanner
from agentclaw.community.core.harness.services.patch_engine import PatchEngineError
from agentclaw.community.api.content_scanner_service import ContentScannerProtocol
from agentclaw.community.api.patch_library_service import PatchLibraryProtocol
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from agentclaw.community.api.patch_planner_service import PatchPlannerProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository

logger = get_logger()

router = APIRouter(prefix="/api/harness", tags=["harness"])


# ── in-memory progress tracking (scan execution progress per scan_id) ──

class _ScanProgress:
    """In-memory scan progress for polling (not persisted)."""

    def __init__(
        self,
        scan_id: int,
        bot_id: str,
        entity_id: str,
        scan_type: str = "full",
        layer: str = "L1",
        trigger_source: str = "api",
        bot_publish_id: str | None = None,
    ) -> None:
        from agentclaw.community.core.harness.models import Finding
        self.scan_id = scan_id
        self.bot_id = bot_id
        self.entity_id = entity_id
        self.scan_type = scan_type
        self.layer = layer
        self.trigger_source = trigger_source
        self.bot_publish_id = bot_publish_id
        self.status: str = "scanning"
        self.diagnose_progress: list[FileProgressItem] = []
        self.patch_progress: list[PatchProgressItem] = []
        self.domain_findings: list[Finding] = []
        self.summary: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        self.health_score: int | None = None
        self.grade: str | None = None
        self.cost: int | None = None
        self.failed_reason: str | None = None
        self.gmt_create: str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_progress_store: ConcurrentStore[_ScanProgress] = ConcurrentStore()


def _compute_grade(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 50:
        return "warning"
    return "critical"


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _highest_risk(*risks: str) -> str:
    """Return the highest risk level among the given values."""
    best = "low"
    for r in risks:
        if _RISK_ORDER.get(r, 0) > _RISK_ORDER.get(best, 0):
            best = r
    return best


def _get_scan_report_type(scan_type: str | None) -> str:
    """Derive scan_report_type from scan_type.

    Only offline reports are split into daily/weekly reports. Weekly reports
    contain an independent "duration" segment when scan_type is split by "_";
    other offline reports are daily. Non-offline reports are normal.
    """
    if not scan_type:
        return "normal"
    parts = str(scan_type).split("_")
    if not parts or parts[0] != "offline":
        return "normal"
    return "weekly" if "duration" in parts else "daily"


def _db_finding_to_progress(check_item: dict, findings: list[dict]) -> FileProgressItem:
    """Reconstruct a FileProgressItem from a DB check_items entry.

    Supports two formats:
    - New format: {"check_item": "AGENTS.md", "status": "completed", "cost": null, "result": "warning", "score": 75}
    - Old format: {"rule_id": "D-AGENTS-001", "rule_name": "...", "severity": "warning"}
    """
    # New format: file-type-level progress stored directly
    if "check_item" in check_item and ("result" in check_item or "status" in check_item):
        return FileProgressItem(
            check_item=check_item["check_item"],
            status=check_item.get("status", "completed"),
            cost=check_item.get("cost"),
            result=check_item.get("result"),
            result_detail=check_item.get("result_detail"),
            score=check_item.get("score"),
        )

    # Old format: rule-level entry — derive file type from findings
    file_type = check_item.get("file_type", check_item.get("rule_id", ""))
    file_findings = [f for f in findings if f.get("file_type") == file_type]
    result, score = _compute_file_result_and_score(file_findings)
    result_detail = ";".join(
        f.get("short_summary", "") for f in file_findings if f.get("short_summary")
    ) or None
    return FileProgressItem(
        check_item=file_type, status="completed", cost=None,
        result=result, result_detail=result_detail, score=score,
    )


def _file_type_to_progress(file_type: str, findings: list[dict]) -> FileProgressItem:
    """Synthesize a FileProgressItem from findings for a file type."""
    file_findings = [f for f in findings if f.get("file_type") == file_type]
    result, score = _compute_file_result_and_score(file_findings)
    result_detail = ";".join(
        f.get("short_summary", "") for f in file_findings if f.get("short_summary")
    ) or None
    return FileProgressItem(
        check_item=file_type, status="completed", cost=None,
        result=result, result_detail=result_detail, score=score,
    )


def _compute_file_result_and_score(file_findings: list[dict]) -> tuple[str, int | None]:
    """Compute file-level (result, score) from findings list.

    When findings have ``score`` fields, the average is computed and
    ``score_to_result()`` determines the pass/warning/fail result.
    When all findings in a file have explicit score=0, the result is "error"
    (LLM disabled or check failed for that file).
    When findings have no score field at all, falls back to ``result``/``severity``.
    Returns ``(result, score)`` where score is ``None`` when unavailable.
    """
    if not file_findings:
        return "pass", 100

    # Distinguish between "score field present" and "score field absent"
    has_score_field = any("score" in f and f["score"] is not None for f in file_findings)

    if has_score_field:
        scores = [f.get("score") or 0 for f in file_findings]
        has_any_positive_score = any(s > 0 for s in scores)

        if has_any_positive_score:
            positive_scores = [score for score in scores if score > 0]
            avg = sum(positive_scores) / len(positive_scores)
            from agentclaw.community.core.harness.models import score_to_result

            return score_to_result(avg), int(avg)

        # All scores are 0 → error (LLM disabled or check failed)
        return "error", 0

    # Fallback: no score fields at all, derive from result/severity
    has_fail = any(f.get("result") == "fail" for f in file_findings)
    has_warning = any(f.get("result") == "warning" for f in file_findings)
    has_pass = any(f.get("result") == "pass" for f in file_findings)
    if has_fail:
        return "fail", None
    if has_warning:
        return "warning", None
    if has_pass:
        return "pass", None

    # Fallback: derive from severity/risk_level
    has_critical = any(
        f.get("severity") == "critical" or f.get("risk_level") == "critical"
        for f in file_findings
    )
    has_other = any(
        f.get("severity") in ("warning", "info") or f.get("risk_level") in ("warning", "info")
        for f in file_findings
    )
    return "fail" if has_critical else ("warning" if has_other else "pass"), None


# ── helpers ─────────────────────────────────────────────────

def _domain_op_to_item(op) -> PatchOperationItem:
    """Convert domain PatchOperation to API PatchOperationItem."""
    return PatchOperationItem(
        op=op.op,
        target=op.target,
        template=op.template,
        detail=op.detail,
    )


def _template_to_item(tpl) -> PatchTemplateItem:
    return PatchTemplateItem(
        name=tpl.name,
        layer=tpl.layer.value if hasattr(tpl.layer, "value") else str(tpl.layer),
        description=tpl.description,
        version=tpl.version,
        risk_level=tpl.risk_level.value if hasattr(tpl.risk_level, "value") else str(tpl.risk_level),
        operations=[_domain_op_to_item(op) for op in tpl.operations],
    )


def _tpl_domain_to_response(tpl) -> TemplateDetailResponse:
    return TemplateDetailResponse(
        id=tpl.id or 0,
        name=tpl.name,
        layer=tpl.layer.value if hasattr(tpl.layer, "value") else str(tpl.layer),
        target={"files": tpl.target.files, "sections": tpl.target.sections},
        version=tpl.version,
        description=tpl.description,
        applicable_when=tpl.applicable_when,
        operations=[_domain_op_to_item(op) for op in tpl.operations],
        risk_level=tpl.risk_level.value if hasattr(tpl.risk_level, "value") else str(tpl.risk_level),
        status=tpl.status.value if hasattr(tpl.status, "value") else str(tpl.status),
        env=tpl.env,
        gmt_create=tpl.gmt_create.isoformat() if tpl.gmt_create else None,
        gmt_modified=tpl.gmt_modified.isoformat() if tpl.gmt_modified else None,
    )


def _finding_to_item(f) -> FindingItem:
    """Convert a domain Finding to API FindingItem."""
    from agentclaw.community.core.harness.models import Finding as DomainFinding
    if isinstance(f, DomainFinding):
        risk = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        return FindingItem(
            name=f.rule_name,
            detail=f.message,
            risk_level=risk,
            result=f.result,
            score=f.score,
            patch_id_list=[str(tid) for tid in f.suggested_template_ids] if f.suggested_template_ids else [],
        )
    # Already an API FindingItem
    return f


def _parse_db_findings(findings_data: list[dict]) -> list[FindingItem]:
    """Parse findings from DB (supports both old flat and new grouped format).

    Old format:  [{"rule_id": "...", "rule_name": "...", "severity": "...", ...}]
    New format:  [{"check_item": "AGENTS.md", "finding_details": [{"name": "...", ...}]}]
    """
    if not findings_data:
        return []
    # Detect format: grouped has "check_item" + "finding_details"
    first = findings_data[0]
    if "check_item" in first and "finding_details" in first:
        items: list[FindingItem] = []
        for group in findings_data:
            for detail in group.get("finding_details", []):
                risk = detail.get("risk_level", "info")
                items.append(FindingItem(
                    name=detail.get("name", ""),
                    detail=detail.get("message", ""),
                    risk_level=risk,
                    result=detail.get("result", "fail" if risk == "critical" else "warning"),
                    score=detail.get("score", 0),
                    patch_id_list=detail.get("patch_id_list", []),
                ))
        return items
    # Old flat format
    return [
        FindingItem(
            name=f.get("rule_name", f.get("name", "")),
            detail=f.get("message", f.get("detail", "")),
            risk_level=f.get("severity", f.get("risk_level", "info")),
            result=f.get("result", "fail" if f.get("severity", f.get("risk_level")) == "critical" else "warning"),
            score=f.get("score", 0),
            patch_id_list=f.get("patch_id_list", f.get("suggested_template_ids", [])),
        )
        for f in findings_data
    ]


def _parse_db_findings_raw(findings_data: list[dict]) -> list[dict]:
    """Parse findings from DB into a uniform flat dict list (for progress reconstruction).

    Supports both old flat and new grouped format.
    Returns flat dicts with file_type, severity, result, short_summary fields.
    """
    if not findings_data:
        return []
    first = findings_data[0]
    if "check_item" in first and "finding_details" in first:
        # Grouped format: flatten
        flat: list[dict] = []
        for group in findings_data:
            ft = group.get("check_item", "")
            for detail in group.get("finding_details", []):
                risk = detail.get("risk_level", "info")
                result = detail.get("result", "fail" if risk == "critical" else "warning")
                flat.append({
                    "file_type": ft,
                    "rule_name": detail.get("name", ""),
                    "message": detail.get("message", ""),
                    "severity": risk,
                    "result": result,
                    "score": detail.get("score", 0),
                    "short_summary": detail.get("name", ""),
                })
        return flat
    # Old flat format already has file_type, severity, etc.
    return findings_data


# ── background scan task ─────────────────────────────────────

def _db_record_to_findings_report(record: dict) -> FindingsReport:
    """Reconstruct FindingsReport from a DB scan record dict.

    Supports both old flat format and new grouped format.
    """
    from agentclaw.community.core.harness.models import Finding, Layer, Severity

    findings_raw = record.get("findings", "[]")
    if isinstance(findings_raw, str):
        findings_raw = json.loads(findings_raw) if findings_raw.strip() else []

    findings: list[Finding] = []
    if findings_raw and "check_item" in findings_raw[0] and "finding_details" in findings_raw[0]:
        # New grouped format: flatten finding_details
        for group in findings_raw:
            file_type = group.get("check_item", "")
            for detail in group.get("finding_details", []):
                risk = detail.get("risk_level", "info")
                try:
                    severity = Severity(risk)
                except ValueError:
                    severity = Severity.INFO
                # suggested_template_ids are numeric IDs from diagnostics
                tpl_ids: list[int] = []
                if "suggested_template_ids" in detail:
                    for v in detail.get("suggested_template_ids", []):
                        try:
                            tpl_ids.append(int(v))
                        except (ValueError, TypeError):
                            pass
                findings.append(Finding(
                    rule_id=detail.get("name", ""),
                    rule_name=detail.get("name", ""),
                    severity=severity,
                    file_type=file_type,
                    message=detail.get("message", ""),
                    suggested_template_ids=tpl_ids,
                ))
    else:
        # Old flat format
        for f in findings_raw:
            severity_str = f.get("severity", "info")
            try:
                severity = Severity(severity_str)
            except ValueError:
                severity = Severity.INFO
            tpl_ids: list[int] = []
            raw_ids = f.get("suggested_template_ids", f.get("patch_id_list", []))
            for v in raw_ids:
                try:
                    tpl_ids.append(int(v))
                except (ValueError, TypeError):
                    pass
            findings.append(Finding(
                rule_id=f.get("rule_id", ""),
                rule_name=f.get("rule_name", f.get("name", "")),
                severity=severity,
                file_type=f.get("file_type", ""),
                message=f.get("message", f.get("detail", "")),
                suggested_template_ids=tpl_ids,
            ))

    check_items_raw = record.get("check_items", "[]")
    if isinstance(check_items_raw, str):
        check_items_raw = json.loads(check_items_raw) if check_items_raw.strip() else []

    summary_raw = record.get("findings_summary", "{}")
    if isinstance(summary_raw, str):
        summary_raw = json.loads(summary_raw) if summary_raw.strip() else {}

    return FindingsReport(
        bot_id=record.get("bot_id", ""),
        entity_id=record.get("entity_id", ""),
        scan_type=record.get("scan_type", "full"),
        layer=Layer(record.get("layer", "L1")),
        health_score=record.get("health_score", 0),
        score_grade=record.get("score_grade"),
        check_items=check_items_raw,
        findings=findings,
        findings_summary=summary_raw,
        trigger_source=record.get("trigger_source", "api"),
        duration_ms=record.get("duration_ms", 0),
        status=record.get("status", "completed"),
        failed_reason=record.get("failed_reason"),
        env=record.get("env", "dev"),
        bot_publish_id=record.get("bot_publish_id"),
    )


def _serialize_findings(findings: list) -> str:
    """Serialize a list of Finding objects to grouped JSON for DB storage."""
    from agentclaw.community.core.harness.models import serialize_findings_grouped
    return serialize_findings_grouped(findings)


async def _run_scan(
    progress: _ScanProgress,
    scanner: ContentScannerProtocol,
    scan_repo: HarnessScanRecordRepository,
    entity_type: str,
    entity_id: str,
    bot_id: str,
    patch_planner: "PatchPlannerProtocol | None" = None,
) -> None:
    """Background coroutine: run diagnostics, generate patches, persist results incrementally.

    Status flow: scanning → scan_completed → patching → completed (or failed).
    If progress.bot_publish_id is set, file reads will use that publish version.
    """
    import time as _time

    from agentclaw.community.core.harness.diagnostics import DiagnosticContext
    from agentclaw.community.core.harness.models import Finding, Layer, Severity

    start = _time.time()

    try:
        diagnostics = scanner._diagnostics

        # Derive file types from diagnostics (already initialized in progress by POST handler)
        diagnostic_file_types = sorted({d.file_type for d in diagnostics if d.file_type})
        file_types = diagnostic_file_types

        # Build diagnostic context (same as ContentScanner.scan)
        ctx = DiagnosticContext(
            llm=scanner._llm,
            bot_profile=scanner._bot_profile,
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            operator_id="",
            bot_meta={},
            bot_publish_id=progress.bot_publish_id,
            mcp_center=scanner._mcp_center,
        )
        all_findings: list[Finding] = []

        # Pre-check: ensure bot files are not all empty
        all_empty = True
        for ft in file_types:
            try:
                ref = await scanner._bot_profile.read_file(
                    entity_type, entity_id, bot_id, ft, "",
                    publish_id=progress.bot_publish_id,
                )
                if ref and ref.content and ref.content.strip():
                    all_empty = False
                    break
            except Exception:
                continue
        if all_empty:
            logger.warning("[/diagnose] All bot files are empty for bot=%s", bot_id)
            progress.status = "failed"
            progress.failed_reason = "请耐心等待配置文件同步完成后，再执行诊断"
            for i, p in enumerate(progress.diagnose_progress):
                progress.diagnose_progress[i] = FileProgressItem(
                    check_item=p.check_item,
                    status="error",
                    cost=p.cost,
                    result="error",
                    result_detail="md 文件为空",
                    score=0,
                )
            # Persist error check_items to DB
            try:
                import json as _json
                check_items_json = _json.dumps(
                    [p.model_dump() for p in progress.diagnose_progress],
                    ensure_ascii=False,
                )
                scan_repo.update_findings(
                    progress.scan_id,
                    findings_json="[]",
                    findings_summary_json="{}",
                    check_items_json=check_items_json,
                    health_score=0,
                    score_grade="critical",
                )
            except Exception:
                logger.exception("Failed to persist error check_items for bot=%s", bot_id)
            try:
                scan_repo.update_status(progress.scan_id, "failed", "请耐心等待配置文件同步完成后，再执行诊断")
            except Exception:
                logger.exception("Failed to persist scan failure for bot=%s", bot_id)
            return

        # Per-file-type timing & completion tracking
        file_type_cost: dict[str, int] = {ft: 0 for ft in file_types}
        expected_diag_count: dict[str, int] = {}
        completed_diag_count: dict[str, int] = {ft: 0 for ft in file_types}
        for d in diagnostics:
            if d.file_type:
                expected_diag_count[d.file_type] = expected_diag_count.get(d.file_type, 0) + 1

        # ── Phase 1: Run diagnostics (status = "scanning") ──────────────
        progress.status = "scanning"
        try:
            scan_repo.update_status(progress.scan_id, "scanning")
        except Exception:
            logger.warning("Failed to update status to scanning for scan_id=%s", progress.scan_id)

        async def _run_one(diag):
            t0 = _time.monotonic()
            try:
                result = await diag.analyze(ctx)
            except Exception as e:
                result = e
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            return diag, result, elapsed_ms

        tasks = [asyncio.create_task(_run_one(d)) for d in diagnostics]
        file_level_check_items: list[dict] = [
            {
                "check_item": p.check_item,
                "status": p.status,
                "cost": p.cost,
                "result": p.result,
                "result_detail": p.result_detail,
            }
            for p in progress.diagnose_progress
        ]

        for coro in asyncio.as_completed(tasks):
            diag, result, elapsed_ms = await coro
            if isinstance(result, Exception):
                logger.error(
                    "[ContentScanner] Diagnostic %s (%s) FAILED: %s",
                    diag.id, diag.name, result,
                )
                all_findings.append(Finding(
                    rule_id="SYS02",
                    rule_name=f"诊断项执行失败: {diag.name}",
                    severity=Severity.CRITICAL,
                    file_type="",
                    message=f"诊断项 {diag.id} ({diag.name}) 执行时发生错误: {result}",
                    short_summary="诊断执行失败",
                    suggested_template_ids=[],
                ))
            else:
                logger.info(
                    "[ContentScanner] Diagnostic %s (%s) returned %d findings in %dms",
                    diag.id, diag.name, len(result), elapsed_ms,
                )
                all_findings.extend(result)

            # Accumulate cost per file type
            ft = diag.file_type
            if ft and ft in file_type_cost:
                file_type_cost[ft] += elapsed_ms
                completed_diag_count[ft] = completed_diag_count.get(ft, 0) + 1

            # Compute intermediate score & summary
            summary = ContentScanner._summarize(all_findings, file_types=file_types)
            health_score = ContentScanner._compute_score(all_findings, file_types=file_types)
            score_grade = _compute_grade(health_score)

            # Update in-memory progress
            progress.domain_findings = all_findings
            progress.summary = summary
            progress.health_score = health_score
            progress.grade = score_grade

            # Mark file-level progress items with cost, score, and result
            for i, ft in enumerate(file_types):
                file_findings = [f for f in all_findings if f.file_type == ft]
                is_ft_done = completed_diag_count.get(ft, 0) >= expected_diag_count.get(ft, 0)

                if is_ft_done and file_findings:
                    from agentclaw.community.core.harness.models import score_to_result
                    positive_scores = [f.score for f in file_findings if f.score > 0]
                    if positive_scores:
                        avg_score = sum(positive_scores) / len(positive_scores)
                        ft_score = int(avg_score)
                        ft_result = score_to_result(avg_score)
                    else:
                        ft_score = 0
                        ft_result = "error"
                elif is_ft_done:
                    # No findings = all diagnostics passed
                    ft_score = 100
                    ft_result = "pass"
                else:
                    ft_score = None
                    ft_result = None

                ft_detail = ";".join(
                    f.short_summary for f in file_findings if f.short_summary
                ) or None
                progress.diagnose_progress[i] = FileProgressItem(
                    check_item=ft,
                    status="completed" if is_ft_done else "running",
                    cost=file_type_cost.get(ft, 0) or None,
                    result=ft_result,
                    result_detail=ft_detail if is_ft_done else None,
                    score=ft_score,
                )

            # Build file-type-level progress for DB persistence
            file_level_check_items = [
                {
                    "check_item": p.check_item,
                    "status": p.status,
                    "cost": p.cost,
                    "result": p.result,
                    "result_detail": p.result_detail,
                    "score": p.score,
                }
                for p in progress.diagnose_progress
            ]

            # Incrementally persist findings to DB
            try:
                scan_repo.update_findings(
                    scan_id=progress.scan_id,
                    findings_json=_serialize_findings(all_findings),
                    findings_summary_json=json.dumps(summary, default=str),
                    check_items_json=json.dumps(file_level_check_items, default=str),
                    health_score=health_score,
                    score_grade=score_grade,
                )
            except Exception:
                logger.exception("Failed to persist incremental findings for bot=%s", bot_id)

        # Enrich with template suggestions
        if scanner._patch_library is not None:
            try:
                all_findings = scanner._enrich_with_templates(all_findings, {})
            except Exception:
                logger.warning("PatchLibrary enrichment failed, skipping")

        # ── Phase 2: Scan completed ─────────────────────────────────────
        duration_ms = int((_time.time() - start) * 1000)
        report = FindingsReport(
            bot_id=bot_id,
            entity_id=entity_id,
            scan_type="full",
            layer=Layer.L1,
            trigger_source="api",
            env=get_current_env(),
            bot_publish_id=progress.bot_publish_id,
        )
        report.findings = all_findings
        report.findings_summary = ContentScanner._summarize(all_findings, file_types=file_types)
        report.health_score = ContentScanner._compute_score(all_findings, file_types=file_types)
        report.score_grade = report.compute_grade()
        report.duration_ms = duration_ms
        report.check_items = file_level_check_items

        progress.cost = duration_ms
        progress.status = "scan_completed"
        progress.domain_findings = all_findings
        progress.summary = report.findings_summary
        progress.health_score = report.health_score
        progress.grade = report.score_grade

        # Persist scan_completed status
        try:
            scan_repo.update_status(progress.scan_id, "scan_completed")
        except Exception:
            logger.warning("Failed to update status to scan_completed for scan_id=%s", progress.scan_id)

        # Persist findings so far
        try:
            scan_repo.update_findings(
                scan_id=progress.scan_id,
                findings_json=_serialize_findings(all_findings),
                findings_summary_json=json.dumps(report.findings_summary, default=str),
                check_items_json=json.dumps(file_level_check_items, default=str),
                health_score=report.health_score,
                score_grade=report.score_grade,
            )
        except Exception:
            logger.exception("Failed to persist scan_completed findings for bot=%s", bot_id)

        logger.info(
            "[ContentScanner] Scan completed bot=%s findings=%d score=%d grade=%s duration=%dms",
            bot_id, len(all_findings), report.health_score, report.score_grade, duration_ms,
        )

        # ── Phase 3: Generate patches (status = "patching") ─────────────
        findings_with_patches = [f for f in all_findings if f.suggested_template_ids]
        if patch_planner is not None and findings_with_patches:
            progress.status = "patching"
            try:
                scan_repo.update_status(progress.scan_id, "patching")
            except Exception:
                logger.warning("Failed to update status to patching for scan_id=%s", progress.scan_id)

            try:
                patch_records = await patch_planner.generate_and_save_patches(
                    report=report,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    operator_id="",
                    scan_id=progress.scan_id,
                    publish_id=progress.bot_publish_id,
                )
                # Build patch_progress from results
                progress.patch_progress = [
                    {
                        "template_name": pr.target.files[0] if pr.target and pr.target.files else "",
                        "target": pr.target.files[0] if pr.target and pr.target.files else "",
                        "status": "completed",
                        "patch_id": pr.patch_id or None,
                        "record_id": pr.id or None,
                        "error": None,
                    }
                    for pr in patch_records
                ] if patch_records else []
                logger.info(
                    "[/diagnose] Patch generation completed bot=%s patches=%d",
                    bot_id, len(patch_records),
                )
            except Exception as e:
                logger.error("[/diagnose] patch generation failed: %s", e)
                # Patch generation failure doesn't fail the whole scan
                progress.patch_progress = []
        elif patch_planner is not None and not findings_with_patches:
            logger.info(
                "[ContentScanner] No actionable findings (all passed or no template associations), "
                "skipping patch generation for bot=%s", bot_id,
            )

        # ── Phase 4: Final completion ───────────────────────────────────
        progress.status = "completed"
        report.status = "completed"
        try:
            scan_repo.complete(progress.scan_id, report)
        except Exception:
            logger.exception("Failed to persist final scan record for bot=%s", bot_id)

    except Exception as e:
        logger.error("[/diagnose] scan failed: %s", e)
        progress.status = "failed"
        progress.failed_reason = str(e)

        # Persist failed status
        try:
            scan_repo.update_status(progress.scan_id, "failed", str(e))
        except Exception:
            logger.exception("Failed to persist scan failure for bot=%s", bot_id)


# ── POST /diagnose — start async scan ──────────────────────

@router.post("/diagnose", response_model=DiagnoseStartResponse, status_code=202)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$body.bot_id",
    owner_id="$body.entity_id",
))
async def diagnose(
    body: DiagnoseRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    scanner: ContentScannerProtocol = Injected(ContentScannerProtocol),
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_planner: PatchPlannerProtocol = Injected(PatchPlannerProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
):
    """Start an async diagnostic scan. Returns scan_id for polling."""
    if not body.bot_id or not body.entity_id:
        raise HTTPException(status_code=400, detail="bot_id and entity_id are required")

    if not bot_repo.get_by_id_and_owner(body.bot_id, body.entity_id):
        raise HTTPException(status_code=404, detail=f"Bot not found: {body.bot_id}")

    # Dedup check: reject if there is an unfinished scan created within 5 minutes
    if scan_repo.has_active_scan(
        body.bot_id, body.entity_id,
        within_minutes=5, bot_publish_id=body.bot_publish_id,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"当前 bot 正在诊断中，请等待",
        )

    # Derive file types from diagnostics for initial progress items
    diagnostic_file_types = sorted({d.file_type for d in scanner._diagnostics if d.file_type})
    initial_check_items = [
        {"check_item": ft, "status": "pending", "cost": None, "result": None, "result_detail": None, "score": None}
        for ft in diagnostic_file_types
    ]

    # Create initial scan record in DB and use the DB-generated ID
    scan_id: int = 0
    try:
        from agentclaw.community.core.harness.models import FindingsReport, Layer
        initial_report = FindingsReport(
            bot_id=body.bot_id,
            entity_id=body.entity_id,
            scan_type=body.scan_type,
            layer=Layer(body.layer),
            trigger_source=body.trigger_source,
            status="scanning",
            check_items=initial_check_items,
            bot_publish_id=body.bot_publish_id,
        )
        scan_id = scan_repo.create(initial_report)
    except Exception:
        logger.exception("Failed to create initial scan record")

    # Fallback to in-memory counter if DB create failed
    if not scan_id:
        scan_id = max(_progress_store.keys(), default=0) + 1

    progress = _ScanProgress(
        scan_id=scan_id,
        bot_id=body.bot_id,
        entity_id=body.entity_id,
        scan_type=body.scan_type,
        layer=body.layer,
        trigger_source=body.trigger_source,
        bot_publish_id=body.bot_publish_id,
    )

    # Initialize progress items immediately so poll returns pending status
    progress.diagnose_progress = [
        FileProgressItem(check_item=ft, status="pending", cost=None, result=None, score=None)
        for ft in diagnostic_file_types
    ]

    _progress_store[scan_id] = progress

    # Launch background scan
    asyncio.create_task(
        _run_scan(
            progress=progress,
            scanner=scanner,
            scan_repo=scan_repo,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=body.bot_id,
            patch_planner=patch_planner,
        )
    )

    return DiagnoseStartResponse(
        scan_id=scan_id,
        bot_id=body.bot_id,
        entity_id=body.entity_id,
        status="scanning",
        message="Scan started",
    )


# ── Admin-only endpoints (no collaborator permission check) ──


def _check_admin(user: AuthenticatedUser) -> None:
    """Raise 403 if the current user is not in the admin list."""
    if user.staffId not in harness_admin():
        raise HTTPException(
            status_code=403,
            detail=f"管理员接口，当前用户 {user.staffId} 无权限",
        )


@router.post("/admin/diagnose", response_model=DiagnoseStartResponse, status_code=202)
async def admin_diagnose(
    body: DiagnoseRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    scanner: ContentScannerProtocol = Injected(ContentScannerProtocol),
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_planner: PatchPlannerProtocol = Injected(PatchPlannerProtocol),
):
    """Admin-only: Start an async diagnostic scan without collaborator permission check."""
    _check_admin(user)

    if not body.bot_id or not body.entity_id:
        raise HTTPException(status_code=400, detail="bot_id and entity_id are required")

    # Dedup check: reject if there is an unfinished scan created within 5 minutes
    if scan_repo.has_active_scan(
        body.bot_id, body.entity_id,
        within_minutes=5, bot_publish_id=body.bot_publish_id,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"当前 bot 正在诊断中，请等待",
        )

    # Derive file types from diagnostics for initial progress items
    diagnostic_file_types = sorted({d.file_type for d in scanner._diagnostics if d.file_type})
    initial_check_items = [
        {"check_item": ft, "status": "pending", "cost": None, "result": None, "result_detail": None, "score": None}
        for ft in diagnostic_file_types
    ]

    # Create initial scan record in DB and use the DB-generated ID
    scan_id: int = 0
    try:
        from agentclaw.community.core.harness.models import FindingsReport, Layer
        initial_report = FindingsReport(
            bot_id=body.bot_id,
            entity_id=body.entity_id,
            scan_type=body.scan_type,
            layer=Layer(body.layer),
            trigger_source=body.trigger_source,
            status="scanning",
            check_items=initial_check_items,
            bot_publish_id=body.bot_publish_id,
        )
        scan_id = scan_repo.create(initial_report)
    except Exception:
        logger.exception("Failed to create initial scan record")

    # Fallback to in-memory counter if DB create failed
    if not scan_id:
        scan_id = max(_progress_store.keys(), default=0) + 1

    progress = _ScanProgress(
        scan_id=scan_id,
        bot_id=body.bot_id,
        entity_id=body.entity_id,
        scan_type=body.scan_type,
        layer=body.layer,
        trigger_source=body.trigger_source,
        bot_publish_id=body.bot_publish_id,
    )

    # Initialize progress items immediately so poll returns pending status
    progress.diagnose_progress = [
        FileProgressItem(check_item=ft, status="pending", cost=None, result=None, score=None)
        for ft in diagnostic_file_types
    ]

    _progress_store[scan_id] = progress

    # Launch background scan
    asyncio.create_task(
        _run_scan(
            progress=progress,
            scanner=scanner,
            scan_repo=scan_repo,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=body.bot_id,
            patch_planner=patch_planner,
        )
    )

    return DiagnoseStartResponse(
        scan_id=scan_id,
        bot_id=body.bot_id,
        entity_id=body.entity_id,
        status="scanning",
        message="Scan started",
    )


@router.post("/admin/apply", response_model=ApplyResponse)
async def admin_apply_patch(
    body: ApplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
    patch_record_repo: HarnessPatchRecordRepository = Injected(HarnessPatchRecordRepository),
):
    """Admin-only: Apply a patch without collaborator permission check."""
    _check_admin(user)

    # --- Same logic as apply_patch below ---
    if not body.record_id and not body.patch_id_list:
        raise HTTPException(status_code=400, detail="Either record_id or patch_id_list must be provided")

    logger.info("[/admin/apply] request: record_id=%s, patch_id_list=%s", body.record_id, body.patch_id_list)

    # Mode 1: Apply by record_id (from ac_harness_patch_record)
    if body.record_id:
        try:
            record = patch_record_repo.get_by_id(body.record_id)
            logger.info("[/admin/apply] fetched record: id=%s, status=%s", record.id if record else None, record.status if record else None)
            if record is None:
                raise HTTPException(status_code=404, detail=f"Patch record {body.record_id} not found")

            if record.status not in (PatchStatus.PLANNED, PatchStatus.PREVIEWED):
                raise HTTPException(
                    status_code=400,
                    detail=f"Patch record status is {record.status.value}, cannot apply",
                )

            eid = body.entity_id or ""
            logger.info("[/admin/apply] calling engine.apply for record_id=%s", body.record_id)
            record = await engine.apply(
                entity_type=body.entity_type,
                entity_id=eid,
                bot_id=record.bot_id,
                record=record,
                operations=record.operations,
                publish_id=body.bot_publish_id,
            )
            logger.info("[/admin/apply] engine.apply done, record status=%s", record.status if record else None)

            # Update ac_harness_patch is_applied status if patch_id exists
            if record.patch_id and patch_repo:
                try:
                    patch_repo.update_is_applied(record.patch_id, True)
                except Exception as e:
                    logger.warning("[/admin/apply] Failed to update patch is_applied: %s", e)

            return ApplyResponse(success=True)
        except HTTPException:
            raise
        except PatchEngineError as e:
            raise HTTPException(status_code=400, detail=e.message)
        except Exception as e:
            logger.error("[/admin/apply] error (record_id mode): %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    # Mode 2: Apply by patch_id_list (from ac_harness_patch)
    try:
        import json as _json

        for patch_id in body.patch_id_list:
            patch_def = patch_repo.get_by_id(patch_id)
            if patch_def is None:
                raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")

            # Parse operations from patch_def.content (JSON string)
            operations: list[PatchOperation] = []
            if patch_def.content:
                try:
                    ops_data = _json.loads(patch_def.content)
                    operations = [PatchOperation(**op) for op in ops_data]
                except Exception:
                    logger.warning("[/admin/apply] Failed to parse operations for patch %s", patch_id)

            # Try to find existing patch_record by patch_id, or create new
            record: PatchRecord | None = None
            if patch_record_repo:
                try:
                    record = patch_record_repo.get_by_patch_id(patch_id)
                    if record:
                        logger.info("[/admin/apply] Found existing patch_record id=%s for patch_id=%s", record.id, patch_id)
                except Exception as e:
                    logger.warning("[/admin/apply] Failed to get patch_record by patch_id: %s", e)

            if record is None:
                record = DomainPatchRecord(
                    bot_id=body.bot_id,
                    entity_id=body.entity_id or "",
                    patch_id=patch_def.id or 0,
                    layer=patch_def.layer,
                    target=PatchTarget(files=[op.target for op in operations if op.target]),
                    status=PatchStatus.PLANNED,
                    operations=operations,
                )
                # Persist to ac_harness_patch_record before apply
                if patch_record_repo:
                    try:
                        record_id = patch_record_repo.create(record)
                        record.id = record_id
                        logger.info("[/admin/apply] Created patch_record id=%s for patch_id=%s", record_id, patch_id)
                    except Exception as e:
                        logger.warning("[/admin/apply] Failed to create patch_record: %s", e)

            record = await engine.apply(
                entity_type=body.entity_type,
                entity_id=body.entity_id or "",
                bot_id=body.bot_id,
                record=record,
                operations=operations,
                publish_id=body.bot_publish_id,
            )

            # Update ac_harness_patch is_applied status
            if patch_repo:
                try:
                    patch_repo.update_is_applied(patch_id, True)
                except Exception as e:
                    logger.warning("[/admin/apply] Failed to update patch is_applied: %s", e)

        return ApplyResponse(success=True)
    except HTTPException:
        raise
    except PatchEngineError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error("[/admin/apply] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Fixed-path diagnose endpoints (MUST come before /{scan_id}) ──

@router.get("/diagnose/recent", response_model=DiagnoseRecentResponse)
async def get_recent_diagnose(
    bot_id: str = Query(..., description="Bot ID"),
    entity_id: str = Query(..., description="Entity ID"),
    scan_type: str | None = Query(default="full", description="Filter by scan type"),
    layer: str | None = Query(default="L1", description="Filter by layer"),
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """Get the latest completed scan for a bot."""
    if not bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")

    # Try DB first
    try:
        record = scan_repo.get_recent(bot_id=bot_id, entity_id=entity_id, scan_type=scan_type, layer=layer)
        if record:
            findings_data = record.get("findings", "[]")
            if isinstance(findings_data, str):
                findings_data = json.loads(findings_data)
            findings = _parse_db_findings(findings_data)
            summary = record.get("findings_summary", {})
            if isinstance(summary, str):
                summary = json.loads(summary)
            return DiagnoseRecentResponse(
                found=True,
                scan_id=record.get("id"),
                bot_id=bot_id,
                entity_id=entity_id,
                status=record.get("status", "completed"),
                scan_type=record.get("scan_type"),
                layer=record.get("layer"),
                findings=findings,
                summary=summary,
                health_score=record.get("health_score"),
                grade=record.get("score_grade"),
                cost=record.get("duration_ms"),
                gmt_create=str(record.get("gmt_create", "")),
            )
    except Exception:
        logger.exception("Failed to query recent scan from DB")

    # Fallback to in-memory
    matches = [
        r for r in _progress_store.values()
        if r.bot_id == bot_id
        and r.entity_id == entity_id
        and r.status == "completed"
        and (scan_type is None or r.scan_type == scan_type)
        and (layer is None or r.layer == layer)
    ]

    if not matches:
        return DiagnoseRecentResponse(found=False, bot_id=bot_id, entity_id=entity_id)

    latest = max(matches, key=lambda r: r.scan_id)

    return DiagnoseRecentResponse(
        found=True,
        scan_id=latest.scan_id,
        bot_id=latest.bot_id,
        entity_id=latest.entity_id,
        status=latest.status,
        scan_type=latest.scan_type,
        layer=latest.layer,
        findings=[_finding_to_item(f) for f in latest.domain_findings],
        summary=latest.summary,
        health_score=latest.health_score,
        grade=latest.grade,
        cost=latest.cost,
        gmt_create=latest.gmt_create,
    )


@router.get("/diagnose/records", response_model=DiagnoseRecordsResponse)
async def list_diagnose_records(
    bot_id: str = Query(..., description="Bot ID"),
    entity_id: str = Query(..., description="Entity ID"),
    scan_type: str | None = Query(default=None, description="Filter by scan type"),
    layer: str | None = Query(default=None, description="Filter by layer"),
    status: str | None = Query(default=None, description="Filter by status"),
    page: int = Query(default=1, ge=1, description="Page number"),
    size: int = Query(default=20, ge=1, le=100, description="Page size"),
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """List diagnostic scan history for a bot (paginated)."""
    if not bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")

    # Try DB first
    try:
        items_raw, total = scan_repo.list_records(
            bot_id=bot_id, entity_id=entity_id, page=page, size=size,
            scan_type=scan_type, layer=layer, status=status,
        )
        items = [
            RecordItem(
                scan_id=r.get("id", 0),
                bot_id=r.get("bot_id", bot_id),
                entity_id=r.get("entity_id", entity_id),
                scan_type=r.get("scan_type", "full"),
                layer=r.get("layer", "L1"),
                health_score=r.get("health_score") if r.get("status") == "completed" else None,
                grade=r.get("score_grade") if r.get("status") == "completed" else None,
                findings_summary=r.get("findings_summary") if r.get("status") == "completed" else None,
                trigger_source=r.get("trigger_source"),
                cost=r.get("duration_ms") if r.get("status") == "completed" else None,
                status=r.get("status", "running"),
                gmt_create=str(r.get("gmt_create", "")),
            )
            for r in items_raw
        ]
        return DiagnoseRecordsResponse(total=total, page=page, size=size, items=items)
    except Exception:
        logger.exception("Failed to query scan records from DB")

    # Fallback to in-memory
    matches = [
        r for r in _progress_store.values()
        if r.bot_id == bot_id
        and r.entity_id == entity_id
        and (scan_type is None or r.scan_type == scan_type)
        and (layer is None or r.layer == layer)
        and (status is None or r.status == status)
    ]

    matches.sort(key=lambda r: r.scan_id, reverse=True)

    total = len(matches)
    start = (page - 1) * size
    page_items = matches[start:start + size]

    items = [
        RecordItem(
            scan_id=r.scan_id,
            bot_id=r.bot_id,
            entity_id=r.entity_id,
            scan_type=r.scan_type,
            layer=r.layer,
            health_score=r.health_score if r.status == "completed" else None,
            grade=r.grade if r.status == "completed" else None,
            findings_summary=r.summary if r.status == "completed" else None,
            trigger_source=r.trigger_source,
            cost=r.cost if r.status == "completed" else None,
            status=r.status,
            gmt_create=r.gmt_create,
        )
        for r in page_items
    ]

    return DiagnoseRecordsResponse(
        total=total,
        page=page,
        size=size,
        items=items,
    )


@router.post("/diagnose/records", response_model=BatchCreateScanRecordResponse)
async def batch_create_scan_records(
    body: BatchCreateScanRecordRequest,
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """Batch insert scan dimension records into ac_harness_scan_record.

    Each item in `records` represents one scan_dim result.
    """
    if not body.bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")
    if not body.records:
        raise HTTPException(status_code=400, detail="records is required and must not be empty")

    # Validate health_score range for each record
    for i, rec in enumerate(body.records):
        if rec.health_score is not None and not (0 <= rec.health_score <= 100):
            raise HTTPException(
                status_code=400,
                detail=f"records[{i}].health_score must be between 0 and 100",
            )
        if rec.duration_ms is None:
            raise HTTPException(
                status_code=400,
                detail=f"records[{i}].duration_ms is required",
            )

    try:
        raw_records = [
            {
                "scan_dim": rec.scan_dim,
                "health_score": rec.health_score,
                "score_grade": rec.score_grade,
                "check_items": rec.check_items,
                "findings": rec.findings,
                "findings_summary": rec.findings_summary,
                "duration_ms": rec.duration_ms,
                "scan_type": rec.scan_type,
                "patch_ids": rec.patch_ids,
                "status": rec.status or "completed",
                "failed_reason": rec.failed_reason,
                "env": rec.env,
            }
            for rec in body.records
        ]
        ids = repo.batch_create(
            bot_id=body.bot_id,
            entity_id=body.entity_id,
            bot_publish_id=body.bot_publish_id,
            layer=body.layer,
            trigger_source=body.trigger_source,
            records=raw_records,
        )
    except Exception as e:
        logger.error(f"[/diagnose/records] batch create failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create scan records: {e}")

    return BatchCreateScanRecordResponse(
        success=True,
        ids=ids,
        total=len(ids),
    )


@router.post("/diagnose/records/offline-batch", response_model=OfflineBatchScanRecordResponse)
async def offline_batch_scan_records(
    body: OfflineBatchScanRecordRequest,
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """离线T+1批量写入接口。

    专用于离线调度系统每日跑批结果写入：
    - 按 (bot_id + entity_id + scan_type + scan_dim) upsert（匹配现有索引）
    - entity_id 在 UPDATE 时同步更新
    - 支持自定义 gmt_create/gmt_modified
    - 返回每条记录的 insert/update 结果
    """
    if not body.bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")
    if not body.records:
        raise HTTPException(status_code=400, detail="records is required and must not be empty")

    # Validate health_score range for each record
    for i, rec in enumerate(body.records):
        if rec.health_score is not None and not (0 <= rec.health_score <= 100):
            raise HTTPException(
                status_code=400,
                detail=f"records[{i}].health_score must be between 0 and 100",
            )
        if rec.duration_ms is None:
            raise HTTPException(
                status_code=400,
                detail=f"records[{i}].duration_ms is required",
            )

    try:
        # Use model_dump() without exclude_none to preserve null fields (e.g., failed_reason=None)
        # This allows clearing nullable fields during update operations
        raw_records = [rec.model_dump() for rec in body.records]
        details = repo.offline_batch(
            bot_id=body.bot_id,
            entity_id=body.entity_id,
            bot_publish_id=body.bot_publish_id,
            layer=body.layer,
            trigger_source=body.trigger_source,
            records=raw_records,
        )

        inserted = sum(
            1
            for d in details
            if d.get("action") == "inserted"
        )
        updated = sum(
            1
            for d in details
            if d.get("action") == "updated"
        )

        # Patch 统计
        patch_inserted = 0
        patch_updated = 0
        patch_failed = 0
        for d in details:
            for p in d.get("patches") or []:
                if p.get("action") == "inserted":
                    patch_inserted += 1
                elif p.get("action") == "updated":
                    patch_updated += 1
                elif p.get("action") == "failed":
                    patch_failed += 1

        return OfflineBatchScanRecordResponse(
            success=True,
            inserted=inserted,
            updated=updated,
            total=len(details),
            patch_inserted=patch_inserted,
            patch_updated=patch_updated,
            patch_failed=patch_failed,
            details=details,
        )

    except Exception as e:
        logger.error(f"[/diagnose/records/offline-batch] failed: {e}")
        raise HTTPException(status_code=500, detail=f"Batch write failed: {e}")


def _is_patch_flag(content: str | None) -> bool:
    """判断 content 是否包含可执行修复内容。

    非空 JSON list 且 length > 0 时返回 True。
    """
    if not content:
        return False
    try:
        data = json.loads(content)
        return isinstance(data, list) and len(data) > 0
    except json.JSONDecodeError:
        return False


def _parse_advise(advise_raw: str | None) -> tuple[dict[str, object] | None, bool]:
    """解析 patch advise，返回 (advise_dict, is_advise)。

    is_advise 为 True 当且仅当 advise.advise_content 为非空字符串。
    """
    if not advise_raw or not advise_raw.strip():
        return None, False
    try:
        data = json.loads(advise_raw)
        if not isinstance(data, dict):
            return None, False
        advise_content = data.get("advise_content")
        if isinstance(advise_content, str) and advise_content.strip():
            return data, True
        return data, False
    except json.JSONDecodeError:
        logger.warning(f"[/diagnose/dim-report] Failed to parse patch advise: {advise_raw[:100]}")
        return None, False


@router.get("/diagnose/dim-report", response_model=DimReportResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$entity_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def get_dim_report(
    bot_id: str = Query(..., description="Bot ID"),
    entity_id: str = Query(..., description="Entity ID"),
    bot_publish_id: str | None = Query(default=None, description="Bot publish ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
):
    """Get the latest dimension-level scan report for a bot.

    Returns one record per scan_dim (the most recent scan, regardless of status).
    When bot_publish_id is provided, only records with matching bot_publish_id are returned.
    When bot_publish_id is omitted, records with any bot_publish_id (including NULL) are returned.
    Includes patch details when patch_ids is not empty.
    """
    if not bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")

    try:
        # When bot_publish_id is None, set match_null_publish=False to get all records
        match_null_publish = bot_publish_id is not None
        rows = repo.get_latest_dim_records(
            bot_id=bot_id,
            entity_id=entity_id,
            bot_publish_id=bot_publish_id,
            match_null_publish=match_null_publish,
        )
    except Exception as e:
        logger.error(f"[/diagnose/dim-report] query failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to query scan records: {e}")

    # Collect all patch_ids from all rows for batch query
    all_patch_ids: set[int] = set()
    row_patch_map: list[tuple[int, list[int]]] = []  # (row_index, patch_id_list)

    for idx, r in enumerate(rows):
        patch_ids_raw = r.get("patch_ids")
        if patch_ids_raw:
            try:
                patch_id_list = json.loads(patch_ids_raw) if isinstance(patch_ids_raw, str) else patch_ids_raw
                if patch_id_list:
                    valid_ids = [int(pid) for pid in patch_id_list if isinstance(pid, (int, str))]
                    row_patch_map.append((idx, valid_ids))
                    all_patch_ids.update(valid_ids)
            except Exception as e:
                logger.warning(f"[/diagnose/dim-report] Failed to parse patch_ids for row {idx}: {e}")

    # Batch fetch all patches at once
    all_patches: dict[int, PatchDefinition] = {}
    if all_patch_ids:
        try:
            patch_defs = patch_repo.list_by_ids(list(all_patch_ids))
            all_patches = {p.id: p for p in patch_defs if p.id}
        except Exception as e:
            logger.error(f"[/diagnose/dim-report] Failed to batch fetch patches: {e}")

    # Build items with patches
    items: list[DimReportItem] = []
    for idx, r in enumerate(rows):
        patches: list[PatchItem] = []
        patch_ids_raw = r.get("patch_ids")

        # Find patch list for this row
        row_patch_list: list[int] = []
        for ridx, pid_list in row_patch_map:
            if ridx == idx:
                row_patch_list = pid_list
                break

        for patch_id in row_patch_list:
            patch_def = all_patches.get(patch_id)
            if not patch_def:
                continue

            # Parse operations from content JSON (原有逻辑)
            patch_ops: list[PatchOperationItem] = []
            if patch_def.content:
                try:
                    ops_data = json.loads(patch_def.content)
                    patch_ops = [
                        PatchOperationItem(
                            op=op.get("op", "update_md"),
                            target=op.get("target", ""),
                            template=op.get("template"),
                            detail=op.get("detail", {}),
                        )
                        for op in ops_data
                    ]
                except json.JSONDecodeError:
                    logger.warning(f"[/diagnose/dim-report] Failed to parse patch content for {patch_id}")

            # 计算 is_patch (新增逻辑)
            is_patch = _is_patch_flag(patch_def.content)

            # 解析 advise 和 is_advise (新增逻辑)
            advise, is_advise = _parse_advise(patch_def.advise)

            patches.append(PatchItem(
                patch_id=patch_def.id or patch_id,
                name=patch_def.name,
                description=patch_def.description,
                is_applied=patch_def.is_applied,
                layer=patch_def.layer.value if hasattr(patch_def.layer, "value") else str(patch_def.layer),
                operations=patch_ops,
                is_patch=is_patch,
                advise=advise,
                is_advise=is_advise,
                gmt_create=patch_def.gmt_create.isoformat() if patch_def.gmt_create else None,
            ))

        items.append(DimReportItem(
            scan_dim=r.get("scan_dim"),
            health_score=r.get("health_score"),
            grade=r.get("grade"),
            check_items=r.get("check_items"),
            findings=r.get("findings"),
            findings_summary=r.get("findings_summary"),
            trigger_source=r.get("trigger_source"),
            status=r.get("status", "completed"),
            failed_reason=r.get("failed_reason"),
            env=r.get("env"),
            duration_ms=r.get("duration_ms"),
            scan_type=r.get("scan_type"),
            scan_report_type=_get_scan_report_type(r.get("scan_type")),
            patch_ids=patch_ids_raw,
            patches=patches,
            gmt_create=r.get("gmt_create"),
        ))

    return DimReportResponse(
        bot_id=bot_id,
        entity_id=entity_id,
        bot_publish_id=bot_publish_id,
        items=items,
    )


@router.get("/diagnose/dim-history", response_model=DimHistoryResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$entity_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def get_dim_history(
    bot_id: str = Query(..., description="Bot ID"),
    entity_id: str = Query(..., description="Entity ID"),
    scan_dim: str | None = Query(default=None, description="Filter by scan dimension"),
    bot_publish_id: str | None = Query(default=None, description="Bot publish ID"),
    page: int = Query(default=1, ge=1, description="Page number"),
    size: int = Query(default=20, ge=1, le=100, description="Page size"),
    user: AuthenticatedUser = Depends(get_current_user),
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
):
    """List scan history records by bot dimension (paginated).

    Returns a paginated list of scan records filtered by bot_id and entity_id.
    Optionally filter by scan_dim and bot_publish_id.
    Includes patch details when patch_ids is not empty.
    """
    if not bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")

    try:
        rows, total = repo.list_dim_history(
            bot_id=bot_id,
            entity_id=entity_id,
            scan_dim=scan_dim,
            bot_publish_id=bot_publish_id,
            page=page,
            size=size,
        )
    except Exception as e:
        logger.error(f"[/diagnose/dim-history] query failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to query scan history: {e}")

    # Collect all patch_ids from all rows for batch query
    all_patch_ids: set[int] = set()
    row_patch_ids: list[tuple[int, list[int]]] = []  # (row_id, patch_id_list)

    for r in rows:
        patch_ids_raw = r.get("patch_ids")
        if patch_ids_raw:
            try:
                patch_id_list = json.loads(patch_ids_raw) if isinstance(patch_ids_raw, str) else patch_ids_raw
                if patch_id_list:
                    valid_ids = [int(pid) for pid in patch_id_list if isinstance(pid, (int, str))]
                    row_patch_ids.append((r.get("id", 0), valid_ids))
                    all_patch_ids.update(valid_ids)
            except Exception as e:
                logger.warning(f"[/diagnose/dim-history] Failed to parse patch_ids for row {r.get('id')}: {e}")

    # Batch fetch all patches at once
    all_patches: dict[int, PatchDefinition] = {}
    if all_patch_ids:
        try:
            patch_defs = patch_repo.list_by_ids(list(all_patch_ids))
            all_patches = {p.id: p for p in patch_defs if p.id}
        except Exception as e:
            logger.error(f"[/diagnose/dim-history] Failed to batch fetch patches: {e}")

    # Build cache for patch operations to avoid re-parsing same content
    patch_ops_cache: dict[int, list[PatchOperationItem]] = {}

    items: list[DimHistoryRecordItem] = []
    for r in rows:
        # Get patches for this row
        patches: list[PatchItem] = []
        row_id = r.get("id", 0)

        # Find patch_ids for this row
        row_patch_list: list[int] = []
        for rid, pid_list in row_patch_ids:
            if rid == row_id:
                row_patch_list = pid_list
                break

        for patch_id in row_patch_list:
            patch_def = all_patches.get(patch_id)
            if not patch_def:
                continue

            # Use cached operations or parse content
            if patch_id in patch_ops_cache:
                patch_ops = patch_ops_cache[patch_id]
            else:
                patch_ops: list[PatchOperationItem] = []
                if patch_def.content:
                    try:
                        ops_data = json.loads(patch_def.content)
                        patch_ops = [
                            PatchOperationItem(
                                op=op.get("op", "update_md"),
                                target=op.get("target", ""),
                                template=op.get("template"),
                                detail=op.get("detail", {}),
                            )
                            for op in ops_data
                        ]
                        patch_ops_cache[patch_id] = patch_ops
                    except json.JSONDecodeError:
                        logger.warning(f"[/diagnose/dim-history] Failed to parse patch content for {patch_id}")

            # 计算 is_patch（与 dim-report 保持一致）
            is_patch = _is_patch_flag(patch_def.content)

            patches.append(PatchItem(
                patch_id=patch_def.id or patch_id,
                name=patch_def.name,
                description=patch_def.description,
                is_applied=patch_def.is_applied,
                layer=patch_def.layer.value if hasattr(patch_def.layer, "value") else str(patch_def.layer),
                operations=patch_ops,
                is_patch=is_patch,
                gmt_create=patch_def.gmt_create.isoformat() if patch_def.gmt_create else None,
            ))

        items.append(DimHistoryRecordItem(
            id=row_id,
            bot_id=r.get("bot_id", bot_id),
            entity_id=r.get("entity_id", entity_id),
            scan_dim=r.get("scan_dim"),
            scan_type=r.get("scan_type"),
            scan_report_type=_get_scan_report_type(r.get("scan_type")),
            health_score=r.get("health_score"),
            grade=r.get("grade"),
            check_items=r.get("check_items"),
            findings=r.get("findings"),
            findings_summary=r.get("findings_summary"),
            trigger_source=r.get("trigger_source"),
            status=r.get("status", "completed"),
            failed_reason=r.get("failed_reason"),
            env=r.get("env"),
            duration_ms=r.get("duration_ms"),
            patch_ids=r.get("patch_ids"),
            patches=patches,
            bot_publish_id=r.get("bot_publish_id"),
            gmt_create=r.get("gmt_create"),
            gmt_modified=r.get("gmt_modified"),
        ))

    return DimHistoryResponse(
        bot_id=bot_id,
        entity_id=entity_id,
        scan_dim=scan_dim,
        bot_publish_id=bot_publish_id,
        total=total,
        page=page,
        size=size,
        items=items,
    )


# ── Parameterized diagnose endpoints ───────────────────────

@router.get("/diagnose/{scan_id}", response_model=DiagnosePollResponse)
async def poll_diagnose(
    scan_id: int,
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """Poll scan progress. Returns file-level status and intermediate summary."""
    # Try in-memory progress first (for running scans on this worker)
    record = _progress_store.get(scan_id)
    if record is not None:
        success = record.status != "failed"
        patch_items = [
            PatchProgressItem(
                template_name=pp["template_name"],
                target=pp["target"],
                status=pp["status"],
                patch_id=pp.get("patch_id"),
                record_id=pp.get("record_id"),
                error=pp.get("error"),
            )
            for pp in record.patch_progress
        ]
        return DiagnosePollResponse(
            success=success,
            scan_id=record.scan_id,
            bot_id=record.bot_id,
            entity_id=record.entity_id,
            status=record.status,
            scan_type=record.scan_type,
            layer=record.layer,
            diagnose_progress=record.diagnose_progress,
            patch_progress=patch_items,
            summary=record.summary,
            failed_reason=record.failed_reason,
        )

    # Fallback: read from DB (other worker may have processed the scan)
    try:
        db_record = scan_repo.get_by_id(scan_id)
    except Exception:
        logger.exception("Failed to query scan record %s from DB", scan_id)
        db_record = None

    if db_record is None:
        raise HTTPException(status_code=404, detail=f"Scan record {scan_id} not found")

    # Reconstruct progress from DB record
    findings_raw = db_record.get("findings", []) or []
    if isinstance(findings_raw, str):
        findings_raw = json.loads(findings_raw)
    findings = _parse_db_findings_raw(findings_raw)
    summary = db_record.get("findings_summary", {}) or {}
    if isinstance(summary, str):
        summary = json.loads(summary)
    check_items = db_record.get("check_items", []) or []
    if isinstance(check_items, str):
        check_items = json.loads(check_items)

    db_status = db_record.get("status", "completed")

    # Reconstruct file-level progress from DB
    progress_items: list[FileProgressItem] = []

    if check_items and isinstance(check_items[0], dict):
        # Check format: new (file-type-level) vs old (rule-level)
        first_item = check_items[0]
        if "check_item" in first_item and ("result" in first_item or "status" in first_item):
            # New format: each entry is a file-type-level progress item
            progress_items = [
                _db_finding_to_progress(item, findings)
                for item in check_items
            ]
        else:
            # Old format: rule-level entries — group by file_type from findings
            # Collect unique file types from findings and build file-level progress
            file_types = sorted({f.get("file_type", "") for f in findings if f.get("file_type")})
            progress_items = [_file_type_to_progress(ft, findings) for ft in file_types]

    # If no check_items stored, synthesize from findings
    if not progress_items and findings:
        file_types = sorted({f.get("file_type", "") for f in findings})
        progress_items = [_file_type_to_progress(ft, findings) for ft in file_types if ft]

    return DiagnosePollResponse(
        success=db_status != "failed",
        scan_id=scan_id,
        bot_id=db_record.get("bot_id", ""),
        entity_id=db_record.get("entity_id", ""),
        status=db_status,
        scan_type=db_record.get("scan_type", "full"),
        layer=db_record.get("layer", "L1"),
        diagnose_progress=progress_items,
        patch_progress=[],  # DB records don't store per-patch progress
        summary=summary,
        failed_reason=db_record.get("failed_reason"),
    )


@router.get("/diagnose/{scan_id}/report", response_model=DiagnoseReportResponse)
async def get_diagnose_report(
    scan_id: int,
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """Get the final diagnostic report. Only available when scan is completed."""
    # Try in-memory progress first
    record = _progress_store.get(scan_id)
    if record is not None:
        if record.status in ("scanning", "scan_completed", "patching"):
            raise HTTPException(
                status_code=400,
                detail=f"Scan {scan_id} is still in progress (status: {record.status}), report not available yet",
            )
        if record.status == "failed":
            raise HTTPException(
                status_code=404,
                detail=f"Scan {scan_id} failed, report not available",
            )
        return DiagnoseReportResponse(
            scan_id=record.scan_id,
            bot_id=record.bot_id,
            entity_id=record.entity_id,
            status=record.status,
            scan_type=record.scan_type,
            layer=record.layer,
            findings=[_finding_to_item(f) for f in record.domain_findings],
            summary=record.summary,
            health_score=record.health_score or 0,
            grade=record.grade or "critical",
            cost=record.cost or 0,
        )

    # Fallback: read from DB
    try:
        db_record = scan_repo.get_by_id(scan_id)
    except Exception:
        logger.exception("Failed to query scan record %s from DB", scan_id)
        db_record = None

    if db_record is None:
        raise HTTPException(status_code=404, detail=f"Scan record {scan_id} not found")

    db_status = db_record.get("status", "completed")
    if db_status in ("scanning", "scan_completed", "patching", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Scan {scan_id} is still in progress (status: {db_status}), report not available yet",
        )
    if db_status == "failed":
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} failed, report not available",
        )

    findings_raw = db_record.get("findings", []) or []
    if isinstance(findings_raw, str):
        findings_raw = json.loads(findings_raw)
    summary = db_record.get("findings_summary", {}) or {}
    if isinstance(summary, str):
        summary = json.loads(summary)

    findings = _parse_db_findings(findings_raw)
    health_score = db_record.get("health_score") or 0

    return DiagnoseReportResponse(
        scan_id=scan_id,
        bot_id=db_record.get("bot_id", ""),
        entity_id=db_record.get("entity_id", ""),
        status=db_status,
        scan_type=db_record.get("scan_type", "full"),
        layer=db_record.get("layer", "L1"),
        findings=findings,
        summary=summary,
        health_score=health_score,
        grade=_compute_grade(health_score),
        cost=db_record.get("duration_ms") or 0,
    )


# ── /patches ───────────────────────────────────────────────

@router.get("/patches", response_model=PatchesResponse)
async def list_patches(
    entity_type: str = "staff",
    entity_id: str = "",
    bot_id: str = "",
    layer: str = "L1",
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    if not bot_id or not entity_id:
        raise HTTPException(status_code=400, detail="bot_id and entity_id are required")

    templates = lib.list_applicable(
        bot_meta={"skill_sets": []},
        layer=lib._templates[0].layer if lib._templates else None,
    )
    return PatchesResponse(
        bot_id=bot_id,
        entity_id=entity_id,
        templates=[_template_to_item(t) for t in templates],
    )


# ── /preview ───────────────────────────────────────────────

@router.post("/preview", response_model=PreviewResponse)
async def preview_patch(
    body: PreviewRequest,
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    if not body.patch_id_list:
        raise HTTPException(status_code=400, detail="patch_id_list is required")

    # Validate scan_id if provided
    if body.scan_id is not None and body.scan_id not in _progress_store:
        raise HTTPException(status_code=404, detail=f"Scan record {body.scan_id} not found")

    # Collect operations and risks from patches (ac_harness_patch)
    operations: list[PatchOperation] = []
    risk_levels: list[str] = []

    for patch_id in body.patch_id_list:
        # Fetch from ac_harness_patch (PatchDefinition)
        patch = patch_repo.get_by_id(patch_id)
        if patch is None:
            raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")

        # Get risk level from template
        tpl = lib.get_template_by_id(patch.template_id) if patch.template_id else None
        risk_levels.append(str(tpl.risk_level) if tpl else "low")

        # Parse operations from patch.content (JSON string)
        if patch.content:
            try:
                ops_data = json.loads(patch.content)
                for op_data in ops_data:
                    operations.append(PatchOperation(
                        op=op_data.get("op", "update_md"),
                        target=op_data.get("target", ""),
                        template=op_data.get("template"),
                        detail=op_data.get("detail", {}),
                    ))
            except json.JSONDecodeError:
                logger.warning("[preview] Failed to parse patch.content for patch_id=%s", patch_id)

    if not operations:
        raise HTTPException(status_code=400, detail="No operations found in patches")

    # Generate preview via engine
    try:
        file_type, results = await engine.preview(
            entity_type=body.entity_type,
            entity_id=body.entity_id or "",
            bot_id=body.bot_id,
            operations=operations,
            file_type=body.file_type,
        )
    except PatchEngineError as e:
        raise HTTPException(status_code=400, detail=e.message)

    # Build response - match operations back to original for detail field
    all_operations: list[PreviewOperationItem] = []
    for (op_type, target, diff_text, content), orig_op in zip(results, operations):
        all_operations.append(PreviewOperationItem(
            op=op_type,
            target=target,
            diff=diff_text,
            op_summary=f"[{op_type}] on {target}",
            content=content,
            template=orig_op.template,
            detail=orig_op.detail or {},
        ))

    # Final content = last operation's content (engine already computed it)
    final_content = results[-1][3] if results else ""

    return PreviewResponse(
        bot_id=body.bot_id,
        scan_id=body.scan_id,
        risk_level=_highest_risk(*risk_levels) if risk_levels else "low",
        final_content=final_content,
        operations=all_operations,
    )


# ── /rollback ───────────────────────────────────────────────

@router.post("/rollback", response_model=ApplyResponse)
async def rollback_patch(
    body: RollbackRequest,
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
):
    """Rollback a patch by restoring src_content.

    For update_md operations:
    - Verifies current file content matches dst_content
    - If content has changed, rejects rollback with error
    - Otherwise restores to src_content
    """
    # Fetch patch from ac_harness_patch
    patch = patch_repo.get_by_id(body.patch_id)
    if patch is None:
        raise HTTPException(status_code=404, detail=f"Patch {body.patch_id} not found")

    # Parse operations from patch.content
    operations: list[PatchOperation] = []
    if patch.content:
        try:
            ops_data = json.loads(patch.content)
            for op_data in ops_data:
                operations.append(PatchOperation(
                    op=op_data.get("op", "update_md"),
                    target=op_data.get("target", ""),
                    template=op_data.get("template"),
                    detail=op_data.get("detail", {}),
                ))
        except json.JSONDecodeError:
            logger.warning("[rollback] Failed to parse patch.content for patch_id=%s", body.patch_id)

    if not operations:
        raise HTTPException(status_code=400, detail="No operations found in patch")

    # Perform smart rollback
    try:
        success, message = await engine.rollback_by_patch(
            entity_type=body.entity_type,
            entity_id=body.entity_id or "",
            bot_id=body.bot_id,
            patch=patch,
            operations=operations,
            file_type=body.file_type,
            publish_id=body.bot_publish_id,
        )

        if not success:
            raise HTTPException(status_code=400, detail=message)

        # Update patch is_applied status to False
        if patch_repo:
            try:
                patch_repo.update_is_applied(body.patch_id, False)
            except Exception as e:
                logger.warning("[rollback] Failed to update patch is_applied: %s", e)

        return ApplyResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[rollback] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── /apply ──────────────────────────────────────────────────

@router.post("/apply", response_model=ApplyResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$body.bot_id",
    owner_id="$body.entity_id",
))
async def apply_patch(
    body: ApplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
    patch_record_repo: HarnessPatchRecordRepository = Injected(HarnessPatchRecordRepository),
):
    """Apply a patch.

    Two modes:
    1. By record_id: Apply saved PatchRecord from ac_harness_patch_record (with stored operations)
    2. By patch_id_list: Apply patches from ac_harness_patch (with generated src_content/dst_content)
    """
    # Validate at least one mode is provided
    if not body.record_id and not body.patch_id_list:
        raise HTTPException(status_code=400, detail="Either record_id or patch_id_list must be provided")

    logger.info("[/apply] request: record_id=%s, patch_id_list=%s", body.record_id, body.patch_id_list)

    # Mode 1: Apply by record_id (from ac_harness_patch_record)
    if body.record_id:
        try:
            record = patch_record_repo.get_by_id(body.record_id)
            logger.info("[/apply] fetched record: id=%s, status=%s", record.id if record else None, record.status if record else None)
            if record is None:
                raise HTTPException(status_code=404, detail=f"Patch record {body.record_id} not found")

            if record.status not in (PatchStatus.PLANNED, PatchStatus.PREVIEWED):
                raise HTTPException(
                    status_code=400,
                    detail=f"Patch record status is {record.status.value}, cannot apply",
                )

            eid = body.entity_id or ""
            logger.info("[/apply] calling engine.apply for record_id=%s", body.record_id)
            record = await engine.apply(
                entity_type=body.entity_type,
                entity_id=eid,
                bot_id=record.bot_id,
                record=record,
                operations=record.operations,
                publish_id=body.bot_publish_id,
            )
            logger.info("[/apply] engine.apply done, record status=%s", record.status if record else None)

            # Update ac_harness_patch is_applied status if patch_id exists
            if record.patch_id and patch_repo:
                try:
                    patch_repo.update_is_applied(record.patch_id, True)
                except Exception as e:
                    logger.warning("[/apply] Failed to update patch is_applied: %s", e)

            return ApplyResponse(success=True)
        except HTTPException:
            raise
        except PatchEngineError as e:
            raise HTTPException(status_code=400, detail=e.message)
        except Exception as e:
            logger.error("[/apply] error (record_id mode): %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    # Mode 2: Apply by patch_id_list (from ac_harness_patch)
    try:
        import json

        for patch_id in body.patch_id_list:
            patch_def = patch_repo.get_by_id(patch_id)
            if patch_def is None:
                raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")

            # Parse operations from patch_def.content (JSON string)
            operations: list[PatchOperation] = []
            if patch_def.content:
                try:
                    ops_data = json.loads(patch_def.content)
                    operations = [PatchOperation(**op) for op in ops_data]
                except Exception:
                    logger.warning("[/apply] Failed to parse operations for patch %s", patch_id)

            # Try to find existing patch_record by patch_id, or create new
            record: PatchRecord | None = None
            if patch_record_repo:
                try:
                    record = patch_record_repo.get_by_patch_id(patch_id)
                    if record:
                        logger.info("[/apply] Found existing patch_record id=%s for patch_id=%s", record.id, patch_id)
                except Exception as e:
                    logger.warning("[/apply] Failed to get patch_record by patch_id: %s", e)

            if record is None:
                record = DomainPatchRecord(
                    bot_id=body.bot_id,
                    entity_id=body.entity_id or "",
                    patch_id=patch_def.id or 0,
                    layer=patch_def.layer,
                    target=PatchTarget(files=[op.target for op in operations if op.target]),
                    status=PatchStatus.PLANNED,
                    operations=operations,
                )
                # Persist to ac_harness_patch_record before apply
                if patch_record_repo:
                    try:
                        record_id = patch_record_repo.create(record)
                        record.id = record_id
                        logger.info("[/apply] Created patch_record id=%s for patch_id=%s", record_id, patch_id)
                    except Exception as e:
                        logger.warning("[/apply] Failed to create patch_record: %s", e)

            record = await engine.apply(
                entity_type=body.entity_type,
                entity_id=body.entity_id or "",
                bot_id=body.bot_id,
                record=record,
                operations=operations,
                publish_id=body.bot_publish_id,
            )

            # Update ac_harness_patch is_applied status
            if patch_repo:
                try:
                    patch_repo.update_is_applied(patch_id, True)
                except Exception as e:
                    logger.warning("[/apply] Failed to update patch is_applied: %s", e)

        return ApplyResponse(success=True)
    except HTTPException:
        raise
    except PatchEngineError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error("[/apply] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── /generate-patches ───────────────────────────────────────

@router.post("/generate-patches", response_model=GeneratePatchesResponse)
async def generate_patches(
    body: GeneratePatchesRequest,
    planner: PatchPlannerProtocol = Injected(PatchPlannerProtocol),
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
):
    """Generate patch records from the latest (or specified) scan report."""
    if not body.bot_id or not body.entity_id:
        raise HTTPException(status_code=400, detail="bot_id and entity_id are required")

    record: dict | None = None
    try:
        if body.scan_id is not None:
            record = scan_repo.get_by_id(body.scan_id)
        else:
            record = scan_repo.get_recent(bot_id=body.bot_id, entity_id=body.entity_id, scan_type="full", layer="L1")
    except Exception:
        logger.exception("Failed to fetch scan record for patch generation")

    if not record:
        raise HTTPException(status_code=404, detail="No completed scan record found for this bot")

    if record.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Scan is not completed yet")

    report = _db_record_to_findings_report(record)
    logger.info(
        "[/generate-patches] 扫描报告转换完成 bot=%s findings=%d findings_summary=%s",
        body.bot_id, len(report.findings), report.findings_summary,
    )

    try:
        patches = await planner.generate_and_save_patches(
            report=report,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=body.bot_id,
            scan_id=record.get("id"),
            publish_id=body.bot_publish_id,
        )
    except Exception as e:
        logger.error("[/generate-patches] error: %s", e)
        raise HTTPException(status_code=500, detail=f"Patch generation failed: {e}")

    logger.info(
        "[/generate-patches] 补丁生成完成 bot=%s generated=%d",
        body.bot_id, len(patches),
    )
    return GeneratePatchesResponse(
        bot_id=body.bot_id,
        generated=len(patches),
        record_ids=[p.id for p in patches if p.id],
    )


# ── /patch-records ──────────────────────────────────────────

@router.get("/patch-records", response_model=PatchRecordListResponse)
async def list_patch_records(
    bot_id: str = Query(..., description="Bot ID"),
    entity_id: str = Query(..., description="Entity ID"),
    status: str | None = Query(default=None, description="Filter by status"),
    repo: HarnessPatchRecordRepository = Injected(HarnessPatchRecordRepository),
):
    if not bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")

    try:
        records = repo.list_by_bot(bot_id=bot_id, entity_id=entity_id, status=status)
    except Exception as e:
        logger.error("[/patch-records] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    return PatchRecordListResponse(
        bot_id=bot_id,
        entity_id=entity_id,
        items=[
            PatchRecordItem(
                id=r.id or 0,
                bot_id=r.bot_id,
                entity_id=r.entity_id,
                patch_id=r.patch_id,
                layer=r.layer.value if hasattr(r.layer, "value") else str(r.layer),
                status=r.status.value if hasattr(r.status, "value") else str(r.status),
                target_files=r.target.files,
                preview_diff=r.preview_diff,
                backup_content=r.backup_content,
                applied_by=r.applied_by,
                applied_at=r.applied_at.isoformat() if r.applied_at else None,
                failed_reason=r.failed_reason,
                gmt_create=r.gmt_create.isoformat() if r.gmt_create else None,
            )
            for r in records
        ],
    )


@router.post("/patch-records/{record_id}/rollback", response_model=PatchRecordRollbackResponse)
async def rollback_patch_record(
    record_id: int,
    entity_type: str = Query(default="staff"),
    entity_id: str | None = Query(default=None),
    bot_publish_id: str | None = Query(default=None),
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    repo: HarnessPatchRecordRepository = Injected(HarnessPatchRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
):
    try:
        record = repo.get_by_id(record_id)
    except Exception as e:
        logger.error("[/patch-records/{record_id}/rollback] fetch error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch patch record")

    if record is None:
        raise HTTPException(status_code=404, detail=f"Patch record {record_id} not found")

    if record.status not in (PatchStatus.APPLIED, PatchStatus.VERIFIED):
        raise HTTPException(
            status_code=400,
            detail=f"Patch record status is {record.status.value}, cannot rollback",
        )

    eid = entity_id or ""
    try:
        # Restore files from backup_content if available
        if record.backup_content:
            try:
                backup_files = json.loads(record.backup_content)
            except json.JSONDecodeError:
                backup_files = {ft: record.backup_content for ft in record.target.files}

            for file_type, content in backup_files.items():
                from agentclaw.community.core.harness.documents import MarkdownDocument
                doc = MarkdownDocument.parse(content, bot_id=record.bot_id, file_type=file_type)
                await engine._bot_profile.write_file(
                    entity_type, eid, record.bot_id, file_type, doc,
                    publish_id=bot_publish_id,
                )

        record = await engine.rollback(
            entity_type=entity_type,
            entity_id=eid,
            bot_id=record.bot_id,
            record=record,
            publish_id=bot_publish_id,
        )
        if repo and record.id:
            repo.update_status(record.id, PatchStatus.ROLLED_BACK)

        # Update ac_harness_patch is_applied status to False if patch_id exists
        if record.patch_id and patch_repo:
            try:
                patch_repo.update_is_applied(record.patch_id, False)
            except Exception as e:
                logger.warning("[/patch-records/{record_id}/rollback] Failed to update patch is_applied: %s", e)
    except Exception as e:
        logger.error("[/patch-records/{record_id}/rollback] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    return PatchRecordRollbackResponse(
        record_id=record_id,
        status="rolled_back",
    )


# ── template management ─────────────────────────────────────

@router.post("/templates", response_model=TemplateDetailResponse)
async def create_template(
    body: TemplateCreateRequest,
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    try:
        from agentclaw.community.core.harness.models import (
            Layer, PatchOperation, PatchTarget, PatchTemplate, PatchTemplateStatus, RiskLevel,
        )
        tpl = PatchTemplate(
            name=body.name,
            layer=Layer(body.layer),
            target=PatchTarget(**body.target),
            version=1,
            description=body.description,
            applicable_when=body.applicable_when,
            operations=[PatchOperation(**op.model_dump(exclude_none=True)) for op in body.operations],
            risk_level=RiskLevel(body.risk_level),
            status=PatchTemplateStatus(body.status),
        )
        created = lib.create_template(tpl)
        return _tpl_domain_to_response(created)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[/templates.create] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    layer: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    size: int = 20,
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    try:
        offset = (page - 1) * size
        items, total = lib.list_templates(
            layer=layer, status=status, keyword=keyword, offset=offset, limit=size,
        )
        return TemplateListResponse(
            total=total,
            items=[_tpl_domain_to_response(t) for t in items],
        )
    except Exception as e:
        logger.error("[/templates.list] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/templates/{template_id}", response_model=TemplateDetailResponse)
async def get_template(
    template_id: int,
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    try:
        tpl = lib.get_template_by_id(template_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        return _tpl_domain_to_response(tpl)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[/templates.get] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/templates/{template_id}", response_model=TemplateDetailResponse)
async def update_template(
    template_id: int,
    body: TemplateUpdateRequest,
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    try:
        updates = body.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updated = lib.update_template(template_id, **updates)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        return _tpl_domain_to_response(updated)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[/templates.update] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
):
    try:
        ok = lib.delete_template(template_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        return {"success": True, "id": template_id, "message": "Template deprecated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[/templates.delete] error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# # ── LLM chat (for testing) ──────────────────────────────────
#
# @router.post("/llm-chat", response_model=LLMChatResponse)
# async def llm_chat(body: LLMChatRequest):
#     """Send a chat prompt to the configured LLM. For testing / dev use.
#
#     Returns [llm disabled] when the configured base_url / token are missing.
#     """
#     try:
#         llm = get_llm()
#         if body.model:
#             llm._model = body.model
#         if body.timeout_ms:
#             llm._timeout_ms = body.timeout_ms
#
#         text = await llm.chat(
#             system=body.system,
#             user=body.user,
#         )
#
#         return LLMChatResponse(
#             response=text,
#             model=llm._model,
#             disabled=text == "[llm disabled]",
#         )
#     except Exception as e:
#         logger.error(f"[/llm-chat] error: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

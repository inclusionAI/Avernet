"""Public /openapi/v1/bots/{bot_id}/harness router."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, ERROR_RESPONSES
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.harness.schemas import (
    HarnessDiagnoseRequest,
    HarnessPreviewRequest,
    HarnessApplyRequest,
    HarnessRollbackRequest,
)
from agentclaw.community.adapters.http.harness.schemas import (
    DiagnoseStartResponse,
    PreviewResponse,
    ApplyResponse,
    DimReportResponse,
    DimHistoryResponse,
    DimReportItem,
    DimHistoryRecordItem,
    PreviewOperationItem,
    PatchOperationItem,
    PatchItem,
)
from agentclaw.community.di import Injected
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.harness.models import (
    PatchOperation,
    PatchTarget,
    PatchRecord,
    PatchStatus,
)
from agentclaw.community.core.harness.services.content_scanner import ContentScanner
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.harness import (
    HarnessPatchRepository,
    HarnessPatchRecordRepository,
    HarnessScanRecordRepository,
)
from agentclaw.community.api.content_scanner_service import ContentScannerProtocol
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from agentclaw.community.api.patch_library_service import PatchLibraryProtocol
from agentclaw.community.api.patch_planner_service import PatchPlannerProtocol
from agentclaw.community.adapters.http.harness.router import (
    _highest_risk,
    _is_patch_flag,
    _parse_advise,
    _ScanProgress,
    _progress_store,
    _run_scan,
    _get_scan_report_type,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/harness",
    tags=["harness"],
    dependencies=[Depends(refuse_app_only_caller)],
)


async def require_harness_bot_access(
    user_id: UserIdDep,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    bot_repo: BotRepository = Injected(BotRepository),
    collaborator_service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> str:
    """Ensure the caller may operate on *bot_id* in harness context.

    Resolves the bot owner and either:
    - returns silently if the caller is the owner, or
    - checks collaborator permission via CollaboratorService.

    Raises HTTPException 404 (not 403) on failure, matching the public surface
    convention that existence and authorization are indistinguishable.
    """
    if bot_id == "default":
        return user_id

    bot = bot_repo.get_by_id(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Not found")

    owner_id = bot.get("owner_id") if isinstance(bot, dict) else getattr(bot, "owner_id", None)
    if not owner_id:
        raise HTTPException(status_code=404, detail="Not found")

    if user_id == owner_id:
        return user_id

    try:
        result = collaborator_service.check_collaborator_permission(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
            required_level=PermissionLevel.ADMIN,
        )
    except Exception as exc:
        logger.warning("[harness] collaborator check failed: %s", exc)
        raise HTTPException(status_code=404, detail="Not found") from exc

    if not result.get("has_permission"):
        raise HTTPException(status_code=404, detail="Not found")

    return user_id


HarnessBotAccessDep = Annotated[str, Depends(require_harness_bot_access)]


@router.post(
    "/diagnose",
    response_model=Envelope[DiagnoseStartResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_diagnose(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    body: HarnessDiagnoseRequest,
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    scanner: ContentScannerProtocol = Injected(ContentScannerProtocol),
    scan_repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_planner: PatchPlannerProtocol = Injected(PatchPlannerProtocol),
) -> Envelope[DiagnoseStartResponse]:
    public_scanner: ContentScanner = scanner  # type: ignore[assignment]

    diagnostic_file_types = sorted({d.file_type for d in public_scanner._diagnostics if d.file_type})
    initial_check_items = [
        {
            "check_item": ft,
            "status": "pending",
            "cost": None,
            "result": None,
            "result_detail": None,
            "score": None,
        }
        for ft in diagnostic_file_types
    ]

    from agentclaw.community.core.harness.models import FindingsReport, Layer

    initial_report = FindingsReport(
        bot_id=bot_id,
        entity_id=body.entity_id,
        scan_type=body.scan_type,
        layer=Layer(body.layer),
        trigger_source="openapi",
        status="scanning",
        check_items=initial_check_items,
        bot_publish_id=body.bot_publish_id,
    )
    scan_id = scan_repo.create(initial_report)
    if not scan_id:
        scan_id = max(_progress_store.keys(), default=0) + 1

    progress = _ScanProgress(
        scan_id=scan_id,
        bot_id=bot_id,
        entity_id=body.entity_id,
        scan_type=body.scan_type,
        layer=body.layer,
        trigger_source="openapi",
        bot_publish_id=body.bot_publish_id,
    )
    from agentclaw.community.adapters.http.harness.schemas import FileProgressItem
    progress.diagnose_progress = [
        FileProgressItem(check_item=ft, status="pending")
        for ft in diagnostic_file_types
    ]
    _progress_store[scan_id] = progress

    import asyncio
    asyncio.create_task(
        # _run_scan is re-used from the internal harness router.
        _run_scan(
            progress=progress,
            scanner=public_scanner,
            scan_repo=scan_repo,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=bot_id,
            patch_planner=patch_planner,
        )
    )

    return envelope(
        DiagnoseStartResponse(
            scan_id=scan_id,
            bot_id=bot_id,
            entity_id=body.entity_id,
            status="scanning",
            message="Scan started",
        ),
        request,
    )


@router.post(
    "/preview",
    response_model=Envelope[PreviewResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_preview(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    body: HarnessPreviewRequest,
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
    lib: PatchLibraryProtocol = Injected(PatchLibraryProtocol),
) -> Envelope[PreviewResponse]:
    if not body.patch_id_list:
        raise HTTPException(status_code=400, detail="patch_id_list is required")

    if body.scan_id is not None and body.scan_id not in _progress_store:
        raise HTTPException(status_code=404, detail=f"Scan record {body.scan_id} not found")

    operations: list[PatchOperation] = []
    risk_levels: list[str] = []

    for patch_id in body.patch_id_list:
        patch = patch_repo.get_by_id(patch_id)
        if patch is None:
            raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")

        tpl = lib.get_template_by_id(patch.template_id) if patch.template_id else None
        risk_levels.append(str(tpl.risk_level) if tpl else "low")

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
                logger.warning("[harness preview] failed to parse patch %s", patch_id)

    if not operations:
        raise HTTPException(status_code=400, detail="No operations found in patches")

    from agentclaw.community.core.harness.services.patch_engine import PatchEngineError
    try:
        file_type, results = await engine.preview(
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=bot_id,
            operations=operations,
            file_type=body.file_type,
        )
    except PatchEngineError as e:
        raise HTTPException(status_code=400, detail=e.message) from e

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

    final_content = results[-1][3] if results else ""

    return envelope(
        PreviewResponse(
            bot_id=bot_id,
            scan_id=body.scan_id,
            risk_level=_highest_risk(*risk_levels) if risk_levels else "low",
            final_content=final_content,
            operations=all_operations,
        ),
        request,
    )


@router.post(
    "/apply",
    response_model=Envelope[ApplyResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_apply(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    body: HarnessApplyRequest,
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
    patch_record_repo: HarnessPatchRecordRepository = Injected(HarnessPatchRecordRepository),
) -> Envelope[ApplyResponse]:
    if not body.record_id and not body.patch_id_list:
        raise HTTPException(status_code=400, detail="Either record_id or patch_id_list must be provided")

    if body.record_id:
        record = patch_record_repo.get_by_id(body.record_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Patch record {body.record_id} not found")
        if record.status not in (PatchStatus.PLANNED, PatchStatus.PREVIEWED):
            raise HTTPException(status_code=400, detail=f"Patch record status is {record.status.value}, cannot apply")

        record = await engine.apply(
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=bot_id,
            record=record,
            operations=record.operations,
            publish_id=body.bot_publish_id,
        )
        if record.patch_id and patch_repo:
            patch_repo.update_is_applied(record.patch_id, True)
        return envelope(ApplyResponse(success=True), request)

    for patch_id in body.patch_id_list:
        patch_def = patch_repo.get_by_id(patch_id)
        if patch_def is None:
            raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")

        operations: list[PatchOperation] = []
        if patch_def.content:
            try:
                ops_data = json.loads(patch_def.content)
                operations = [PatchOperation(**op) for op in ops_data]
            except Exception:
                logger.warning("[harness apply] failed to parse operations for patch %s", patch_id)

        record: PatchRecord | None = None
        if patch_record_repo:
            try:
                record = patch_record_repo.get_by_patch_id(patch_id)
            except Exception as e:
                logger.warning("[harness apply] failed to get patch_record by patch_id: %s", e)

        if record is None:
            record = PatchRecord(
                bot_id=bot_id,
                entity_id=body.entity_id,
                patch_id=patch_def.id or 0,
                layer=patch_def.layer,
                target=PatchTarget(files=[op.target for op in operations if op.target]),
                status=PatchStatus.PLANNED,
                operations=operations,
            )
            if patch_record_repo:
                try:
                    record.id = patch_record_repo.create(record)
                except Exception as e:
                    logger.warning("[harness apply] failed to create patch_record: %s", e)

        record = await engine.apply(
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            bot_id=bot_id,
            record=record,
            operations=operations,
            publish_id=body.bot_publish_id,
        )
        if patch_repo:
            patch_repo.update_is_applied(patch_id, True)

    return envelope(ApplyResponse(success=True), request)


@router.post(
    "/rollback",
    response_model=Envelope[ApplyResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_rollback(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    body: HarnessRollbackRequest,
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    engine: PatchEngineProtocol = Injected(PatchEngineProtocol),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
) -> Envelope[ApplyResponse]:
    patch = patch_repo.get_by_id(body.patch_id)
    if patch is None:
        raise HTTPException(status_code=404, detail=f"Patch {body.patch_id} not found")

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
            logger.warning("[harness rollback] failed to parse patch %s", body.patch_id)

    if not operations:
        raise HTTPException(status_code=400, detail="No operations found in patch")

    success, message = await engine.rollback_by_patch(
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        bot_id=bot_id,
        patch=patch,
        operations=operations,
        file_type=body.file_type,
        publish_id=body.bot_publish_id,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)

    if patch_repo:
        patch_repo.update_is_applied(body.patch_id, False)

    return envelope(ApplyResponse(success=True), request)


def _build_patches_for_row(
    row_patch_map: list[tuple[int, list[int]]],
    row_index: int,
    all_patches: dict[int, object],
) -> list[PatchItem]:
    """Build PatchItem list for a dim-report row."""
    row_patch_list = next((pid_list for idx, pid_list in row_patch_map if idx == row_index), [])
    patches = []
    for patch_id in row_patch_list:
        patch_def = all_patches.get(patch_id)
        if not patch_def:
            continue
        patch_ops = []
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
                pass
        advise, is_advise = _parse_advise(patch_def.advise)
        patches.append(PatchItem(
            patch_id=patch_def.id or patch_id,
            name=patch_def.name,
            description=patch_def.description,
            is_applied=patch_def.is_applied,
            layer=patch_def.layer.value if hasattr(patch_def.layer, "value") else str(patch_def.layer),
            operations=patch_ops,
            is_patch=_is_patch_flag(patch_def.content),
            advise=advise,
            is_advise=is_advise,
            gmt_create=patch_def.gmt_create.isoformat() if patch_def.gmt_create else None,
        ))
    return patches


def _build_patches_for_history_row(
    row_patch_ids: list[tuple[int, list[int]]],
    row_id: int,
    all_patches: dict[int, object],
    patch_ops_cache: dict[int, list[PatchOperationItem]],
) -> list[PatchItem]:
    """Build PatchItem list for a dim-history row."""
    row_patch_list = next((pid_list for rid, pid_list in row_patch_ids if rid == row_id), [])
    patches = []
    for patch_id in row_patch_list:
        patch_def = all_patches.get(patch_id)
        if not patch_def:
            continue

        if patch_id in patch_ops_cache:
            patch_ops = patch_ops_cache[patch_id]
        else:
            patch_ops = []
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
                    pass

        patches.append(PatchItem(
            patch_id=patch_def.id or patch_id,
            name=patch_def.name,
            description=patch_def.description,
            is_applied=patch_def.is_applied,
            layer=patch_def.layer.value if hasattr(patch_def.layer, "value") else str(patch_def.layer),
            operations=patch_ops,
            is_patch=_is_patch_flag(patch_def.content),
            gmt_create=patch_def.gmt_create.isoformat() if patch_def.gmt_create else None,
        ))
    return patches


@router.get(
    "/dim-report",
    response_model=Envelope[DimReportResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_dim_report(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    entity_id: Annotated[str, Query(..., description="Entity ID")],
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    bot_publish_id: Annotated[
        str | None, Query(description="Bot publish ID, to read a specific publish version")
    ] = None,
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
) -> Envelope[DimReportResponse]:
    match_null_publish = bot_publish_id is not None
    rows = repo.get_latest_dim_records(
        bot_id=bot_id,
        entity_id=entity_id,
        bot_publish_id=bot_publish_id,
        match_null_publish=match_null_publish,
    )

    all_patch_ids: set[int] = set()
    row_patch_map: list[tuple[int, list[int]]] = []
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
                logger.warning("[harness dim-report] failed to parse patch_ids for row %s: %s", idx, e)

    all_patches: dict[int, object] = {}
    if all_patch_ids:
        try:
            patch_defs = patch_repo.list_by_ids(list(all_patch_ids))
            all_patches = {p.id: p for p in patch_defs if p.id}
        except Exception as e:
            logger.error("[harness dim-report] failed to batch fetch patches: %s", e)

    items = []
    for idx, r in enumerate(rows):
        patches = _build_patches_for_row(row_patch_map, idx, all_patches)
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
            patch_ids=r.get("patch_ids"),
            patches=patches,
            gmt_create=r.get("gmt_create"),
        ))

    return envelope(
        DimReportResponse(
            bot_id=bot_id,
            entity_id=entity_id,
            bot_publish_id=bot_publish_id,
            items=items,
        ),
        request,
    )


@router.get(
    "/dim-history",
    response_model=Envelope[DimHistoryResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_dim_history(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    entity_id: Annotated[str, Query(..., description="Entity ID")],
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    scan_dim: Annotated[
        str | None, Query(description="Filter by scan dimension")
    ] = None,
    bot_publish_id: Annotated[
        str | None, Query(description="Bot publish ID, to read a specific publish version")
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number, 1-based")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
) -> Envelope[DimHistoryResponse]:
    rows, total = repo.list_dim_history(
        bot_id=bot_id,
        entity_id=entity_id,
        scan_dim=scan_dim,
        bot_publish_id=bot_publish_id,
        page=page,
        size=size,
    )

    all_patch_ids: set[int] = set()
    row_patch_ids: list[tuple[int, list[int]]] = []
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
                logger.warning("[harness dim-history] failed to parse patch_ids: %s", e)

    all_patches: dict[int, object] = {}
    if all_patch_ids:
        try:
            patch_defs = patch_repo.list_by_ids(list(all_patch_ids))
            all_patches = {p.id: p for p in patch_defs if p.id}
        except Exception as e:
            logger.error("[harness dim-history] failed to batch fetch patches: %s", e)

    patch_ops_cache: dict[int, list[PatchOperationItem]] = {}
    items = []
    for r in rows:
        row_id = r.get("id", 0)
        patches = _build_patches_for_history_row(row_patch_ids, row_id, all_patches, patch_ops_cache)
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

    return envelope(
        DimHistoryResponse(
            bot_id=bot_id,
            entity_id=entity_id,
            scan_dim=scan_dim,
            bot_publish_id=bot_publish_id,
            total=total,
            page=page,
            size=size,
            items=items,
        ),
        request,
    )

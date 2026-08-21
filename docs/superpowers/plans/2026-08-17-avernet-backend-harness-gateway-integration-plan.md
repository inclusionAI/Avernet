# Backend Harness Gateway 接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Avernet 开源版 Backend 上新增 `/openapi/v1/harness/bots/{bot_id}/*` 公共面，并将其接入 gateway。

**Architecture:** 新增独立 public harness router，使用现有的 gateway principal 鉴权（`require_principal` + `UserIdDep`），并通过 `CollaboratorService` 复用内部 harness 的协作者权限检查；handler 作为薄 wrapper 调用现有 `ContentScanner` / `PatchEngine` / `HarnessScanRecordRepository` 等 service。OpenAPI schema 通过扩展 `dump_openapi.py` 的 path-prefix 过滤能力拆分生成 `harness.openapi.json`。

**Tech Stack:** Python 3.12, FastAPI, pydantic, dependency_injector, pytest, uv

## Global Constraints

- 所有 public 路由必须声明 `require_principal`（gateway 签名验证）。
- 所有 user-scoped 路由必须声明 `UserIdDep` 作为 `user_id` query 参数。
- 成功响应统一使用 `Envelope`，错误统一用 `@envelope_errors` 映射。
- `entity_type` / `entity_id` 在 public DTO 中必须显式传入，不得默认 `"staff"`。
- 原内部 `/api/harness/*` 接口保持不动；public 路由 `trigger_source="openapi"`。
- 所有变更必须通过 `gate_and_publish_openapi.py` 兼容性检查。

## 新增/修改文件清单

| 角色 | 路径 |
|------|------|
| 创建 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/__init__.py` |
| 创建 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/schemas.py` |
| 创建 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/router.py` |
| 修改 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py` |
| 修改 | `src/backend/scripts/dump_openapi.py` |
| 修改 | `src/gateway/scripts/dump_and_publish.sh` |
| 修改 | `src/gateway/configs/application.yaml` |
| 创建 | `src/gateway/configs/schemas/harness.openapi.json`（由脚本生成） |
| 创建 | `src/backend/tests/unit/adapters/http/openapi_v1/harness/test_router.py` |
| 修改 | `src/gateway/tests/unit/core/forwarding/test_domain_map.py` |
| 修改 | `src/gateway/tests/unit/core/forwarding/test_served_openapi.py` |

---

### Task 1: 创建 public harness DTO（`schemas.py`）

**Files:**
- Create: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/schemas.py`

**Interfaces:**
- Produces: `HarnessDiagnoseRequest`, `HarnessPreviewRequest`, `HarnessApplyRequest`, `HarnessRollbackRequest`, `HarnessDimReportRequest`, `HarnessDimHistoryRequest` 六个 Pydantic model；以及内部 `harness/schemas.py` 中同名 response model 的别名导入。

- [ ] **Step 1: 编写 DTO 文件**

```python
"""Public harness surface DTOs.

These are intentionally stricter than the internal /api/harness DTOs:
- entity_type and entity_id are required (no default "staff").
- bot_id lives on the URL path and is not repeated in the body.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HarnessDiagnoseRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type, e.g. staff")
    entity_id: str = Field(..., description="Entity ID (owner/workNo)")
    scan_type: str = Field(default="full", description="full / verify")
    layer: str = Field(default="L1", description="L1 / L2 / L3")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessPreviewRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    scan_id: int | None = Field(default=None, description="Associated scan record ID")
    file_type: str | None = Field(default=None, description="Target file type")
    patch_id_list: list[int] = Field(..., description="Patch template IDs to preview")


class HarnessApplyRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    patch_id_list: list[int] = Field(
        default_factory=list, description="ac_harness_patch IDs to apply"
    )
    record_id: int | None = Field(default=None, description="ac_harness_patch_record ID")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessRollbackRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    patch_id: int = Field(..., description="Patch ID from ac_harness_patch")
    file_type: str | None = Field(default=None, description="Target file type")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessDimReportRequest(BaseModel):
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessDimHistoryRequest(BaseModel):
    scan_dim: str | None = Field(default=None, description="Filter by scan dimension")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
```

- [ ] **Step 2: 运行 backend 类型检查**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/backend
uv run pyright src/agentclaw/community/adapters/http/openapi_v1/harness/schemas.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/schemas.py
git commit -m "feat(backend): add public harness request DTOs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 创建 public harness router（`router.py`）

**Files:**
- Create: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/router.py`

**Interfaces:**
- Consumes: `UserIdDep`, `HarnessDiagnoseRequest`, `HarnessPreviewRequest`, `HarnessApplyRequest`, `HarnessRollbackRequest`, `HarnessDimReportRequest`, `HarnessDimHistoryRequest`。
- Consumes services: `ContentScannerProtocol`, `PatchEngineProtocol`, `HarnessScanRecordRepository`, `HarnessPatchRepository`, `HarnessPatchRecordRepository`, `PatchLibraryProtocol`。
- Produces: 六个 public endpoints，转发到现有 service 方法。

- [ ] **Step 1: 实现权限依赖和 router**

```python
"""Public /openapi/v1/harness/bots/{bot_id} router."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, ERROR_RESPONSES
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.harness.schemas import (
    HarnessDiagnoseRequest,
    HarnessPreviewRequest,
    HarnessApplyRequest,
    HarnessRollbackRequest,
    HarnessDimReportRequest,
    HarnessDimHistoryRequest,
)
from agentclaw.community.adapters.http.harness.schemas import (
    DiagnoseStartResponse,
    PreviewResponse,
    ApplyResponse,
    DimReportResponse,
    DimHistoryResponse,
    PreviewOperationItem,
    PatchOperationItem,
    PatchItem,
)
from agentclaw.community.di import Injected
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.gateway_principal import Principal
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
    _compute_grade,
    _domain_op_to_item,
    _finding_to_item,
    _highest_risk,
    _is_patch_flag,
    _parse_advise,
    _parse_db_findings,
    _parse_db_findings_raw,
    _ScanProgress,
    _progress_store,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter()


async def require_harness_bot_access(
    principal: Annotated[Principal, Depends(require_principal)],
    user_id: UserIdDep,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    bot_repo: BotRepository = Injected(BotRepository),
    collaborator_service: CollaboratorService = Injected(CollaboratorService),
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
```

注意：如果 `CollaboratorService` 的注入名或接口不同，请根据实际导入路径调整。`require_harness_bot_access` 使用了 public surface 的 `Principal`（即 `VerifiedCaller`）来满足 `require_principal` 依赖。

- [ ] **Step 2: 添加 diagnose endpoint**

在 `router.py` 中继续添加：

```python
@router.post(
    "/bots/{bot_id}/diagnose",
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
```

说明：`_run_scan`、`_ScanProgress`、`_progress_store` 直接从内部 `harness/router.py` 复用导入。如果导入导致循环依赖，把这些辅助函数/类迁移到 `agentclaw.community.adapters.http.harness._common` 或 core 下。

- [ ] **Step 3: 添加 preview endpoint**

```python
@router.post(
    "/bots/{bot_id}/preview",
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
```

- [ ] **Step 4: 添加 apply endpoint**

```python
@router.post(
    "/bots/{bot_id}/apply",
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
```

- [ ] **Step 5: 添加 rollback endpoint**

```python
@router.post(
    "/bots/{bot_id}/rollback",
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
```

- [ ] **Step 6: 添加 dim-report / dim-history endpoints**

```python
@router.get(
    "/bots/{bot_id}/dim-report",
    response_model=Envelope[DimReportResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_dim_report(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    entity_id: Annotated[str, Query(..., description="Entity ID")],
    body_params: HarnessDimReportRequest = Depends(),
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
) -> Envelope[DimReportResponse]:
    match_null_publish = body_params.bot_publish_id is not None
    rows = repo.get_latest_dim_records(
        bot_id=bot_id,
        entity_id=entity_id,
        bot_publish_id=body_params.bot_publish_id,
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
            bot_publish_id=body_params.bot_publish_id,
            items=items,
        ),
        request,
    )


@router.get(
    "/bots/{bot_id}/dim-history",
    response_model=Envelope[DimHistoryResponse],
    responses=ERROR_RESPONSES,
)
@envelope_errors
async def harness_dim_history(
    request: Request,
    bot_id: Annotated[str, Path(..., description="Bot ID")],
    entity_id: Annotated[str, Query(..., description="Entity ID")],
    params: HarnessDimHistoryRequest = Depends(),
    user_id: UserIdDep,
    _: HarnessBotAccessDep,
    repo: HarnessScanRecordRepository = Injected(HarnessScanRecordRepository),
    patch_repo: HarnessPatchRepository = Injected(HarnessPatchRepository),
) -> Envelope[DimHistoryResponse]:
    rows, total = repo.list_dim_history(
        bot_id=bot_id,
        entity_id=entity_id,
        scan_dim=params.scan_dim,
        bot_publish_id=params.bot_publish_id,
        page=params.page,
        size=params.size,
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
            scan_dim=params.scan_dim,
            bot_publish_id=params.bot_publish_id,
            total=total,
            page=params.page,
            size=params.size,
            items=items,
        ),
        request,
    )
```

辅助函数 `_build_patches_for_row` 和 `_build_patches_for_history_row` 可直接从内部 `harness/router.py` 的对应逻辑抽取，或复制为私有函数。例如：

```python
def _build_patches_for_row(
    row_patch_map: list[tuple[int, list[int]]],
    row_index: int,
    all_patches: dict[int, object],
) -> list[PatchItem]:
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
```

- [ ] **Step 7: 创建 `__init__.py`**

```python
from __future__ import annotations

from fastapi import APIRouter

from .router import router as harness_router


def build_harness_router() -> APIRouter:
    return harness_router


__all__ = ["build_harness_router", "harness_router"]
```

- [ ] **Step 8: 运行 backend import / 类型检查**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/backend
uv run python -c "from agentclaw.community.adapters.http.openapi_v1.harness.router import harness_router; print('ok')"
```

Expected: prints `ok` with no errors.

- [ ] **Step 9: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/harness/
git commit -m "feat(backend): add public harness router for gateway

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 挂载 harness router

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`

**Interfaces:**
- Consumes: `build_harness_router`。
- Produces: `harness_router` mounted under `/openapi/v1` before the `{bot_id}` wildcard group.

- [ ] **Step 1: import 并挂载 harness router**

在 `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py` 中：

1. 在 import block 添加：
```python
from .harness import build_harness_router
```

2. 在 `_SUBGROUPS` 列表末尾添加：
```python
    harness_router,
```

更新后的 `_SUBGROUPS` 看起来像：
```python
_SUBGROUPS = [
    authorized_apps_router,
    authorized_bots_router,
    skills_router,
    harness_router,
]
```

- [ ] **Step 2: 验证 import**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/backend
uv run python -c "from agentclaw.community.adapters.http.openapi_v1 import build_public_router; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(backend): mount public harness router in openapi_v1 surface

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 扩展 `dump_openapi.py` 支持 path-prefix 过滤

**Files:**
- Modify: `src/backend/scripts/dump_openapi.py`

**Interfaces:**
- Produces: `dump_openapi()` accepts optional `path_prefix` argument; CLI accepts optional `--path-prefix` argument.

- [ ] **Step 1: 修改脚本**

```python
"""Dump the public ``/openapi/v1`` description as the gateway's pinned artifact.

Run in CI on release: it produces the backend's *published description* (the
public surface only), which the gateway consumes to generate its served doc.
Deterministic (sorted keys) so drift/compat diffs are stable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_PUBLIC_BASE = "/openapi/v1"


def build_public_openapi(path_prefix: str = _PUBLIC_BASE) -> dict[str, Any]:
    """The backend's OpenAPI narrowed to the public ``path_prefix`` surface.

    ``path_prefix`` is typically ``/openapi/v1`` (legacy single-domain dump) or
    a more specific prefix such as ``/openapi/v1/bots`` / ``/openapi/v1/harness``
    when the gateway wants per-domain schema artifacts.
    """
    from agentclaw.community.adapters.http.app import app

    spec = app.openapi()
    public_paths = {
        path: item
        for path, item in spec.get("paths", {}).items()
        if path.startswith(path_prefix)
    }
    out = {
        key: value for key, value in spec.items() if key not in ("paths", "components")
    }
    out["paths"] = public_paths
    components = _prune_components(spec.get("components") or {}, public_paths)
    if components:
        out["components"] = components
    return out


# _prune_components, _collect_refs, _ref_parts, _resolve_ref stay unchanged


def dump_openapi(target: str | Path, *, path_prefix: str = _PUBLIC_BASE) -> dict[str, Any]:
    """Write the public description to *target* (deterministic JSON)."""
    spec = build_public_openapi(path_prefix=path_prefix)
    Path(target).write_text(
        json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return spec


if __name__ == "__main__":  # pragma: no cover - CLI entry for CI
    parser = argparse.ArgumentParser(description="Dump backend public OpenAPI")
    parser.add_argument("dest", nargs="?", default="bots.openapi.json", help="output file")
    parser.add_argument(
        "--path-prefix",
        default=_PUBLIC_BASE,
        help="only keep paths starting with this prefix",
    )
    args = parser.parse_args()
    dump_openapi(args.dest, path_prefix=args.path_prefix)
    print(f"wrote public OpenAPI to {args.dest}")
```

- [ ] **Step 2: 验证 dump 拆分可用**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/backend
uv run python scripts/dump_openapi.py /tmp/backend-full.openapi.json
uv run python scripts/dump_openapi.py /tmp/backend-bots.openapi.json --path-prefix /openapi/v1/bots
uv run python scripts/dump_openapi.py /tmp/backend-harness.openapi.json --path-prefix /openapi/v1/harness
```

Expected: 三个文件都生成；`backend-bots.openapi.json` 只含 `/openapi/v1/bots/**` 路径；`backend-harness.openapi.json` 只含 `/openapi/v1/harness/**` 路径。

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(backend): support path-prefix filtering in dump_openapi.py

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 更新 `dump_and_publish.sh` 拆分 bots / harness

**Files:**
- Modify: `src/gateway/scripts/dump_and_publish.sh`

**Interfaces:**
- Consumes: `dump_openapi.py --path-prefix`。
- Produces: 同时发布 `bots.openapi.json` 和 `harness.openapi.json`。

- [ ] **Step 1: 替换 backend dump + publish 段落**

原代码：
```bash
    # ── backend ────────────────────────────────────────────────────────────────
    _dump_upstream backend
    if ! $DRY_RUN; then
        _gate_and_publish \
            backend \
            "$SCHEMAS_DIR/bots.openapi.json" \
            "$TMPDIR/backend.openapi.json"
    fi
```

替换为：
```bash
    # ── backend ────────────────────────────────────────────────────────────────
    # bots domain
    _dump_upstream backend --path-prefix /openapi/v1/bots
    if ! $DRY_RUN; then
        _gate_and_publish \
            backend \
            "$SCHEMAS_DIR/bots.openapi.json" \
            "$TMPDIR/backend.openapi.json"
    fi

    # harness domain
    _dump_upstream backend --path-prefix /openapi/v1/harness
    if ! $DRY_RUN; then
        _gate_and_publish \
            harness \
            "$SCHEMAS_DIR/harness.openapi.json" \
            "$TMPDIR/backend.openapi.json"
    fi
```

- [ ] **Step 2: dry-run 验证**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/gateway
BACKEND_URL=http://127.0.0.1:8080 ./scripts/dump_and_publish.sh --dry-run
ls -la configs/schemas/harness.openapi.json
```

Expected: `--dry-run` 不会写入 `configs/schemas/harness.openapi.json`；但会打印 `backend dumped` 两次。

- [ ] **Step 3: 真正跑一次 publish（首次无 breaking risk）**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/gateway
BACKEND_URL=http://127.0.0.1:8080 ./scripts/dump_and_publish.sh
```

Expected: 生成 `configs/schemas/harness.openapi.json`。

- [ ] **Step 4: Commit**

```bash
git add src/gateway/scripts/dump_and_publish.sh src/gateway/configs/schemas/harness.openapi.json
git commit -m "feat(gateway): publish harness.openapi.json alongside bots

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 更新 gateway `application.yaml`

**Files:**
- Modify: `src/gateway/configs/application.yaml`

**Interfaces:**
- Produces: `harness` domain + `route_security` 规则。

- [ ] **Step 1: 在 domains 段增加 harness domain**

在 `upstreams.domains` 中，紧接 `bots` domain 后添加：

```yaml
    harness:
      match: /openapi/v1/harness/bots/**
      server: backend
      protocols: [http]
      schema:
        source: file
        path: schemas/harness.openapi.json
        refresh_seconds: 300
```

- [ ] **Step 2: 在 route_security 段增加 harness 规则**

在 `route_security` 中，紧接 `/openapi/v1/bots/**` 后添加：

```yaml
  "/openapi/v1/harness/**":
    user: required
```

- [ ] **Step 3: 验证 YAML 语法**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/gateway
uv run python -c "import yaml; yaml.safe_load(open('configs/application.yaml')); print('ok')"
```

Expected: prints `ok`。

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(gateway): add harness domain and route_security

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 添加 gateway 单元测试

**Files:**
- Modify: `src/gateway/tests/unit/core/forwarding/test_domain_map.py`
- Modify: `src/gateway/tests/unit/core/forwarding/test_served_openapi.py`

- [ ] **Step 1: domain map test**

在 `test_domain_map.py` 中，参考 `test_shipped_config_routes_bcsfuse_clean_paths` 添加：

```python
def test_shipped_config_routes_harness_clean_paths(domain_map):
    domain = domain_map.http_domain_for("/openapi/v1/harness/bots/b-123/diagnose")
    assert domain is not None
    assert domain.server.name == "backend"

    domain = domain_map.http_domain_for("/openapi/v1/harness/bots/b-456/dim-report")
    assert domain is not None
    assert domain.server.name == "backend"
```

- [ ] **Step 2: served openapi security test**

在 `test_served_openapi.py` 中，参考 `test_bcsfuse_paths_served_with_user_security` 添加：

```python
def test_harness_paths_served_with_user_security(forwarding):
    openapi = forwarding.served_openapi()
    paths = openapi.get("paths", {})
    harness_path = "/openapi/v1/harness/bots/{bot_id}/diagnose"
    assert harness_path in paths
    operation = paths[harness_path]["post"]
    security = operation.get("security", [])
    assert any("user" in s for s in security)
```

- [ ] **Step 3: 运行 gateway 测试**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/gateway
uv run pytest tests/unit/core/forwarding/test_domain_map.py tests/unit/core/forwarding/test_served_openapi.py -v
```

Expected: 测试通过。

- [ ] **Step 4: Commit**

```bash
git commit -am "test(gateway): assert harness domain and openapi security

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: backend public harness router 单元测试

**Files:**
- Create: `src/backend/tests/unit/adapters/http/openapi_v1/harness/test_router.py`

- [ ] **Step 1: 编写基本测试**

```python
"""Unit tests for public harness router."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import build_public_router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_public_router(), prefix="/openapi/v1")
    return TestClient(app)


def test_harness_diagnose_missing_principal(client: TestClient) -> None:
    resp = client.post("/openapi/v1/harness/bots/b-1/diagnose", json={"entity_type": "staff", "entity_id": "u1"})
    assert resp.status_code == 401
```

- [ ] **Step 2: 运行测试**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/Avernet/src/backend
uv run pytest tests/unit/adapters/http/openapi_v1/harness/test_router.py -v
```

Expected: 测试通过（驗证未鉴权返回 401）。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/adapters/http/openapi_v1/harness/
git commit -m "test(backend): add public harness router smoke tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: 端到端验证

- [ ] **Step 1: 本地 backend + gateway 启动后 curl**

```bash
# 替换为你的测试 gateway URL
curl -H "Authorization: Bearer <user_token>" \
     -H "Content-Type: application/json" \
     -X POST \
     "https://gateway/openapi/v1/harness/bots/<bot_id>/diagnose?user_id=<user_id>" \
     -d '{"entity_type":"staff","entity_id":"u1"}'
```

Expected: 返回 `{"code":200,"message":"OK","data":{"success":true,...}}`。

- [ ] **Step 2: 验证 schema 发布**

```bash
curl -H "Authorization: Bearer <user_token>" \
     https://gateway/openapi.json | jq '.paths | keys | map(select(startswith("/openapi/v1/harness")))'
```

Expected: 输出包含六个 harness 路径。

- [ ] **Step 3: 运行 CI 入口**

根据 `AGENTS.md` 中 backend 和 gateway 的 pre-push CI entrypoint 运行相关测试；确保不破坏现有 bots domain。

---

## 自评检查

- **Spec coverage:** 六个目标接口、auth、错误信封、schema 拆分、gateway 配置、测试已全覆盖。
- **Placeholder scan:** 无 TBD/TODO；涉及导入路径以实际代码为准。
- **Type consistency:** `UserIdDep` 与 `HarnessBotAccessDep` 类型一致；response model 复用内部 schemas。

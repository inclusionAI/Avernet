"""Capability Verify API Routes — 批量能力验证接口。"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.domain.events import get_event_bus, WorkerProfileCreatedEvent
from src.domain.models.worker import Availability, TrustLevel, WorkerType

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================================================
# Request / Response Models
# ============================================================================

class BatchVerifyRequest(BaseModel):
    """批量验证请求。"""

    worker_ids: list[str] = Field(
        ...,
        description="要验证的 Worker ID 列表",
        min_length=1,
        max_length=50,
    )
    reset_trust_level: bool = Field(
        default=True,
        description="是否先将 trust_level 重置为 unverified 再触发验证；"
        "若为 false，仅对当前 unverified 的 worker 触发",
    )


class BatchVerifyItemResult(BaseModel):
    """单个 Worker 的提交结果。"""

    worker_id: str
    status: str = Field(description="submitted / skipped / error")
    reason: str = Field(default="")


class BatchVerifyResponse(BaseModel):
    """批量验证响应。"""

    success: bool
    submitted: int = 0
    skipped: int = 0
    errors: int = 0
    items: list[BatchVerifyItemResult]


# ============================================================================
# Helpers
# ============================================================================

def _enum_value(val) -> str:
    """安全获取枚举值。"""
    return val.value if hasattr(val, "value") else str(val)


def _ensure_service_available() -> None:
    """确认能力验证服务可用，否则 503。"""
    from src.interfaces.api.dependencies.fusion_dependencies import get_capability_verify_service
    if get_capability_verify_service() is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": "Capability verify service is not enabled or LLM gateway is unavailable",
            },
        )


def _get_registry_store():
    from src.interfaces.api.dependencies.worker_dependencies import get_registry_store
    return get_registry_store()


def _should_skip(worker, *, reset: bool) -> Optional[str]:
    """判断 worker 是否应跳过验证，返回跳过原因；None 表示不跳过。"""
    if _enum_value(worker.type) != "bot":
        return f"not a bot (type={_enum_value(worker.type)})"

    if not reset and _enum_value(worker.state.trust_level) != TrustLevel.UNVERIFIED.value:
        return f"already verified (trust_level={_enum_value(worker.state.trust_level)})"

    return None


# ============================================================================
# Endpoint
# ============================================================================

@router.post(
    "/verify/batch",
    response_model=BatchVerifyResponse,
    summary="批量提交能力验证",
    description="""
为指定的 Worker 列表批量提交能力验证任务。

- reset_trust_level=true（默认）：先将 trust_level 重置为 unverified，再发布事件触发异步验证。
- reset_trust_level=false：仅对当前 trust_level==unverified 的 worker 发布事件，已验证的跳过。

验证异步执行，接口立即返回。每次最多 50 个 worker。
""",
)
async def batch_verify(request: BatchVerifyRequest):
    _ensure_service_available()

    store = _get_registry_store()
    event_bus = get_event_bus()

    submitted = 0
    skipped = 0
    errors = 0
    items: list[BatchVerifyItemResult] = []

    for worker_id in request.worker_ids:
        result = _process_single(
            worker_id=worker_id,
            store=store,
            event_bus=event_bus,
            reset=request.reset_trust_level,
        )
        items.append(result)

        if result.status == "submitted":
            submitted += 1
        elif result.status == "error":
            errors += 1
        else:
            skipped += 1

    return BatchVerifyResponse(
        success=True,
        submitted=submitted,
        skipped=skipped,
        errors=errors,
        items=items,
    )


def _process_single(
    worker_id: str,
    store,
    event_bus,
    *,
    reset: bool,
) -> BatchVerifyItemResult:
    """处理单个 worker 的验证提交。"""

    worker = store.get_by_id(worker_id)
    if worker is None:
        return BatchVerifyItemResult(worker_id=worker_id, status="skipped", reason="worker not found")

    skip_reason = _should_skip(worker, reset=reset)
    if skip_reason:
        return BatchVerifyItemResult(worker_id=worker_id, status="skipped", reason=skip_reason)

    if reset:
        store.update_trust_level(worker_id, TrustLevel.UNVERIFIED)

    event_bus.publish(WorkerProfileCreatedEvent(worker_id=worker_id))
    return BatchVerifyItemResult(worker_id=worker_id, status="submitted")


# ============================================================================
# Batch Verify All
# ============================================================================

class BatchVerifyAllRequest(BaseModel):
    """全量验证请求。"""

    reset_trust_level: bool = Field(
        default=True,
        description="是否先将 trust_level 重置为 unverified 再触发验证；"
        "若为 false，仅对当前 unverified 的 worker 触发",
    )
    dry_run: bool = Field(
        default=False,
        description="试运行模式，只返回筛选结果不实际提交验证",
    )


class BatchVerifyAllResponse(BaseModel):
    """全量批量验证响应。"""

    success: bool
    total_workers: int = 0
    filtered_workers: int = 0
    submitted: int = 0
    skipped: int = 0
    errors: int = 0
    items: list[BatchVerifyItemResult]


def _collect_target_workers(store, *, page_size: int = 200) -> tuple[int, list]:
    """分页加载所有 worker，筛选出满足条件的：
    - type == bot 且 availability == public

    Returns:
        (total_count, matched_workers)
    """
    all_count = 0
    matched: list = []
    offset = 0

    while True:
        batch = store.list(lifecycle_states=None, limit=page_size, offset=offset)
        if not batch:
            break
        all_count += len(batch)

        for w in batch:
            type_val = _enum_value(w.type)
            avail_val = _enum_value(w.state.availability) if w.state.availability else ""
            is_public_bot = type_val == WorkerType.BOT.value and avail_val == Availability.PUBLIC.value

            if is_public_bot:
                matched.append(w)

        if len(batch) < page_size:
            break
        offset += page_size

    return all_count, matched


@router.post(
    "/verify/batchAll",
    response_model=BatchVerifyAllResponse,
    summary="全量批量提交能力验证",
    description="""
自动筛选所有满足条件的 Worker 并批量提交能力验证任务。

筛选条件：
- type == bot 且 availability == public

可选择 dry_run 模式预览筛选结果而不实际提交。
""",
)
async def batch_verify_all(request: BatchVerifyAllRequest):
    _ensure_service_available()

    store = _get_registry_store()
    event_bus = get_event_bus()

    all_count, matched_workers = _collect_target_workers(store)
    filtered_count = len(matched_workers)

    if request.dry_run:
        items = [
            BatchVerifyItemResult(
                worker_id=w.id,
                status="skipped",
                reason="dry_run",
            )
            for w in matched_workers
        ]
        return BatchVerifyAllResponse(
            success=True,
            total_workers=all_count,
            filtered_workers=filtered_count,
            submitted=0,
            skipped=filtered_count,
            errors=0,
            items=items,
        )

    submitted = 0
    skipped = 0
    errors = 0
    items: list[BatchVerifyItemResult] = []

    for worker in matched_workers:
        result = _process_single(
            worker_id=worker.id,
            store=store,
            event_bus=event_bus,
            reset=request.reset_trust_level,
        )
        items.append(result)

        if result.status == "submitted":
            submitted += 1
        elif result.status == "error":
            errors += 1
        else:
            skipped += 1

    logger.info(
        "[Capability-Verify] batchAll 完成: total=%d filtered=%d submitted=%d skipped=%d errors=%d",
        all_count, filtered_count, submitted, skipped, errors,
    )

    return BatchVerifyAllResponse(
        success=True,
        total_workers=all_count,
        filtered_workers=filtered_count,
        submitted=submitted,
        skipped=skipped,
        errors=errors,
        items=items,
    )


__all__ = ["router"]
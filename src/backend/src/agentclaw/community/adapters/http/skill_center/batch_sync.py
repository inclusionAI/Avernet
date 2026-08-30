"""Legacy SC batch routes.

The old batch-sync writer is retired. Batch-delete remains unchanged and is
outside the exact-version materialization flow.
"""
import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query

from agentclaw.community.adapters.http.skill_center.schemas import (
    BatchSyncTaskResponse,
    BatchSyncTaskStatusResponse,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient

logger = get_logger()
router = APIRouter(prefix="/api/v1/skill-center", tags=["skill-center"])

REPORT_DIR = Path(os.environ.get("BATCH_SYNC_REPORT_DIR", "/home/log/logs/batch_sync_report"))

def _get_trace_id() -> str:
    """从 SOFA Tracer 获取当前请求的 trace_id，获取失败则生成一个。"""
    try:
        import opentracing
        scope = opentracing.tracer.scope_manager.active
        if scope and scope.span:
            return str(scope.span.context.trace_id)
    except Exception:
        pass
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


_BATCH_SYNC_RETIRED = (
    "Legacy batch sync is retired; use "
    "POST /openapi/v1/bots/market/skill-center/sync"
)


def _retired_batch_sync() -> None:
    raise HTTPException(status_code=410, detail=_BATCH_SYNC_RETIRED)


@router.post("/batch-sync", deprecated=True)
async def batch_sync_post() -> None:
    _retired_batch_sync()


@router.get("/batch-sync", deprecated=True)
async def batch_sync_get() -> None:
    _retired_batch_sync()


@router.get("/batch-sync/status/{task_id}", deprecated=True)
async def get_batch_sync_status(task_id: str) -> None:
    del task_id
    _retired_batch_sync()


@router.get("/batch-sync/report/{task_id}", deprecated=True)
async def get_batch_sync_report(task_id: str) -> None:
    del task_id
    _retired_batch_sync()


# ======================== 批量删除技能 ========================

_batch_delete_tasks: dict[str, dict] = {}
_batch_delete_reports: dict[str, str] = {}


async def _run_batch_delete_background(
    task_id: str, skill_codes: List[str], client: SkillCenterClient,
    repo: SkillRepository,
) -> None:
    """后台执行批量删除，更新 _batch_delete_tasks 状态。

    ``client`` and ``repo`` are captured at scheduling time
    (route-level ``Injected``).
    """
    try:
        trace_id = task_id
        report_path = str(REPORT_DIR / f"{trace_id}_delete.md")

        results = []
        success_count = 0
        failed_count = 0
        blocked_count = 0

        for skill_code in skill_codes:
            try:
                # 1. 检查是否被 Bot 关联的技能集引用
                blocked_bots = repo.check_skill_blocked_by_bot(skill_code)
                if blocked_bots:
                    results.append({
                        "skill_code": skill_code,
                        "success": False,
                        "status": "blocked",
                        "error": f"Referenced by bot(s): {', '.join(blocked_bots)}",
                    })
                    blocked_count += 1
                    logger.warning(
                        "[batch_delete] skill=%s blocked: referenced by bots %s",
                        skill_code, blocked_bots,
                    )
                    continue

                # 2. 调 SC 删除
                resp = client.delete_skill(skill_code)
                if not resp.get("success"):
                    error_msg = resp.get("message", resp.get("error", "Unknown error"))
                    results.append({
                        "skill_code": skill_code,
                        "success": False,
                        "status": "failed",
                        "error": f"SC delete failed: {error_msg}",
                    })
                    failed_count += 1
                    continue

                # 3. SC 成功后，级联清理本地 DB
                try:
                    cascade = repo.delete_by_name_with_cascade(skill_code)
                    logger.info(
                        "[batch_delete] skill=%s cleaned locally: skills=%d, set_skill=%d, member=%d",
                        skill_code,
                        cascade.get("deleted_skill_count", 0),
                        cascade.get("cleaned_set_skill", 0),
                        cascade.get("cleaned_member", 0),
                    )
                except Exception as db_exc:
                    logger.exception(
                        "[batch_delete] skill=%s SC deleted OK but local DB cleanup failed: %s",
                        skill_code, db_exc,
                    )
                    # SC 已成功，标记为成功，但记录 DB 清理警告
                    results.append({
                        "skill_code": skill_code,
                        "success": True,
                        "status": "deleted_with_warning",
                        "error": f"Local DB cleanup failed: {db_exc}",
                    })
                    success_count += 1
                    continue

                results.append({
                    "skill_code": skill_code,
                    "success": True,
                    "status": "deleted",
                    "error": None,
                })
                success_count += 1
            except Exception as exc:
                logger.exception("delete_skill failed for %s: %s", skill_code, exc)
                results.append({"skill_code": skill_code, "success": False, "status": "failed", "error": str(exc)})
                failed_count += 1

        # 生成报告
        report_content = f"""# 批量删除技能报告

**执行时间**: {datetime.now().isoformat()}
**任务 ID**: {trace_id}

## 统计信息

- 总数：{len(skill_codes)}
- 成功：{success_count}
- 失败：{failed_count}
- 跳过（被 Bot 引用）：{blocked_count}

## 详细结果

| skillCode | 状态 | 错误信息 |
|-----------|------|----------|
"""
        status_map = {"deleted": "成功", "failed": "失败", "blocked": "跳过(被Bot引用)", "deleted_with_warning": "成功(有警告)"}
        for r in results:
            error_col = r.get("error", "") or "-"
            display_status = status_map.get(r.get("status", ""), r.get("status", "-"))
            report_content += f"| {r['skill_code']} | {display_status} | {error_col} |\n"

        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            Path(report_path).write_text(report_content, encoding="utf-8")
            logger.info("Batch delete report written to %s", report_path)
        except Exception as exc:
            logger.warning("Failed to write delete report to %s: %s", report_path, exc)

        _batch_delete_reports[trace_id] = report_path
        result_data = {
            "success": failed_count == 0,
            "trace_id": trace_id,
            "total": len(skill_codes),
            "deleted": success_count,
            "failed": failed_count,
            "blocked": blocked_count,
            "results": results,
            "report_path": report_path,
        }
        _batch_delete_tasks[task_id] = {"status": "done", "progress": f"completed ({len(skill_codes)} skills)", "result": result_data, "error": ""}
        logger.info(
            "Background batch delete task %s completed: total=%d, success=%d, failed=%d, blocked=%d",
            task_id, len(skill_codes), success_count, failed_count, blocked_count,
        )
    except Exception as exc:
        logger.exception("Background batch delete task %s crashed: %s", task_id, exc)
        _batch_delete_tasks[task_id] = {"status": "error", "progress": "", "result": None, "error": str(exc)}


def _start_batch_delete_task(
    skill_codes: List[str], client: SkillCenterClient, repo: SkillRepository,
) -> BatchSyncTaskResponse:
    """创建后台删除任务，立即返回 task_id。"""
    task_id = _get_trace_id()
    _batch_delete_tasks[task_id] = {"status": "running", "progress": "starting...", "result": None, "error": ""}
    asyncio.create_task(_run_batch_delete_background(task_id, skill_codes, client, repo))
    return BatchSyncTaskResponse(task_id=task_id, status="running", message="Batch delete started in background")


@router.post("/batch-delete", response_model=BatchSyncTaskResponse)
async def batch_delete_post(
    request: dict,
    client: SkillCenterClient = Injected(SkillCenterClient),
    repo: SkillRepository = Injected(SkillRepository),
):
    """JSON body 调用批量删除（异步，立即返回 task_id）。

    Body: { "skill_codes": ["skill-1", "skill-2"] }
    """
    skill_codes = request.get("skill_codes", [])
    if not skill_codes:
        raise HTTPException(status_code=400, detail="skill_codes is required")
    return _start_batch_delete_task(skill_codes, client, repo)


@router.get("/batch-delete", response_model=BatchSyncTaskResponse)
async def batch_delete_get(
    skill_codes: str = Query(..., description="逗号分隔的技能列表"),
    client: SkillCenterClient = Injected(SkillCenterClient),
    repo: SkillRepository = Injected(SkillRepository),
):
    """浏览器直接访问触发批量删除（异步，立即返回 task_id）。"""
    codes = [c.strip() for c in skill_codes.split(",") if c.strip()]
    if not codes:
        raise HTTPException(status_code=400, detail="skill_codes is required")
    return _start_batch_delete_task(codes, client, repo)


@router.get("/batch-delete/status/{task_id}", response_model=BatchSyncTaskStatusResponse)
async def get_batch_delete_status(task_id: str):
    """轮询批量删除任务状态。"""
    task = _batch_delete_tasks.get(task_id)
    if not task:
        candidate = REPORT_DIR / f"{task_id}_delete.md"
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8")
                import re
                success_match = re.search(r"成功：\s*(\d+)", content, re.IGNORECASE)
                failed_match = re.search(r"失败：\s*(\d+)", content, re.IGNORECASE)
                total_match = re.search(r"总数：\s*(\d+)", content, re.IGNORECASE)
                blocked_match = re.search(r"跳过.*被\s*Bot.*引用.*：\s*(\d+)", content)

                result_data = {
                    "success": int(success_match.group(1)) if success_match else 0,
                    "trace_id": task_id,
                    "total": int(total_match.group(1)) if total_match else 0,
                    "deleted": int(success_match.group(1)) if success_match else 0,
                    "failed": int(failed_match.group(1)) if failed_match else 0,
                    "blocked": int(blocked_match.group(1)) if blocked_match else 0,
                    "results": [],
                    "report_path": str(candidate),
                }
            except Exception:
                result_data = {
                    "success": True,
                    "trace_id": task_id,
                    "total": 0,
                    "deleted": 0,
                    "failed": 0,
                    "blocked": 0,
                    "results": [],
                    "report_path": str(candidate),
                }
            return BatchSyncTaskStatusResponse(
                task_id=task_id,
                status="done",
                progress="completed (from disk)",
                result=result_data,
                error="",
            )
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return BatchSyncTaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        progress=task.get("progress", ""),
        result=task.get("result"),
        error=task.get("error", ""),
    )


@router.get("/batch-delete/report/{task_id}")
async def get_batch_delete_report(task_id: str):
    """查看指定 task_id 的删除报告。"""
    report_path = _batch_delete_reports.get(task_id)
    if not report_path:
        candidate = REPORT_DIR / f"{task_id}_delete.md"
        if candidate.is_file():
            report_path = str(candidate)
        else:
            raise HTTPException(status_code=404, detail=f"Report not found: {task_id}")

    try:
        content = Path(report_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Report file missing: {report_path}")

    return {"task_id": task_id, "report_path": report_path, "content": content}

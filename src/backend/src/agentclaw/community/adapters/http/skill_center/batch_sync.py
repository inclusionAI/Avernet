"""批量同步技能到 SkillCenter — HTTP API（异步模式）。

POST /api/v1/skill-center/batch-sync                发起异步任务，立即返回 task_id
GET  /api/v1/skill-center/batch-sync                同上（浏览器直接访问）
GET  /api/v1/skill-center/batch-sync/status/{task_id}  轮询任务状态
GET  /api/v1/skill-center/batch-sync/report/{task_id}  查看报告

用法示例:
  # 发起全量同步（浏览器）
  GET /api/v1/skill-center/batch-sync

  # 指定 skill（逗号分隔）
  GET /api/v1/skill-center/batch-sync?skill_codes=alarm-inspector,odps-sql-generator

  # 强制重发
  GET /api/v1/skill-center/batch-sync?skill_codes=alarm-inspector&force=true

  # POST 调用（推荐）
  POST /api/v1/skill-center/batch-sync
  Body: { "skill_codes": ["alarm-inspector"], "force": false }

  # 轮询状态
  GET /api/v1/skill-center/batch-sync/status/{task_id}
"""
import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.skill_center.schemas import (
    BatchSyncRequest,
    BatchSyncResponse,
    BatchSyncSkillResult,
    BatchSyncTaskResponse,
    BatchSyncTaskStatusResponse,
)
from agentclaw.community.core.skill_center.services.skill_batch_sync_service import (
    BatchSyncReport,
    SyncResult,
    generate_report,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.di import Injected
from agentclaw.community.api.git_sync_service import GitSyncServiceProtocol
from agentclaw.community.api.skill_batch_sync_service import SkillBatchSyncServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()
router = APIRouter(prefix="/api/v1/skill-center", tags=["skill-center"])

REPORT_DIR = Path(os.environ.get("BATCH_SYNC_REPORT_DIR", "/home/log/logs/batch_sync_report"))

# 内存存储：task_id → report_path（进程重启后丢失，不影响已落盘的报告）
_task_reports: dict[str, str] = {}

# 内存存储：task_id → task state
_tasks: dict[str, dict] = {}


def _build_response(report: BatchSyncReport, report_path: str) -> BatchSyncResponse:
    results = [
        BatchSyncSkillResult(
            skill_code=r.skill_code,
            success=r.success,
            status=r.status,
            skipped=r.skipped,
            error=r.error,
        )
        for r in report.results
    ]
    return BatchSyncResponse(
        success=report.failed == 0,
        trace_id=report.trace_id,
        total=report.total,
        skipped=report.skipped,
        success_count=report.success,
        failed=report.failed,
        results=results,
        report_path=report_path,
    )


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


async def _run_batch_sync_background(
    task_id: str,
    skill_codes: Optional[List[str]],
    force: bool,
    skills_dir: Optional[str],
    batch_size: Optional[int],
    batch_index: int,
    git_sync: GitSyncServiceProtocol,
    svc: SkillBatchSyncServiceProtocol,
    envs: Optional[List[str]] = None,
) -> None:
    """后台执行批量同步，更新 _tasks 状态。

    ``git_sync`` and ``svc`` are captured at scheduling time so the
    background closure does not invoke the service-locator.
    """
    effective_envs = envs if envs else [None]  # None 表示用当前环境
    logger.info("[batch_sync] task_id=%s, envs=%s, effective_envs=%s", task_id, envs, effective_envs)
    try:
        trace_id = task_id
        report_path = str(REPORT_DIR / f"{trace_id}.md")

        # 先触发 git sync 拉取最新 skills 仓库
        sync_ok = False
        try:
            if git_sync:
                sync_result = await git_sync.sync()
                sync_ok = sync_result.get("success", False) if isinstance(sync_result, dict) else False
                if not sync_ok:
                    logger.warning("Pre-sync git returned failure: %s", sync_result)
                else:
                    logger.info("Pre-sync git result: %s", sync_result)
                    logger.info("[batch_sync][task_id=%s] git sync OK, sync_ok=%s", task_id, sync_ok)
        except Exception as exc:
            logger.warning("Pre-sync git failed (continuing with existing repo): %s", exc)

        # 如果 git sync 失败且未指定 skills_dir，先尝试 bootstrap 再重试一次
        if not sync_ok and not skills_dir:
            try:
                if git_sync:
                    bootstrap_result = await git_sync.sync_bootstrap()
                    logger.info("GitSyncService bootstrap result: %s", bootstrap_result)
                    if bootstrap_result.get("success"):
                        sync_result = await git_sync.sync()
                        sync_ok = sync_result.get("success", False) if isinstance(sync_result, dict) else False
                        logger.info("[batch_sync][task_id=%s] post-bootstrap git sync result: sync_ok=%s", task_id, sync_ok)
            except Exception as exc:
                logger.warning("Bootstrap + retry sync failed: %s", exc)

        # 分批模式：先扫描全量 skill_codes，再按 batch_size 切片
        effective_codes = skill_codes
        total_scanned = 0
        total_batches = 0
        if batch_size is not None:
            if skill_codes is None:
                try:
                    from pathlib import Path as _Path
                    scan_dir = _Path(skills_dir) if skills_dir else svc.get_default_skills_dir()
                    all_dirs = await run_in_threadpool(svc._scan_skills, scan_dir)
                    all_codes = [d.name for d in all_dirs]
                    total_scanned = len(all_codes)
                except Exception as exc:
                    logger.exception("Failed to scan skills for batching: %s", exc)
                    _tasks[task_id] = {"status": "error", "progress": "", "result": None, "error": f"Scan failed: {exc}"}
                    return
            else:
                all_codes = skill_codes
                total_scanned = len(all_codes)

            total_batches = (total_scanned + batch_size - 1) // batch_size
            if batch_index >= total_batches:
                _tasks[task_id] = {
                    "status": "error", "progress": "", "result": None,
                    "error": f"batch_index={batch_index} out of range, total_batches={total_batches}",
                }
                return
            start = batch_index * batch_size
            end = min(start + batch_size, total_scanned)
            effective_codes = all_codes[start:end]
            _tasks[task_id]["progress"] = f"batch {batch_index + 1}/{total_batches} ({len(effective_codes)} skills)"
            logger.info(
                "Batch mode: total=%d, batch_size=%d, batch_index=%d, this_batch=%d, codes=%s",
                total_scanned, batch_size, batch_index, len(effective_codes),
                effective_codes[:5] if len(effective_codes) > 5 else effective_codes,
            )

        try:
            all_reports: list[BatchSyncReport] = []
            for env in effective_envs:
                env_label = env if env else get_current_env()
                logger.info("[batch_sync] starting sync for env=%s, effective_codes=%s", env_label, effective_codes)
                try:
                    report: BatchSyncReport = await run_in_threadpool(
                        svc.run, skills_dir=skills_dir, skill_codes=effective_codes, env=env,
                    )
                    report.env = env_label
                    all_reports.append(report)
                    logger.info(
                        "[batch_sync] env=%s completed: total=%d, success=%d, failed=%d, skipped=%d",
                        env_label, report.total, report.success, report.failed, report.skipped,
                    )
                except Exception as exc:
                    logger.exception("[batch_sync] env=%s failed: %s", env_label, exc)
                    error_report = BatchSyncReport(total=len(effective_codes) if effective_codes else 0)
                    error_report.failed = error_report.total
                    for code in (effective_codes or []):
                        error_report.results.append(SyncResult(skill_code=code, success=False, error=str(exc)))
                    all_reports.append(error_report)

            # 合并所有 env 的报告
            merged_report = BatchSyncReport()
            for r in all_reports:
                merged_report.total += r.total
                merged_report.success += r.success
                merged_report.failed += r.failed
                merged_report.skipped += r.skipped
                merged_report.results.extend(r.results)
            merged_report.trace_id = trace_id
            report = merged_report
        except FileNotFoundError as exc:
            logger.warning("Batch sync skipped: %s", exc)
            _tasks[task_id] = {"status": "error", "progress": "", "result": None, "error": str(exc)}
            return
        except Exception as exc:
            logger.exception("Batch sync failed: %s", exc)
            _tasks[task_id] = {"status": "error", "progress": "", "result": None, "error": f"Batch sync failed: {exc}"}
            return
        report.trace_id = trace_id

        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            generate_report(report, report_path)
            logger.info("Batch sync report written to %s", report_path)
        except Exception as exc:
            logger.warning("Failed to write report to %s: %s", report_path, exc)

        _task_reports[trace_id] = report_path
        resp = _build_response(report, report_path)
        resp.batch_size = batch_size
        resp.batch_index = batch_index
        resp.total_batches = total_batches
        resp.has_next = (batch_size is not None and batch_index + 1 < total_batches)
        _tasks[task_id] = {"status": "done", "progress": f"completed ({report.total} skills)", "result": resp, "error": ""}
        logger.info(
            "Background batch sync task %s completed: total=%d, success=%d, failed=%d",
            task_id, report.total, report.success, report.failed,
        )
    except Exception as exc:
        logger.exception("Background batch sync task %s crashed: %s", task_id, exc)
        _tasks[task_id] = {"status": "error", "progress": "", "result": None, "error": str(exc)}


def _start_batch_sync_task(
    skill_codes: Optional[List[str]],
    force: bool,
    skills_dir: Optional[str],
    batch_size: Optional[int],
    batch_index: int,
    git_sync: GitSyncServiceProtocol,
    svc: SkillBatchSyncServiceProtocol,
    envs: Optional[List[str]] = None,
) -> BatchSyncTaskResponse:
    """创建后台任务，立即返回 task_id。"""
    task_id = _get_trace_id()
    _tasks[task_id] = {"status": "running", "progress": "starting...", "result": None, "error": ""}
    asyncio.create_task(
        _run_batch_sync_background(
            task_id, skill_codes, force, skills_dir, batch_size, batch_index, git_sync, svc, envs,
        )
    )
    return BatchSyncTaskResponse(task_id=task_id, status="running", message="Batch sync started in background")


@router.post("/batch-sync", response_model=BatchSyncTaskResponse)
async def batch_sync_post(
    request: BatchSyncRequest,
    git_sync: GitSyncServiceProtocol = Injected(GitSyncServiceProtocol),
    svc: SkillBatchSyncServiceProtocol = Injected(SkillBatchSyncServiceProtocol),
):
    """JSON body 调用批量同步（异步，立即返回 task_id）。"""
    logger.info("[batch_sync] POST received, envs=%s, skill_codes=%s", request.envs, request.skill_codes)
    return _start_batch_sync_task(
        request.skill_codes, request.force, request.skills_dir,
        request.batch_size, request.batch_index, git_sync, svc, request.envs,
    )


@router.get("/batch-sync", response_model=BatchSyncTaskResponse)
async def batch_sync_get(
    skill_codes: Optional[str] = Query(None, description="逗号分隔的技能列表，空=全量"),
    force: bool = Query(False, description="跳过幂等检查，强制重新发布"),
    skills_dir: Optional[str] = Query(None, description="技能仓库根目录，默认使用 GitSyncConfig 配置路径"),
    batch_size: Optional[int] = Query(None, description="每批处理数量，空=不分批", ge=1),
    batch_index: int = Query(0, description="当前批次索引（从 0 开始）", ge=0),
    envs: Optional[str] = Query(None, description="逗号分隔的 env 列表，空=当前环境；如 pre,prod"),
    git_sync: GitSyncServiceProtocol = Injected(GitSyncServiceProtocol),
    svc: SkillBatchSyncServiceProtocol = Injected(SkillBatchSyncServiceProtocol),
):
    """浏览器直接访问触发批量同步（异步，立即返回 task_id）。"""
    codes = [c.strip() for c in skill_codes.split(",") if c.strip()] if skill_codes else None
    env_list = [e.strip() for e in envs.split(",") if e.strip()] if envs else None
    logger.info("[batch_sync] GET received, envs=%s, skill_codes=%s", env_list, codes)
    return _start_batch_sync_task(codes, force, skills_dir, batch_size, batch_index, git_sync, svc, env_list)


@router.get("/batch-sync/status/{task_id}", response_model=BatchSyncTaskStatusResponse)
async def get_batch_sync_status(task_id: str):
    """轮询批量同步任务状态。"""
    task = _tasks.get(task_id)
    if not task:
        # 尝试从磁盘查找报告（进程重启后 _tasks 丢失）
        candidate = REPORT_DIR / f"{task_id}.md"
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8")
                # 从报告内容中解析统计信息（如果包含 "success=X" 等标记）
                import re
                success_match = re.search(r"success[=:]\s*(\d+)", content, re.IGNORECASE)
                failed_match = re.search(r"failed[=:]\s*(\d+)", content, re.IGNORECASE)
                total_match = re.search(r"total[=:]\s*(\d+)", content, re.IGNORECASE)

                result_data = {
                    "success": int(success_match.group(1)) if success_match else 0,
                    "trace_id": task_id,
                    "total": int(total_match.group(1)) if total_match else 0,
                    "skipped": 0,
                    "success_count": int(success_match.group(1)) if success_match else 0,
                    "failed": int(failed_match.group(1)) if failed_match else 0,
                    "results": [],
                    "report_path": str(candidate),
                }
            except Exception:
                # 解析失败时使用默认值
                result_data = {
                    "success": True,
                    "trace_id": task_id,
                    "total": 0,
                    "skipped": 0,
                    "success_count": 0,
                    "failed": 0,
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


@router.get("/batch-sync/report/{task_id}")
async def get_batch_sync_report(task_id: str):
    """查看指定 task_id 的同步报告。"""
    report_path = _task_reports.get(task_id)
    if not report_path:
        candidate = REPORT_DIR / f"{task_id}.md"
        if candidate.is_file():
            report_path = str(candidate)
        else:
            raise HTTPException(status_code=404, detail=f"Report not found: {task_id}")

    try:
        content = Path(report_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Report file missing: {report_path}")

    return {"task_id": task_id, "report_path": report_path, "content": content}


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

"""

Migrated from: services/openclawserver/server/routers/skill_scan.py
Skill Scan router for triggering skill scans via HTTP API.
"""
import asyncio
from fastapi import APIRouter, HTTPException

from agentclaw.community.adapters.http.skill_center.schemas import (
    ScanGitRequest, ScanSkillRequest, StartDailyTaskRequest,
    ScanCenterRequest, ScanResultItem, ScanResponse, ServiceStatusResponse, MessageResponse,
)
from agentclaw.community.di import Injected
from agentclaw.community.api.skill_scan_service import SkillScanServiceProtocol
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(prefix="/api/skill-scan", tags=["skill-scan"])


# ==================== APIs ====================

@router.get("/status", response_model=ServiceStatusResponse)
async def get_service_status() -> ServiceStatusResponse:
    """Get skill scan service status."""
    return ServiceStatusResponse(
        success=True,
        service_started=True,
        scheduler_started=False,
        daily_task_running=False,
        message="Service is available for on-demand scans"
    )


@router.post("/scan/git", response_model=ScanResponse)
async def scan_git(
    request: ScanGitRequest,
    service: SkillScanServiceProtocol = Injected(SkillScanServiceProtocol),
) -> ScanResponse:
    """Trigger a manual Git repository scan."""
    logger.info(f"[skill_scan.scan_git] Request: git_url={request.git_url}")

    try:
        # 使用 asyncio.to_thread 将同步阻塞操作放到线程池执行，避免阻塞事件循环
        task_result = await asyncio.to_thread(
            service.exec_task,
            git_url=request.git_url,
            private_token=request.private_token,
        )

        if not task_result.get("success"):
            raise HTTPException(status_code=500, detail=task_result.get("error", "Task failed"))

        results = task_result.get("results", [])
        success_count = task_result.get("success_count", 0)
        total = task_result.get("total", len(results))

        logger.info(f"[skill_scan.scan_git] Completed: {success_count}/{total} successful")

        return ScanResponse(
            success=True,
            total=total,
            success_count=success_count,
            results=[ScanResultItem(**r) for r in results],
            message=f"Scan completed: {success_count}/{total} successful"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[skill_scan.scan_git] Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan/skill", response_model=ScanResponse)
async def scan_skill(
    request: ScanSkillRequest,
    service: SkillScanServiceProtocol = Injected(SkillScanServiceProtocol),
) -> ScanResponse:
    """Trigger a manual single skill scan."""
    logger.info(f"[skill_scan.scan_skill] Request: skill_path={request.skill_path}")

    try:
        # 使用 asyncio.to_thread 将同步阻塞操作放到线程池执行
        result = await asyncio.to_thread(
            service.scan_skill,
            skill_path=request.skill_path,
        )

        return ScanResponse(
            success=True,
            total=1,
            success_count=1,
            results=[ScanResultItem(
                skill_path=request.skill_path,
                success=True,
                result=result,
            )],
            message="Skill scanned successfully"
        )
    except Exception as e:
        logger.error(f"[skill_scan.scan_skill] Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/daily-task/start", response_model=MessageResponse)
async def start_daily_task(request: StartDailyTaskRequest) -> MessageResponse:
    """Start the daily scheduled Git scan task (runs at 1:00 AM)."""
    raise HTTPException(status_code=501, detail="Daily task is not supported in stateless mode")


@router.post("/daily-task/stop", response_model=MessageResponse)
async def stop_daily_task() -> MessageResponse:
    """Stop the daily scheduled task."""
    raise HTTPException(status_code=501, detail="Daily task is not supported in stateless mode")


@router.post("/scan/center", response_model=ScanResponse, deprecated=True)
async def scan_center(
    request: ScanCenterRequest,
) -> ScanResponse:
    """Retired: exact metadata scanning is owned by the G2 Materializer."""
    if not request.skill_uuids:
        raise HTTPException(status_code=400, detail="skill_uuids must not be empty")

    raise HTTPException(
        status_code=410,
        detail="Use POST /openapi/v1/bots/market/skill-center/sync",
    )

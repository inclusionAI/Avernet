"""
Cron Router - 定时任务管理

提供定时任务的 REST 接口封装，所有操作调用后端引擎。
"""
import logging

from fastapi import APIRouter, HTTPException

from engine.community.api.cron.schemas import (
    CreateTaskRequest,
    UpdateTaskRequest,
    RunSingleAutoInitiateRequest,
)
from engine.community.plugin_api.cron.models import (
    CreateJobRequest,
    CronNotifyConfig,
    CronNotifyPatch,
    UpdateJobRequest,
)
from engine.community.core.cron.protocol import CronService
from engine.community.core.engine.capability import Capability
from engine.community.api.caps import check_capability

log = logging.getLogger("new_ocb.cron-router")

router = APIRouter(prefix="/api/cron", tags=["cron"])

# 可通过 set_cron_api() 注入测试实例
_cron_api_override: CronService | None = None


def _get_cron_api(engine: str | None = None) -> CronService:
    """Return the CronService for the given engine."""
    if _cron_api_override is not None:
        return _cron_api_override
    from engine.community.manager import EngineManager
    manager = EngineManager.get_instance()

    if engine is not None and engine != manager.engine:
        raise ValueError(
            f"Unsupported engine type: {engine}. Only '{manager.engine}' is supported."
        )
    return manager.cron


def get_cron_api() -> CronService:
    """获取 CronService 实例（向后兼容 + 测试注入）"""
    return _get_cron_api()


def set_cron_api(api: CronService) -> None:
    """设置 CronService 实例（用于测试）"""
    global _cron_api_override
    _cron_api_override = api


# ========== API 路由 ==========

@router.get("")
async def list_tasks():
    """
    获取所有定时任务列表
    GET /api/scheduled-tasks
    """
    log.info("[list_tasks] GET /api/cron")
    warning = check_capability(Capability.CRON_LIST)
    try:
        cron_api = get_cron_api()
        jobs = await cron_api.list_jobs()
        return {
            "success": True,
            "data": [j.model_dump() for j in jobs],
            "total": len(jobs),
            "warning": warning,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_cron_status():
    """
    获取 cron 服务状态
    GET /api/scheduled-tasks/status

    Intentionally unguarded: this reports the cron *service*'s status (is the
    poller running, last error, etc.), not a declared engine capability.
    """
    log.info("[get_cron_status] GET /api/cron/status")
    try:
        cron_api = get_cron_api()
        status = await cron_api.get_status()
        return {"success": True, "data": status.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/running")
async def get_running_tasks():
    """
    获取正在执行的任务列表
    GET /api/cron/running
    """
    log.info("[get_running_tasks] GET /api/cron/running")
    warning = check_capability(Capability.CRON_LIST)
    try:
        cron_api = get_cron_api()
        running = await cron_api.get_running_jobs()
        return {
            "success": True,
            "data": {"running": running},
            "total": len(running),
            "warning": warning,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}")
async def get_task(task_id: str):
    """
    获取单个定时任务详情
    GET /api/scheduled-tasks/{task_id}
    """
    log.info(f"[get_task] GET /api/cron/{task_id}")
    warning = check_capability(Capability.CRON_LIST)
    try:
        cron_api = get_cron_api()
        job = await cron_api.get_job(task_id)

        if not job:
            raise HTTPException(status_code=404, detail="Task not found")

        return {"success": True, "data": job.model_dump(), "warning": warning}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_task(request: CreateTaskRequest):
    """
    创建定时任务
    POST /api/scheduled-tasks
    """
    log.info(f"[create_task] POST /api/cron name={request.name}")
    warning = check_capability(Capability.CRON_CREATE)
    try:
        cron_api = get_cron_api()

        # 构建 schedule 对象
        schedule = {
            "kind": "cron",
            "expr": request.schedule,
            "tz": request.timezone
        }

        # 构建 payload — kind 默认 agentTurn（relay 唯一可执行类型），允许调用方显式覆盖
        payload = {
            "kind": request.kind or "agentTurn",
            "message": request.command,
            "timeout_secs": request.timeout_secs if request.timeout_secs is not None else 86400
        }
        if request.model:
            payload["model"] = request.model  # 指定AI模型
        if request.runtime:
            payload["runtime"] = request.runtime  # aicoding 创建会话时使用的 runtime
        if request.append_message:
            payload["append_message"] = request.append_message  # autoInitiate 补充说明

        # 构建 notify 配置
        notify = None
        if request.notify is not None:
            notify = CronNotifyConfig(
                enabled=request.notify.enabled,
                user_ids=request.notify.user_ids
            )

        create_request = CreateJobRequest(
            name=request.name,
            schedule=schedule,
            payload=payload,
            session_target="isolated",
            enabled=request.enabled,
            notify=notify
        )

        job = await cron_api.add_job(create_request)

        return {"success": True, "data": job.model_dump(), "warning": warning}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{task_id}")
async def update_task(task_id: str, request: UpdateTaskRequest):
    """
    更新定时任务
    PUT /api/scheduled-tasks/{task_id}
    """
    log.info(f"[update_task] PUT /api/cron/{task_id}")
    warning = check_capability(Capability.CRON_UPDATE)
    try:
        cron_api = get_cron_api()

        # 构建 update 请求
        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.schedule is not None or request.timezone is not None:
            update_data["schedule"] = {"kind": "cron"}
            if request.schedule is not None:
                update_data["schedule"]["expr"] = request.schedule
            if request.timezone is not None:
                update_data["schedule"]["tz"] = request.timezone
        if request.command is not None or request.timeout_secs is not None or request.model is not None or request.runtime is not None:
            # 先获取现有任务，保留其他 payload 字段
            existing_job = await cron_api.get_job(task_id)
            if existing_job:
                update_data["payload"] = existing_job.payload.copy()
                # 更新 message 字段（kind 由引擎层根据 message 前缀自动检测）
                if request.command is not None:
                    update_data["payload"]["message"] = request.command
                if request.timeout_secs is not None:
                    update_data["payload"]["timeout_secs"] = request.timeout_secs
                if request.model is not None:
                    update_data["payload"]["model"] = request.model
                if request.runtime is not None:
                    update_data["payload"]["runtime"] = request.runtime
            else:
                raise HTTPException(status_code=404, detail="Task not found")

        # 处理 notify 更新（支持部分更新）
        if request.notify is not None:
            notify_patch = CronNotifyPatch()
            if request.notify.enabled is not None:
                notify_patch.enabled = request.notify.enabled
            if request.notify.user_ids is not None:
                notify_patch.user_ids = request.notify.user_ids
            update_data["notify"] = notify_patch

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        update_req = UpdateJobRequest(**update_data)
        job = await cron_api.update_job(task_id, update_req)

        return {"success": True, "data": job.model_dump(), "warning": warning}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """
    删除定时任务
    DELETE /api/scheduled-tasks/{task_id}
    """
    log.info(f"[delete_task] DELETE /api/cron/{task_id}")
    warning = check_capability(Capability.CRON_DELETE)
    try:
        cron_api = get_cron_api()
        success = await cron_api.remove_job(task_id)

        if not success:
            raise HTTPException(status_code=404, detail="Task not found or delete failed")

        return {"success": True, "message": "Task deleted", "warning": warning}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/run")
async def run_task(
    task_id: str,
    force: bool = False,
):
    """
    立即执行定时任务
    POST /api/scheduled-tasks/{task_id}/run?force=false

    """
    log.info(f"[run_task] POST /api/cron/{task_id}/run force={force}")
    warning = check_capability(Capability.CRON_RUN)
    try:
        cron_api = get_cron_api()
        result = await cron_api.run_job(task_id, force)
        return {"success": True, "data": result, "warning": warning}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auto-initiate/run-single")
async def run_single_auto_initiate(request: RunSingleAutoInitiateRequest):
    """为单个需求/工作项直接发起会话，跳过批量查询/过滤。

    POST /api/cron/auto-initiate/run-single
    """
    log.info(
        "[run_single_auto_initiate] work_item_url=%s, user_id=%s, agent_id=%s",
        request.work_item_url, request.user_id, request.agent_id,
    )
    try:
        from engine.community.manager import EngineManager
        manager = EngineManager.get_instance()
        engine = manager._require_engine()
        plugin = getattr(engine, "auto_initiate", None)
        if plugin is None:
            raise HTTPException(status_code=501, detail="AutoInitiate not supported by current engine")

        result = await plugin.execute_single(
            work_item_url=request.work_item_url,
            user_id=request.user_id,
            agent_id=request.agent_id,
            workflow=request.workflow,
            append_message=request.append_message,
            model=request.model,
        )

        from dataclasses import asdict
        return {"success": True, "data": asdict(result)}
    except HTTPException:
        raise
    except Exception as e:
        log.error("[run_single_auto_initiate] %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}/runs")
async def get_task_runs(task_id: str, limit: int = 20):
    """
    获取任务的执行历史（包含输入命令）
    GET /api/scheduled-tasks/{task_id}/runs?limit=20
    """
    log.info(f"[get_task_runs] GET /api/cron/{task_id}/runs limit={limit}")
    warning = check_capability(Capability.CRON_HISTORY)
    try:
        cron_api = get_cron_api()

        # 获取任务详情（拿到输入）
        jobs = await cron_api.list_jobs()
        job = next(
            (
                j
                for j in jobs
                if j.id == task_id
            ),
            None,
        )
        input_command = job.payload.get("message", "") if job else ""

        runs = await cron_api.get_runs(task_id, limit)

        return {
            "success": True,
            "data": {
                "input": input_command,
                "runs": [r.model_dump() for r in runs]
            },
            "warning": warning,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

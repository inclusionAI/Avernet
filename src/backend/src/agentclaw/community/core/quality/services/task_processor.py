"""Task Processor Service.

Business logic for advancing quality task status.

Status flow: init → env_preparing → env_ready → task_created → task_executed → success/failed
"""
from enum import Enum
from typing import Annotated, Any

from injector import inject

from agentclaw.community.core.quality.repositories import (
    QualityTaskRepository,
    QualityTaskRecord,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_MASA_AGENT_EVAL
from agentclaw.community.plugin_api.tracer import TracerPlugin

logger = get_logger()

# Eval progress status labels
_STATUS_LABELS = {
    "init": "待执行",
    "running": "评测中",
    "judging": "评判中",
    "reporting": "报告生成中",
    "completed": "已完成",
    "failed": "已失败",
}

_TERMINAL_STATUSES = {"completed", "failed"}


class TaskStatus(str, Enum):
    """Quality task status enumeration."""

    INIT = "init"
    ENV_PREPARING = "env_preparing"
    ENV_READY = "env_ready"
    TASK_CREATED = "task_created"
    TASK_EXECUTED = "task_executed"
    SUCCESS = "success"
    FAILED = "failed"


class InvalidStatusTransitionError(ValueError):
    """Raised when attempting an invalid status transition."""

    def __init__(self, current_status: str, target_status: str) -> None:
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Invalid status transition: {current_status} → {target_status}"
        )


class TaskProcessor:
    """Service for advancing quality task status."""

    @inject
    def __init__(
            self,
            repository: QualityTaskRepository,
            masa_eval_http: Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL],
            publish_flow_service: PublishFlowService,
            tracer: TracerPlugin,
    ) -> None:
        self._repository = repository
        self._masa_eval_http = masa_eval_http
        self._publish_flow_service = publish_flow_service
        self._tracer = tracer

    async def process(self, id: int) -> QualityTaskRecord:
        """Advance task to the next status. Terminal status returns unchanged."""
        task = self._repository.get_by_id(id)
        logger.info("[process] task fetched: id=%s, status=%s, bot_id=%s", id, task.status if task else None, task.bot_id if task else None)
        if not task:
            raise ValueError(f"Task not found: {id}")

        try:
            current_status = task.status

            # Route to appropriate transition method based on current status
            if current_status == TaskStatus.INIT.value:
                return await self.to_env_preparing(id)
            elif current_status == TaskStatus.ENV_PREPARING.value:
                return self.to_env_ready(id)
            elif current_status == TaskStatus.ENV_READY.value:
                return self.to_task_created(id)
            elif current_status == TaskStatus.TASK_CREATED.value:
                return self.to_task_executed(id)
            elif current_status == TaskStatus.TASK_EXECUTED.value:
                return self.to_env_released(id, TaskStatus.TASK_EXECUTED.value, TaskStatus.SUCCESS.value)
            else:
                # Terminal status (success/failed) - return current task unchanged
                logger.info("[process] id=%s is at terminal status '%s', returning unchanged", id, current_status)
                return task
        except Exception as e:
            # Record exception info to ext, then re-raise
            logger.exception("[process] id=%s failed with error: %s", id, e)
            try:
                ext = dict(task.ext) if task.ext else {}
                trace_id = self._tracer.current_trace_id()
                if trace_id:
                    ext["error_msg"] = f"{e} (trace_id: {trace_id})"
                else:
                    ext["error_msg"] = str(e)
                self._repository.update_ext(id, ext)
            except Exception as update_error:
                logger.warning("[process] id=%s failed to update ext with error info: %s", id, update_error)
            raise

    async def to_env_preparing(self, id: int) -> QualityTaskRecord:
        """Advance task from 'init' to 'env_preparing'.

        Calls PublishFlowService.general_publish to prepare the evaluation environment.
        """
        logger.info("[to_env_preparing] id=%s", id)

        task = self._repository.get_by_id(id)
        if not task:
            raise ValueError(f"Task not found: {id}")

        # Get publish_id from ext field
        ext = dict(task.ext) if task.ext else {}
        publish_id = ext.get("publish_id")
        if not publish_id:
            raise ValueError(f"Task {id} missing publish_id in ext field")

        # Use uuid as biz_id
        biz_id = task.uuid
        if not biz_id:
            raise ValueError(f"Task {id} missing uuid field")

        operator = task.operator_id or ""

        logger.info(
            "[to_env_preparing] Calling general_publish: publish_id=%s, biz_id=%s, operator=%s",
            publish_id, biz_id, operator
        )

        result = await self._publish_flow_service.general_publish(
            publish_id=int(publish_id),
            operator=operator,
            biz_id=biz_id,
        )

        logger.info("[to_env_preparing] general_publish completed: result=%s", result)

        # Save bot_uuid and baas_publish_id to ext
        if result:
            if "bot_uuid" in result:
                ext["bot_uuid"] = result["bot_uuid"]
            if "baas_publish_id" in result:
                ext["baas_publish_id"] = result["baas_publish_id"]

        self._transition_to(id, TaskStatus.INIT.value, TaskStatus.ENV_PREPARING.value, ext)
        logger.info("[to_env_preparing] id=%s, init -> env_preparing completed, proceeding to env_ready", id)
        return self.to_env_ready(id)

    def to_env_ready(self, id: int) -> QualityTaskRecord:
        """Advance task from 'env_preparing' to 'env_ready'.

        Queries BaaS publish progress to check if environment is ready.
        Returns unchanged task if still in progress.
        """
        logger.info("[to_env_ready] id=%s", id)

        task = self._repository.get_by_id(id)
        if not task:
            raise ValueError(f"Task not found: {id}")

        ext = dict(task.ext) if task.ext else {}
        baas_publish_id = ext.get("baas_publish_id")
        if not baas_publish_id:
            raise ValueError(f"Task {id} missing baas_publish_id in ext field")

        logger.info("[to_env_ready] Querying BaaS publish progress: baas_publish_id=%s", baas_publish_id)
        progress = self._publish_flow_service.get_baas_publish_progress(
            baas_publish_id=baas_publish_id,
            include_devices=False,
        )
        status = progress.get("status", "")
        logger.info("[to_env_ready] BaaS publish status: %s", status)
        ext["baas_publish_progress"] = progress

        if status == "SUCCESS":
            self._transition_to(id, TaskStatus.ENV_PREPARING.value, TaskStatus.ENV_READY.value, ext)
            logger.info("[to_env_ready] id=%s, env_preparing -> env_ready completed", id)
            return self.to_task_created(id)
        elif status == "FAILED":
            ext["error_msg"] = progress.get("error", "BaaS publish failed")
            self._repository.update_ext(id, ext)
            return self.to_env_released(id, TaskStatus.ENV_PREPARING.value, TaskStatus.FAILED.value)
        else:
            # Still in progress (e.g., RUNNING), save progress and return unchanged
            self._repository.update_ext(id, ext)
            logger.info("[to_env_ready] id=%s, publish still in progress, returning unchanged", id)
            return task

    def to_task_created(self, id: int) -> QualityTaskRecord:
        """Advance task from 'env_ready' to 'task_created'. Calls /eval/start API and stores set_task_uuid."""
        logger.info("[to_task_created] id=%s", id)

        task = self._repository.get_by_id(id)
        if not task:
            raise ValueError(f"Task not found: {id}")

        ext = dict(task.ext) if task.ext else {}

        if not ext.get("set_task_uuid"):
            set_uuid = ext.get("set_uuid")
            if not set_uuid:
                raise ValueError(f"Task {id} missing set_uuid in ext field")

            version = ext.get("version")
            if not version:
                raise ValueError(f"Task {id} missing version in ext field")
            version = str(version)  # ensure version is string for API

            bot_id = task.bot_id
            if not bot_id:
                raise ValueError(f"Task {id} missing bot_id")

            owner_id = task.owner_id
            if not owner_id:
                raise ValueError(f"Task {id} missing owner_id")

            # 评测API要求的格式: bot_id:owner_id
            bot_id_with_owner = f"{bot_id}:{owner_id}"

            env = f"{task.task_type}-{task.uuid}"

            logger.info("[to_task_created] Calling /eval/start: env=%s, bot_id=%s, set_uuid=%s, version=%s", env, bot_id_with_owner, set_uuid, version)

            set_task_uuid = self._call_eval_start_api(env, bot_id_with_owner, set_uuid, version)
            ext["set_task_uuid"] = set_task_uuid
            logger.info("[to_task_created] Got set_task_uuid=%s", set_task_uuid)

        ext["source_status"] = TaskStatus.ENV_READY.value
        updated = self._repository.update_status(id, TaskStatus.TASK_CREATED.value, ext)
        if not updated:
            raise ValueError(f"Failed to update task: {id}")

        logger.info("[to_task_created] id=%s, env_ready -> task_created completed", id)
        return self.to_task_executed(id)

    def to_task_executed(self, id: int) -> QualityTaskRecord:
        """Advance task from 'task_created' to 'task_executed'. Queries /eval/progress API."""
        logger.info("[to_task_executed] id=%s", id)

        task = self._repository.get_by_id(id)
        if not task:
            raise ValueError(f"Task not found: {id}")

        ext = dict(task.ext) if task.ext else {}
        set_task_uuid = ext.get("set_task_uuid")
        if not set_task_uuid:
            raise ValueError(f"Task {id} missing set_task_uuid in ext field")

        logger.info("[to_task_executed] Calling /eval/progress: set_task_uuid=%s", set_task_uuid)
        progress = self._call_eval_progress_api(set_task_uuid)
        ext["eval_progress"] = progress
        status = progress.get("status")
        logger.info("[to_task_executed] Got progress status=%s", status)

        if status not in _TERMINAL_STATUSES:
            return task

        self._transition_to(id, TaskStatus.TASK_CREATED.value, TaskStatus.TASK_EXECUTED.value, ext)

        if status == "completed":
            return self.to_env_released(id, TaskStatus.TASK_EXECUTED.value, TaskStatus.SUCCESS.value)
        else:  # failed
            ext["error_msg"] = f"{progress.get('error', '评测失败')} (set_task_uuid={set_task_uuid})"
            self._repository.update_ext(id, ext)
            return self.to_env_released(id, TaskStatus.TASK_EXECUTED.value, TaskStatus.FAILED.value)

    def to_env_released(self, id: int, source_status: str, target_status: str) -> QualityTaskRecord:
        """Release environment and advance task to target_status."""
        logger.info("[to_env_released] id=%s, releasing environment", id)

        task = self._repository.get_by_id(id)
        if not task:
            raise ValueError(f"Task not found: {id}")

        ext = dict(task.ext) if task.ext else {}
        bot_uuid = ext.get("bot_uuid")
        if bot_uuid:
            operator = task.operator_id or "system"
            logger.info("[to_env_released] Calling general_teardown: bot_uuid=%s, operator=%s", bot_uuid, operator)
            try:
                teardown_result = self._publish_flow_service.general_teardown(bot_uuid, operator=operator)
                logger.info("[to_env_released] general_teardown completed: result=%s", teardown_result)
                if teardown_result and "destroy_publish_id" in teardown_result:
                    ext["destroy_publish_id"] = teardown_result["destroy_publish_id"]
            except Exception as e:
                logger.warning("[to_env_released] general_teardown failed: %s", e)
        else:
            logger.warning("[to_env_released] No bot_uuid in ext, skipping teardown")

        return self._transition_to(id, source_status, target_status, ext)

    def _call_eval_start_api(self, env: str, bot_id: str, set_uuid: str, version: str) -> str:
        """Call /eval/start API and return set_task_uuid."""
        response = self._masa_eval_http.post(
            "/eval/start",
            json={
                "env": env,
                "bot_id": bot_id,
                "set_uuid": set_uuid,
                "version": version,
            },
        )
        logger.info("[eval_start] response status=%s body=%s", response.status_code, response.text)
        response.raise_for_status()

        data: dict[str, Any] = response.json()
        if not data.get("success"):
            raise ValueError(f"Eval start failed: {data}")

        inner = data.get("data", {})
        if not inner:
            raise ValueError(f"Eval start response missing 'data' field: {data}")

        set_task_uuid = inner.get("set_task_uuid")
        if not set_task_uuid:
            raise ValueError(f"Eval start response missing set_task_uuid: {data}")

        return set_task_uuid

    def _call_eval_progress_api(self, set_task_uuid: str) -> dict[str, Any]:
        """Call /eval/progress API and return progress data."""
        response = self._masa_eval_http.get(f"/eval/progress?set_task_uuid={set_task_uuid}")
        logger.info("[eval_progress] response status=%s body=%s", response.status_code, response.text)
        response.raise_for_status()

        data: dict[str, Any] = response.json()
        if not data.get("success"):
            raise ValueError(f"Eval progress failed: {data}")

        inner = data.get("data", {})
        if not inner:
            raise ValueError(f"Eval progress response missing 'data' field: {data}")

        return inner

    def _transition_to(
            self,
            id: int,
            source_status: str,
            target: str,
            ext: dict[str, Any] | None = None,
    ) -> QualityTaskRecord:
        """Transition task from source_status to target.

        Args:
            id: Task ID
            source_status: Expected current status
            target: Target status to transition to
            ext: Optional ext dict to merge (source_status will be added automatically)
        """
        task = self._repository.get_by_id(id)
        if not task:
            raise ValueError(f"Task not found: {id}")

        if task.status != source_status:
            raise ValueError(
                f"Expected status '{source_status}', but task {id} has status '{task.status}'"
            )

        ext_update = ext if ext is not None else {}
        ext_update["source_status"] = source_status
        updated = self._repository.update_status(id, target, ext_update)
        if not updated:
            raise ValueError(f"Failed to update task: {id}")

        logger.info("[_transition_to] id=%s, %s -> %s completed", id, source_status, target)
        return updated

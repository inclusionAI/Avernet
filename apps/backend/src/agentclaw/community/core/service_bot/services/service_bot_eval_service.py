"""Authorization and release-context resolution for service-Bot evaluation."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Annotated, Any, Sequence

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    resolve_operable_permission_level,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
    ServicePublicationNotFoundError,
)
from agentclaw.community.core.service_bot.services.standard_eval_snapshots import (
    public_standard_environment,
    standard_task_snapshot,
)
from agentclaw.community.core.quality.quality_service_protocol import (
    QualityTaskServiceProtocol,
)
from agentclaw.community.core.quality.task_processor_service_protocol import (
    TaskProcessorProtocol,
)
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_MASA_AGENT_EVAL
from agentclaw.community.plugin_api.service_bot_eval import (
    ServiceBotEvalContext,
    ServiceBotEvalFile,
    ServiceBotEvalPlugin,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_STANDARD_ENV_POLL_INTERVAL_SECONDS = 5
_STANDARD_ENV_MAX_POLL_SECONDS = 30 * 60
# 标准评测正常执行可达小时级：环境 TTL 默认 4 小时，覆盖执行时长并留有余量。
_STANDARD_EVAL_ENV_TTL_MINUTES = 240
# MASA /eval/progress 自适应退避：启动后前 5 分钟 15s/次，之后 60s/次。
_EVAL_PROGRESS_FAST_INTERVAL_SECONDS = 15
_EVAL_PROGRESS_FAST_WINDOW_SECONDS = 5 * 60
_EVAL_PROGRESS_SLOW_INTERVAL_SECONDS = 60


def _iso_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


class ServiceBotEvalService:
    """Protect the evaluation plugin with Bot, collaborator, and release binding.

    The caller-supplied owner and version inputs are never authoritative.  The
    service resolves the tenant-scoped Bot, checks the actor, then proves that
    the addressed version belongs to that Bot's current environment.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        publish_repo: BotPublishRepositoryProtocol,
        collaborator_service: CollaboratorService,
        plugin: ServiceBotEvalPlugin,
        quality_tasks: QualityTaskServiceProtocol | None = None,
        task_processor: TaskProcessorProtocol | None = None,
        masa_eval_http: Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL] | None = None,
    ) -> None:
        self._bot_repo = bot_repo
        self._publish_repo = publish_repo
        self._collaborator_service = collaborator_service
        self._plugin = plugin
        self._quality_tasks = quality_tasks
        self._task_processor = task_processor
        self._masa_eval_http = masa_eval_http
        # Strong references keep an asyncio driver alive until it finishes.
        self._standard_task_drivers: dict[int, asyncio.Task[None]] = {}

    def _resolve(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        required_level: PermissionLevel,
        require_draft: bool = False,
        latest_when_missing: bool = False,
    ) -> tuple[dict[str, Any], BotPublishRecord, ServiceBotEvalContext]:
        try:
            bot = dict(
                BotAddressResolver(self._bot_repo).resolve(
                    bot_id=bot_id,
                    legacy_owner_id=owner_id,
                ).bot
            )
        except BotAddressNotFoundError as exc:
            raise ServicePublicationNotFoundError("bot not found") from exc

        level = resolve_operable_permission_level(
            self._collaborator_service,
            bot=bot,
            user_id=actor_id,
            owner_id=str(bot["owner_id"]),
            env=get_current_env(),
        )
        if level < required_level:
            # Keep absent and forbidden indistinguishable on this public boundary.
            raise ServicePublicationNotFoundError("bot not found")
        if bot.get("bot_type") != "service":
            raise ServicePublicationConflictError("bot is not a service bot")

        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        record: BotPublishRecord | None = None
        try:
            requested_version = int(bot_version, 10)
        except (TypeError, ValueError):
            requested_version = -1
        for item in records:
            if int(item.version or 0) == requested_version:
                record = item
                break
        if record is None:
            if not latest_when_missing:
                raise ServicePublicationNotFoundError("publication not found")
            record = None
            for item in records:
                if record is None or (
                    (item.version or 0, item.id or 0)
                    > (record.version or 0, record.id or 0)
                ):
                    record = item
            if record is None:
                raise ServicePublicationConflictError("no actionable publication")
        if require_draft and record.status != PublishStatus.DRAFT.value:
            raise ServicePublicationConflictError("publication is not a draft")

        context = ServiceBotEvalContext(
            bot_id=str(bot["bot_id"]),
            bot_version=str(record.version),
            release_id=str(record.id),
            publish_id=str(record.id),
            operator_id=actor_id,
            latest_bot_modified_at=_iso_time(bot.get("gmt_modified")),
        )
        return bot, record, context

    async def precheck(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
            require_draft=True,
        )
        return await self._plugin.precheck(context, trigger_source="draft_to_validate")

    async def detail(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        return await self._plugin.detail(context)

    async def submit(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        files: Sequence[ServiceBotEvalFile],
        trigger_source: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        return await self._plugin.submit(
            context,
            list(files),
            trigger_source=trigger_source,
        )

    async def get_submission(
        self,
        bot_id: str,
        submission_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            "latest",
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
            latest_when_missing=True,
        )
        return await self._plugin.get_submission(context, submission_id)

    async def history(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        page_no: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        return await self._plugin.history(
            context,
            page_no=max(page_no, 1),
            page_size=min(max(page_size, 1), 100),
        )


    def _standard_backend(self) -> tuple[QualityTaskServiceProtocol, TaskProcessorProtocol, HttpClient]:
        if self._quality_tasks is None:
            raise ServicePublicationConflictError("standard evaluation backend is unavailable")
        if self._task_processor is None:
            raise ServicePublicationConflictError("standard evaluation backend is unavailable")
        if self._masa_eval_http is None:
            raise ServicePublicationConflictError("standard evaluation backend is unavailable")
        return self._quality_tasks, self._task_processor, self._masa_eval_http


    @staticmethod
    def _response_object(response: Any) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise ServicePublicationConflictError("MASA returned an invalid response")

    def _list_sample_sets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        masa_eval_http: HttpClient,
    ) -> dict[str, Any]:
        response = masa_eval_http.get(
            "/eval/listEvalSet",
            params={
                "bot_id": f"{bot_id}:{owner_id}",
            },
        )
        body = self._response_object(response)
        sets = body.get("sets")
        if sets is None and isinstance(body.get("data"), dict):
            sets = body["data"].get("sets")
        if sets is None:
            sets = []
        if not isinstance(sets, list):
            raise ServicePublicationConflictError("MASA returned invalid sample sets")
        return {
            "support": bool(body.get("support", True)),
            "sets": [set_item for set_item in sets if isinstance(set_item, dict)],
        }

    @staticmethod
    def _public_standard_environment(environment: dict[str, Any]) -> dict[str, Any]:
        return public_standard_environment(environment)

    @staticmethod
    def _standard_task_snapshot(record: Any) -> dict[str, Any]:
        return standard_task_snapshot(record)

    @staticmethod
    def _is_processing_standard_task(record: Any) -> bool:
        return str(record.status) in {
            "init",
            "env_preparing",
            "env_ready",
            "task_created",
            "task_executed",
        }

    @staticmethod
    def _is_pending_standard_save(record: Any) -> bool:
        """Whether a terminal task still needs its unified-gate result saved."""
        if str(record.status) not in {"success", "failed"}:
            return False
        ext = record.ext if isinstance(record.ext, dict) else {}
        if ext.get("standard_result_saved") is True:
            return False
        save_status = str(ext.get("standard_result_save_status") or "").upper()
        return save_status in {"", "SAVING"}

    def _find_sample_set(self, set_uuid: str, sets: list[dict[str, Any]]) -> dict[str, Any] | None:
        for set_item in sets:
            if str(set_item.get("uuid") or "") == set_uuid:
                return set_item
        return None

    def _start_standard_task_driver(
        self,
        task_id: int,
        *,
        context: ServiceBotEvalContext,
        trigger_source: str,
    ) -> None:
        if task_id in self._standard_task_drivers or not self._task_processor:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        driver: asyncio.Task[None] = asyncio.create_task(
            self._drive_standard_task(task_id, context=context, trigger_source=trigger_source)
        )
        self._standard_task_drivers[task_id] = driver
        driver.add_done_callback(lambda _task: self._standard_task_drivers.pop(task_id, None))

    def _standard_environment(self, environment: Any) -> dict[str, Any]:
        if not isinstance(environment, dict):
            raise ServicePublicationConflictError("standard evaluation environment response is invalid")
        return {**environment, "default_tag": str(environment.get("default_tag") or "eval")}

    async def _prepare_standard_environment(
        self,
        current: Any,
        context: ServiceBotEvalContext,
    ) -> Any:
        environment = self._standard_environment(
            await self._plugin.create_standard_environment(
                context,
                default_tag="eval",
                ttl_minutes=_STANDARD_EVAL_ENV_TTL_MINUTES,
            )
        )
        ext = dict(current.ext or {})
        ext["standard_environment"] = environment
        build_status = str(environment.get("build_status") or "")

        if environment.get("supported") and build_status == "active":
            ext["standard_environment_provider"] = "service_bot_eval_plugin"
            ext["eval_env"] = environment
            return self._quality_tasks.update_task_status(
                current.id,
                "env_ready",
                ext,
            )

        if environment.get("supported") and build_status == "failed":
            ext["standard_environment_provider"] = "service_bot_eval_plugin"
            ext["error_msg"] = str(
                environment.get("error")
                or environment.get("build_info")
                or "标准评测环境创建失败"
            )
            return self._quality_tasks.update_task_status(
                current.id,
                "failed",
                ext,
            )

        if environment.get("supported"):
            ext["standard_environment_provider"] = "service_bot_eval_plugin"
            return self._quality_tasks.update_task_status(
                current.id,
                "env_preparing",
                ext,
            )

        # community/local profiles do not expose the corp Eval-zone service.
        # The existing TaskProcessor lifecycle continues to own the environment
        # in those profiles, while prod uses the server-managed plugin above.
        ext["standard_environment_provider"] = "task_processor"
        return self._quality_tasks.update_task_status(
            current.id,
            str(current.status),
            ext,
        )

    async def _poll_standard_environment(
        self,
        current: Any,
        context: ServiceBotEvalContext,
    ) -> Any:
        environment = self._standard_environment(
            await self._plugin.get_standard_environment_status(
                context,
                default_tag="eval",
            )
        )
        ext = dict(current.ext or {})
        ext["standard_environment"] = environment
        build_status = str(environment.get("build_status") or "")
        if build_status == "active":
            ext["eval_env"] = environment
            return self._quality_tasks.update_task_status(
                current.id,
                "env_ready",
                ext,
            )
        if build_status == "failed":
            ext["error_msg"] = str(
                environment.get("error")
                or environment.get("build_info")
                or "标准评测环境构建失败"
            )
            return self._quality_tasks.update_task_status(
                current.id,
                "failed",
                ext,
            )
        return self._quality_tasks.update_task_status(
            current.id,
            str(current.status),
            ext,
        )

    def _standard_result_from_record(
        self,
        record: Any,
    ) -> dict[str, Any]:
        ext = dict(record.ext) if isinstance(record.ext, dict) else {}
        progress = ext.get("eval_progress") if isinstance(ext.get("eval_progress"), dict) else {}
        report = progress.get("report") if isinstance(progress.get("report"), dict) else {}
        return {
            "task_id": record.id,
            "task_uuid": record.uuid or "",
            "task_status": "success" if str(record.status) == "success" else "failed",
            "gate_status": "PASSED" if str(record.status) == "success" else "BLOCKED",
            "sample_set_uuid": ext.get("set_uuid"),
            "sample_set_name": ext.get("sample_set_name"),
            "sample_count": ext.get("sample_set_count"),
            "sample_set_url": ext.get("sample_set_url") or "",
            "report_url": report.get("reportUrl") or "",
            "error_code": "STANDARD_EVAL_FAILED" if str(record.status) == "failed" else "",
            "error_message": ext.get("error") or ext.get("error_msg") or "",
            "progress": progress,
        }

    async def _drive_standard_task(
        self,
        task_id: int,
        *,
        context: ServiceBotEvalContext,
        trigger_source: str,
    ) -> None:
        if not self._quality_tasks or not self._task_processor:
            return
        try:
            current = self._quality_tasks.get_task_by_id(task_id)
            if not current:
                return

            ext = dict(current.ext or {})
            if not ext.get("standard_environment_provider"):
                current = await self._prepare_standard_environment(current, context)

            poll_elapsed = 0
            progress_started_at: float | None = None
            while self._is_processing_standard_task(current):
                status = str(current.status)
                provider = str((current.ext or {}).get("standard_environment_provider") or "")

                # 记录评测进度轮询起点：task_created 即 MASA /eval/progress 等待阶段。
                if status == "task_created":
                    if progress_started_at is None:
                        progress_started_at = time.monotonic()
                else:
                    progress_started_at = None

                if status == "env_preparing" and provider == "service_bot_eval_plugin":
                    if poll_elapsed >= _STANDARD_ENV_MAX_POLL_SECONDS:
                        ext = dict(current.ext or {})
                        ext["error_msg"] = "等待标准评测环境构建超时"
                        current = self._quality_tasks.update_task_status(
                            current.id,
                            "failed",
                            ext,
                        )
                        break
                    await asyncio.sleep(_STANDARD_ENV_POLL_INTERVAL_SECONDS)
                    poll_elapsed += _STANDARD_ENV_POLL_INTERVAL_SECONDS
                    current = await self._poll_standard_environment(current, context)
                    continue

                processed = await self._task_processor.process(task_id)
                if not processed:
                    return
                if str(processed.status) == status:
                    interval = _STANDARD_ENV_POLL_INTERVAL_SECONDS
                    if progress_started_at is not None:
                        progress_elapsed = time.monotonic() - progress_started_at
                        interval = (
                            _EVAL_PROGRESS_FAST_INTERVAL_SECONDS
                            if progress_elapsed < _EVAL_PROGRESS_FAST_WINDOW_SECONDS
                            else _EVAL_PROGRESS_SLOW_INTERVAL_SECONDS
                        )
                    await asyncio.sleep(interval)
                current = processed

            if str(current and current.status) not in {"success", "failed"}:
                return

            payload = self._standard_result_from_record(current)
            saved_result = await self._plugin.save_standard_result(
                context,
                payload,
                trigger_source=trigger_source,
            )
            ext = dict(current.ext or {})
            saved = bool(saved_result.get("saved"))
            ext["standard_result_saved"] = False
            ext["standard_result_save_status"] = "SAVING"
            self._quality_tasks.update_task_status(current.id, current.status, ext)

            if (
                ext.get("standard_environment_provider") == "service_bot_eval_plugin"
                and isinstance(ext.get("eval_env"), dict)
            ):
                try:
                    ext["standard_environment_result"] = await self._plugin.destroy_standard_environment(
                        context,
                        default_tag="eval",
                        reason="standard_eval_finished",
                    )
                except Exception as exc:
                    logger.warning(
                        "[service_bot_eval] standard environment cleanup failed id=%s error=%s",
                        task_id,
                        exc,
                    )
                    ext["standard_environment_result"] = {
                        "destroyed": False,
                        "destroy_status": "failed",
                        "error": str(exc),
                    }

            save_status = "SAVED" if saved else str(
                saved_result.get("gate_status") or saved_result.get("message") or "SKIPPED"
            ).upper()
            ext["standard_result_saved"] = saved
            ext["standard_result_save_status"] = save_status
            self._quality_tasks.update_task_status(current.id, current.status, ext)
        except Exception:
            # A failed driver does not abandon the task record; the current-status
            # endpoint can restart the driver, and the explicit save API remains a
            # manual fallback for completed tasks.
            logger.exception("[service_bot_eval] standard task driver failed id=%s", task_id)



    @staticmethod
    def _standard_task_records(
        records: list[Any],
        *,
        bot_version: str,
    ) -> list[Any]:
        result: list[Any] = []
        for record in records:
            ext = record.ext if isinstance(record.ext, dict) else {}
            if ext.get("source") != "standard_eval":
                continue
            if str(ext.get("version")) != str(bot_version):
                continue
            result.append(record)
        return result

    @staticmethod
    def _find_active_standard_task(records: list[Any]) -> Any | None:
        active = [
            record
            for record in records
            if str(record.status)
            in {"init", "env_preparing", "env_ready", "task_created", "task_executed"}
        ]
        return max(active, default=None, key=lambda record: int(getattr(record, "id", 0)))

    @staticmethod
    def _find_latest_standard_task(records: list[Any]) -> Any | None:
        return max(records, default=None, key=lambda record: int(getattr(record, "id", 0)))


    async def list_standard_sample_sets(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        if not bot_id or not bot_version:
            raise ServicePublicationConflictError("standard evaluation missing bot version")
        bot, _, _ = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        _, _, masa_eval_http = self._standard_backend()
        return self._list_sample_sets(
            bot_id=str(bot["bot_id"]),
            owner_id=str(bot["owner_id"]),
            masa_eval_http=masa_eval_http,
        )

    async def create_standard_task(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._quality_tasks or not self._masa_eval_http or not self._task_processor:
            raise ServicePublicationConflictError("standard evaluation backend is unavailable")
        bot, record, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )

        sample_sets = self._list_sample_sets(
            bot_id=str(bot["bot_id"]),
            owner_id=str(bot["owner_id"]),
            masa_eval_http=self._masa_eval_http,
        )
        sample_set = self._find_sample_set(
            task["set_uuid"],
            sample_sets.get("sets", []),
        )
        if not sample_set:
            raise ServicePublicationConflictError("selected evaluation sample set not found")

        tasks, _ = self._quality_tasks.list_tasks(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=str(bot["bot_id"]),
            owner_id=str(bot["owner_id"]),
            page=1,
            page_size=100,
        )
        standard_tasks = self._standard_task_records(tasks, bot_version=str(context.bot_version))
        active = self._find_active_standard_task(standard_tasks)
        if active is not None:
            self._start_standard_task_driver(
                active.id,
                context=context,
                trigger_source=str((active.ext or {}).get("trigger_source") or "manual_restart_eval"),
            )
            return self._standard_task_snapshot(active)

        trigger_source = str(task.get("trigger_source") or "manual_restart_eval")
        ext: dict[str, Any] = {
            "source": "standard_eval",
            "publish_id": record.id,
            "version": record.version,
            "set_uuid": task["set_uuid"],
            "sample_set_name": str(sample_set.get("name") or ""),
            "sample_set_count": sample_set.get("sample_count") or 0,
            "sample_set_url": str(sample_set.get("sample_set_url") or ""),
            "trigger_source": trigger_source,
        }
        goal_type = str(task.get("goal_type") or "json")
        ext["goal_type"] = goal_type
        if goal_type == "json":
            if isinstance(task.get("goal_json"), dict):
                ext["goal_json"] = task["goal_json"]
        else:
            ext["goal_text"] = str(task.get("goal_text") or "")
        created = self._quality_tasks.create_task(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=str(bot["bot_id"]),
            owner_id=str(bot["owner_id"]),
            ext=ext,
            operator_id=actor_id,
        )
        self._start_standard_task_driver(
            created.id,
            context=context,
            trigger_source=trigger_source,
        )
        return self._standard_task_snapshot(created)


    async def get_current_standard_task(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        if not self._quality_tasks:
            raise ServicePublicationConflictError("standard evaluation backend is unavailable")
        bot, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        tasks, _ = self._quality_tasks.list_tasks(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=str(bot["bot_id"]),
            owner_id=str(bot["owner_id"]),
            page=1,
            page_size=100,
        )
        standard_tasks = self._standard_task_records(tasks, bot_version=str(context.bot_version))
        current = self._find_latest_standard_task(standard_tasks)
        if current is None:
            raise ServicePublicationNotFoundError("standard evaluation task not found")
        if (
            self._is_processing_standard_task(current)
            or self._is_pending_standard_save(current)
        ):
            self._start_standard_task_driver(
                current.id,
                context=context,
                trigger_source=str((current.ext or {}).get("trigger_source") or "manual_restart_eval"),
            )
        return self._standard_task_snapshot(current)



    async def create_standard_environment(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        environment: dict[str, Any],
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        result = await self._plugin.create_standard_environment(
            context,
            default_tag=str(environment.get("default_tag") or "eval"),
            ttl_minutes=int(
                environment.get("ttl_minutes") or _STANDARD_EVAL_ENV_TTL_MINUTES
            ),
        )
        return self._public_standard_environment(self._standard_environment(result))

    async def get_current_standard_environment(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        result = await self._plugin.get_standard_environment_status(
            context,
            default_tag="eval",
        )
        return self._public_standard_environment(self._standard_environment(result))

    async def destroy_standard_environment(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        reason: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        result = await self._plugin.destroy_standard_environment(
            context,
            default_tag="eval",
            reason=reason,
        )
        return self._public_standard_environment_result(result, reason=reason)

    @staticmethod
    def _public_standard_environment_result(
        result: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Return only lifecycle-safe fields for the public destroy endpoint."""
        supported = bool(result.get("supported", True))
        destroyed = bool(result.get("destroyed"))
        destroy_status = str(
            result.get("destroy_status")
            or ("succeeded" if destroyed else "" if supported else "skipped")
        )
        return {
            "supported": supported,
            "default_tag": str(result.get("default_tag") or "eval"),
            "destroyed": destroyed,
            "destroy_status": destroy_status,
            "reason": str(result.get("reason") or reason),
            "error": str(result.get("error") or ""),
        }

    async def save_standard_result(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        result: dict[str, Any],
        trigger_source: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        return await self._plugin.save_standard_result(
            context,
            result,
            trigger_source=trigger_source,
        )

    async def sync(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        return await self._plugin.sync(context)

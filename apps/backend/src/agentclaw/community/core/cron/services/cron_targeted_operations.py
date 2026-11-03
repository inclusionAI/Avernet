"""Single-container Cron operations using existing authorized runtime bindings."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
    MULTI_INSTANCE_DEVICE_PROVIDERS,
)
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.runtime_binding.errors import (
    RuntimeBindingResolutionError,
)
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest, RuntimeBindingSource
from agentclaw.community.log import get_logger

logger = get_logger()


# The engine Cron contract defines notify.enabled and notify.user_ids only.
_CRON_LOG_FIELDS = {
    "task_id",
    "id",
    "name",
    "schedule",
    "command",
    "timezone",
    "enabled",
    "timeout_secs",
    "kind",
    "model",
    "owner_id",
    "runtime",
    "append_message",
    "runtime_stage",
    "device_uuid",
    "operator_id",
    "sessionKey",
    "session_key",
    "created_at",
    "updated_at",
    "last_run_at",
    "next_run_at",
    "running_count",
    "total_count",
    "status",
    "run_id",
    "started_at",
    "finished_at",
    "duration_secs",
    "output",
    "error",
}


def _cron_business_fields(data: dict) -> dict:
    """Project documented job fields, excluding authentication extension fields."""
    fields = {key: value for key, value in data.items() if key in _CRON_LOG_FIELDS}
    notify = data.get("notify")
    if isinstance(notify, dict):
        fields["notify"] = {
            key: value
            for key, value in notify.items()
            if key in {"enabled", "user_ids"}
        }
    return fields


class CronTargetedOperationsMixin:
    """Resolve and validate one target before making exactly one adapter call."""

    async def _resolve_single_cron_target(
        self,
        *,
        bot_id: str,
        user_id: str,
        runtime_stage: str,
        device_uuid: str | None,
        operator_id: str | None,
        authenticated_user_id: str,
    ) -> tuple[dict, CronRuntimeTarget]:
        self._validate_runtime_stage(runtime_stage)
        creator = operator_id or authenticated_user_id
        bot = self._bot_provider.get_bot(bot_id, user_id)
        owner_id = bot.get("owner_id") or user_id
        # COSEC: A supplied creator selects a Caller; it never grants management rights.
        if creator != authenticated_user_id:
            try:
                self._collaborators.check_permission(
                    bot["id"],
                    authenticated_user_id,
                    owner_id,
                    PermissionLevel.ADMIN,
                )
            except Exception as exc:
                raise CronRelayError(
                    "Cron delegation requires Bot management permission", 403
                ) from exc
        if bot.get("call_type") == "caller" and runtime_stage != "online":
            raise CronRelayError(
                "Caller Cron targets only support runtime_stage=online", 400
            )
        try:
            resolved = self._runtime_bindings.resolve(
                RuntimeBindingRequest(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_user_id=creator,
                    stage=runtime_stage,
                )
            )
        except EngineStageNotLiveError as exc:
            logger.warning(
                "cron.target.runtime_unavailable bot_id=%s stage=%s error=%s",
                bot_id,
                runtime_stage,
                exc,
            )
            stage_name = {"verify": "预发", "online": "生产"}.get(runtime_stage, "草稿")
            raise CronRelayError(
                f"{stage_name}运行环境当前不可用，请先发布到{stage_name}，待实例就绪后重试。",
                error_code=409,
            ) from exc
        except RuntimeBindingResolutionError as exc:
            raise CronRelayError(str(exc), error_code=409) from exc
        target = CronRuntimeTarget(
            bot_id=bot_id,
            bot_name=bot.get("bot_name", ""),
            owner_id=owner_id,
            bot_type=bot.get("bot_type") or "personal",
            runtime_stage=runtime_stage,
            binding_id=resolved.binding_id,
            operator_id=creator,
            authenticated_user_id=authenticated_user_id,
            fixed_binding=True,
            caller_binding=resolved.source is RuntimeBindingSource.CALLER_INSTANCE,
        )
        device = self._get_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )
        provider = str(self._read_field(device, "device_provider") or "").lower()
        if provider in MULTI_INSTANCE_DEVICE_PROVIDERS:
            try:
                devices = await self._get_runtime_devices(target)
            except TimeoutError as exc:
                raise CronRelayError("Runtime device query timed out", 504) from exc
            except Exception as exc:
                raise CronRelayError("Runtime device query failed", 409) from exc
            # COSEC: Membership is checked against the resolved Caller/stage binding.
            if device_uuid and device_uuid not in devices:
                raise CronRelayError(
                    "device_uuid does not belong to the requested runtime binding", 404
                )
            if not device_uuid:
                if len(devices) != 1:
                    raise CronRelayError(
                        "device_uuid is required for a multi-instance runtime", 400
                    )
                device_uuid = devices[0]
        elif device_uuid:
            # Single-instance providers expose their existing device identity on the binding.
            if device_uuid != self._read_field(device, "device_id"):
                raise CronRelayError(
                    "device_uuid does not belong to the requested runtime binding", 404
                )
        target = replace(target, device_uuid=device_uuid)
        logger.info(
            "cron.target.resolved bot_id=%s stage=%s binding_id=%s device_uuid=%s operator_id=%s authenticated_user_id=%s",
            bot_id,
            runtime_stage,
            target.binding_id,
            device_uuid,
            creator,
            authenticated_user_id,
        )
        return bot, target

    async def _forward_targeted_cron_request(
        self,
        *,
        bot_id: str,
        user_id: str,
        runtime_stage: str,
        device_uuid: str | None = None,
        operator_id: str | None = None,
        authenticated_user_id: str | None = None,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
        include_bot_context: bool = False,
        _resolved_target: CronRuntimeTarget | None = None,
    ) -> dict:
        started = monotonic()
        authenticated_user = authenticated_user_id or user_id
        fields = dict(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            device_uuid=device_uuid,
            operator_id=operator_id or authenticated_user,
            authenticated_user_id=authenticated_user,
            method=method,
            path=path,
        )
        # Authentication headers and arbitrary notify credentials are never logged.
        business = _cron_business_fields(body or {})
        logger.info(
            "cron.request system=backend direction=in operation=cron fields=%s body=%s params=%s",
            fields,
            business,
            params,
        )
        transport_started = False
        try:
            if _resolved_target is None:
                bot, target = await self._resolve_single_cron_target(
                    bot_id=bot_id,
                    user_id=user_id,
                    runtime_stage=runtime_stage,
                    device_uuid=device_uuid,
                    operator_id=operator_id,
                    authenticated_user_id=authenticated_user,
                )
            else:
                # COSEC: Only the authenticated list's resolved binding and member
                # snapshot enter here; HTTP target fields cannot supply this value.
                target = _resolved_target
                bot = {"bot_name": target.bot_name}
            logger.info(
                "cron.transport.request system=engine direction=out fields=%s binding_id=%s body=%s params=%s",
                fields,
                target.binding_id,
                business,
                params,
            )
            transport_started = True
            result = await self._forward_runtime_target_request(
                target,
                method=method,
                path=path,
                body=body,
                params=params,
            )
            self._decorate_single_result(
                result,
                target=target,
                bot=bot,
                include_runtime=True,
                include_bot_context=include_bot_context,
            )
            if isinstance(result.get("data"), dict):
                result["data"].update(
                    runtime_stage=target.runtime_stage,
                    device_uuid=target.device_uuid,
                    operator_id=target.operator_id,
                )
            if isinstance(result.get("data"), list):
                for item in result["data"]:
                    if isinstance(item, dict):
                        self._decorate_runtime_item(item, target)
            elif isinstance(result.get("data"), dict):
                for item in result["data"].get("running", []):
                    if isinstance(item, dict):
                        self._decorate_runtime_item(item, target)
            response_data = result.get("data", {})
            response_fields = (
                _cron_business_fields(response_data)
                if isinstance(response_data, dict)
                else [
                    _cron_business_fields(row)
                    for row in response_data
                    if isinstance(row, dict)
                ]
                if isinstance(response_data, list)
                else None
            )
            event = "success" if result.get("success", True) else "failure"
            logger.info(
                "cron.transport.%s system=engine direction=out fields=%s success=%s error_code=%s result=%s elapsed_ms=%.1f",
                event,
                fields,
                result.get("success", True),
                result.get("error_code"),
                response_fields,
                (monotonic() - started) * 1000,
            )
            logger.info(
                "cron.response system=backend direction=in fields=%s success=%s error_code=%s result=%s elapsed_ms=%.1f",
                fields,
                result.get("success", True),
                result.get("error_code"),
                response_fields,
                (monotonic() - started) * 1000,
            )
            return result
        except Exception as exc:
            error_message = (
                str(exc)
                if isinstance(exc, CronRelayError)
                else "Adapter request failed"
            )
            if transport_started:
                logger.warning(
                    "cron.transport.failure system=engine direction=out fields=%s exception_type=%s error_code=%s error=%s elapsed_ms=%.1f",
                    fields,
                    type(exc).__name__,
                    getattr(exc, "error_code", None),
                    error_message,
                    (monotonic() - started) * 1000,
                )
            logger.warning(
                "cron.failure system=backend direction=in fields=%s exception_type=%s error_code=%s error=%s elapsed_ms=%.1f",
                fields,
                type(exc).__name__,
                getattr(exc, "error_code", None),
                error_message,
                (monotonic() - started) * 1000,
            )
            raise

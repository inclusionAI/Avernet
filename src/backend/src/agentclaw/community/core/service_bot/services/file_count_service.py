"""Authorize and count each current runtime instance independently."""
import asyncio
from dataclasses import asdict
from time import monotonic
from typing import Any

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest, RuntimeBindingTarget
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.kernel.file_count import FileCountBinding, FileCountError, FileCountQuery, safe_log_fields
from agentclaw.community.log import get_logger

logger = get_logger()
INSTANCE_CONCURRENCY = 2


class FileCountService:
    def __init__(self, bots, runtime_bindings, bindings, permissions, runtime, env):
        self.bots, self.runtime_bindings, self.bindings = bots, runtime_bindings, bindings
        self.permissions, self.runtime, self.env = permissions, runtime, env

    async def query(
        self, query: FileCountQuery, operator_id: str, *, is_admin: bool,
    ) -> dict[str, Any]:
        started = monotonic()
        fields = {**asdict(query), "operator_id": operator_id, "system": "backend",
                  "direction": "inbound", "method": "GET",
                  "route": "/api/service-bot/publish/ops/file-count", "elapsed_ms": 0}
        logger.info("backend.file_count.request %s", safe_log_fields(fields))
        try:
            binding, targets = await self._resolve(query, operator_id, is_admin)
            semaphore = asyncio.Semaphore(INSTANCE_CONCURRENCY)

            async def count(target):
                async with semaphore:
                    return await self.runtime.query(binding, target, query, operator_id)

            results = await asyncio.gather(*(count(target) for target in targets))
            result = {"success": all(r["status"] == "success" for r in results),
                      "scope": "current_instances", "stage": query.stage, "path": query.path,
                      "request_id": query.request_id, "results": results}
            logger.info("backend.file_count.response %s", safe_log_fields({
                **fields, **result, "elapsed_ms": int((monotonic() - started) * 1000),
            }))
            return result
        except Exception as exc:
            # COSEC: exception text and connection credentials never enter logs.
            logger.warning("backend.file_count.failure %s", safe_log_fields({
                **fields, "status": "failed",
                "error_code": exc.code if isinstance(exc, FileCountError) else "operation_failed",
                "elapsed_ms": int((monotonic() - started) * 1000),
            }))
            raise

    async def _resolve(self, query, operator_id, is_admin):
        if not operator_id or operator_id == "anonymous":
            raise FileCountError("permission_denied")
        bot = await asyncio.to_thread(self.bots.get_by_id_and_entity, query.bot_id, query.entity_id)
        if not bot:
            raise FileCountError("bot_not_found")
        # COSEC: entity lookup alone never authorizes runtime directory access.
        if not is_admin:
            permission = await asyncio.to_thread(
                self.permissions.get_operable_permission_level,
                bot=bot, user_id=operator_id, env=self.env,
            )
            if permission < PermissionLevel.ADMIN:
                raise FileCountError("permission_denied")
        if bot.get("bot_type") != "service":
            raise FileCountError("not_service_bot")
        try:
            resolved = await asyncio.to_thread(
                self.runtime_bindings.resolve,
                RuntimeBindingRequest(bot_id=query.bot_id, owner_id=bot["owner_id"],
                                      actor_user_id=operator_id, stage=query.stage,
                                      environment=self.env, target=RuntimeBindingTarget.CALLER_SERVICE),
            )
        except (RuntimeBindingResolutionError, EngineStageNotLiveError):
            raise FileCountError("stage_not_bound") from None
        binding = await asyncio.to_thread(self.bindings.get_by_id, resolved.binding_id)
        if not binding:
            raise FileCountError("binding_conflict")
        if binding.device_provider not in {"baas", "arca"}:
            raise FileCountError("unsupported_provider")
        value = FileCountBinding(binding.id, binding.device_provider, binding.device_id)
        targets = await self.runtime.targets(value)
        if not targets:
            raise FileCountError("no_current_instances")
        return value, targets

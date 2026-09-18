"""Authorization and current-stage runtime selection for publish-ignore."""

import asyncio
from dataclasses import asdict
from time import monotonic
from typing import Any

from agentclaw.community.kernel.publish_ignore import (
    PublishIgnoreCommand,
    PublishIgnoreError,
    PublishIgnoreBinding,
)
from agentclaw.community.core.bot_collaborator.collaborator_service_protocol import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.runtime_binding.models import (
    RuntimeBindingRequest, RuntimeBindingTarget,
)
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.plugin_api.publish_ignore_runtime import PublishIgnoreRuntime
from agentclaw.community.log import get_logger

logger = get_logger()


class PublishIgnoreService:
    def __init__(
        self,
        bots: BotRepository,
        runtime_bindings: RuntimeBindingResolutionService,
        bindings: DeviceBindingRepository,
        permissions: CollaboratorServiceProtocol,
        runtime: PublishIgnoreRuntime,
        env: str,
    ):
        self.bots, self.runtime_bindings, self.bindings = bots, runtime_bindings, bindings
        self.permissions, self.runtime, self.env = permissions, runtime, env

    async def change(
        self, command: PublishIgnoreCommand, operator_id: str, *, is_admin: bool
    ) -> dict[str, Any]:
        started = monotonic()
        fields = {
            **asdict(command),
            "operator_id": operator_id,
            "system": "backend",
            "direction": "inbound",
            "method": "POST",
            "route": "/api/service-bot/publish/ops/publish-ignore",
            "elapsed_ms": 0,
        }
        logger.info("backend.publish_ignore.request %s", fields)
        try:
            result = await self._change(command, operator_id, is_admin)
            logger.info(
                "backend.publish_ignore.response %s",
                {**fields, **result, "elapsed_ms": int((monotonic() - started) * 1000)},
            )
            return result
        except Exception as exc:
            logger.warning(
                "backend.publish_ignore.failure %s",
                {
                    **fields,
                    "error_type": type(exc).__name__,
                    "code": exc.code
                    if isinstance(exc, PublishIgnoreError)
                    else "operation_failed",
                    "elapsed_ms": int((monotonic() - started) * 1000),
                },
            )
            raise

    async def _change(self, command, operator_id, is_admin):
        if not operator_id or operator_id == "anonymous":
            raise PublishIgnoreError("permission_denied")
        bot = await asyncio.to_thread(
            self.bots.get_by_id_and_entity, command.bot_id, command.entity_id
        )
        if not bot:
            raise PublishIgnoreError("bot_not_found")
        # COSEC: entity is a lookup key, never proof of mutation permission.
        if not is_admin:
            level = await asyncio.to_thread(
                self.permissions.get_operable_permission_level,
                bot=bot,
                user_id=operator_id,
                env=self.env,
            )
            if level < PermissionLevel.ADMIN:
                raise PublishIgnoreError("permission_denied")
        if bot.get("bot_type") != "service":
            raise PublishIgnoreError("not_service_bot")
        try:
            resolved = await asyncio.to_thread(
                self.runtime_bindings.resolve,
                RuntimeBindingRequest(
                    bot_id=command.bot_id, owner_id=bot["owner_id"],
                    actor_user_id=operator_id, stage=command.stage,
                    environment=self.env, target=RuntimeBindingTarget.CALLER_SERVICE,
                ),
            )
        except (RuntimeBindingResolutionError, EngineStageNotLiveError) as exc:
            raise PublishIgnoreError("stage_not_bound") from exc
        binding = await asyncio.to_thread(self.bindings.get_by_id, resolved.binding_id)
        if not binding:
            raise PublishIgnoreError("binding_conflict")
        if binding.device_provider not in {"baas", "arca"}:
            raise PublishIgnoreError("unsupported_provider")
        runtime_binding = PublishIgnoreBinding(
            binding.id, binding.device_provider, binding.device_id
        )
        targets = await self.runtime.targets(runtime_binding)
        if not targets:
            raise PublishIgnoreError("no_current_instances")
        results = []
        for target in targets:
            results.append(
                await self.runtime.change(runtime_binding, target, command, operator_id)
            )
        return {
            "success": all(r["status"] in {"changed", "unchanged"} for r in results),
            "scope": "current_instances",
            "results": results,
            "request_id": command.request_id,
        }

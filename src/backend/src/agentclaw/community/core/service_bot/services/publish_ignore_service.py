"""Exact release selection and authorization for publish-ignore updates."""

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
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.plugin_api.publish_ignore_runtime import PublishIgnoreRuntime
from agentclaw.community.log import get_logger

logger = get_logger()


class PublishIgnoreService:
    def __init__(
        self,
        bots: BotRepository,
        publications: BotPublishRepositoryProtocol,
        bindings: DeviceBindingRepository,
        permissions: CollaboratorServiceProtocol,
        runtime: PublishIgnoreRuntime,
        env: str,
    ):
        self.bots, self.publications, self.bindings = bots, publications, bindings
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
        if command.stage not in {"verify", "online"} or command.operation not in {
            "add",
            "remove",
        }:
            raise PublishIgnoreError("invalid_operation")
        records = await asyncio.to_thread(
            self.publications.list_by_source_bot, bot["id"], self.env
        )
        matches = [r for r in records if r.version == command.version]
        if len(matches) != 1:
            raise PublishIgnoreError("version_not_unique_or_missing")
        publication = matches[0]
        if (publication.ext or {}).get("restart", {}).get("restarting"):
            raise PublishIgnoreError("restart_in_progress")
        if publication.status not in {"validating", "success", "upgraded"}:
            raise PublishIgnoreError("publication_not_ready")
        binding_id = (publication.ext or {}).get("binding", {}).get(command.stage)
        if not binding_id:
            raise PublishIgnoreError("stage_not_bound")
        binding = await asyncio.to_thread(self.bindings.get_by_id, binding_id)
        if (
            not binding
            or binding.status != "ACTIVE"
            or binding.entity_id != command.entity_id
            or binding.env != self.env
        ):
            raise PublishIgnoreError("binding_conflict")
        if binding.device_provider not in {"baas", "arca"}:
            raise PublishIgnoreError("unsupported_provider")
        declared_bot = binding.device_props.get("bolt_id") or binding.device_props.get(
            "bot_id"
        )
        if declared_bot and declared_bot not in {
            command.bot_id,
            publication.publish_bot_id,
        }:
            raise PublishIgnoreError("binding_bot_mismatch")
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
        try:
            refreshed = await asyncio.to_thread(
                self.publications.list_by_source_bot, bot["id"], self.env
            )
            same_version = [r for r in refreshed if r.version == command.version]
            publication_changed = (
                len(same_version) != 1
                or (same_version[0].ext or {}).get("binding", {}).get(command.stage)
                != binding_id
                or bool(
                    (same_version[0].ext or {}).get("restart", {}).get("restarting")
                )
                or same_version[0].status not in {"validating", "success", "upgraded"}
            )
            current_binding = await asyncio.to_thread(
                self.bindings.get_by_id, binding_id
            )
            current = await self.runtime.targets(runtime_binding)
            target_changed = (
                publication_changed
                or set(current) != set(targets)
                or not current_binding
                or current_binding.status != "ACTIVE"
                or current_binding.device_id != binding.device_id
            )
            snapshot_status = "changed" if target_changed else "stable"
        except Exception:
            target_changed = True
            snapshot_status = "unknown"
        success = (
            all(r["status"] in {"changed", "unchanged"} for r in results)
            and not target_changed
        )
        result = {
            "success": success,
            "scope": "current_instances",
            "results": results,
            "targets_changed": target_changed,
            "snapshot_status": snapshot_status,
            "request_id": command.request_id,
        }
        return result

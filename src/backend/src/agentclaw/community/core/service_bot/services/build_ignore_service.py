"""Manage next-build rules without contacting runtime containers."""

import asyncio
from time import monotonic
from typing import Any

from agentclaw.community.core.bot_collaborator.collaborator_service_protocol import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.engines.registry import resolve_bot_engine
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.build_ignore import (
    BuildIgnoreRepositoryProtocol,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.kernel.build_ignore import (
    BuildIgnoreCommand,
    BuildIgnoreQuery,
    BuildIgnoreConfig,
    BuildIgnoreError,
    normalize_build_ignore_path,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BuildIgnoreService:
    def __init__(
        self,
        bots: BotRepository,
        permissions: CollaboratorServiceProtocol,
        rules: BuildIgnoreRepositoryProtocol,
        sandbox_registry: EngineSandboxRegistry,
        env: str,
    ):
        self.bots, self.permissions, self.rules = bots, permissions, rules
        self.sandbox_registry, self.env = sandbox_registry, env

    async def query(
        self, query: BuildIgnoreQuery, operator_id: str, *, is_admin: bool
    ) -> dict[str, Any]:
        return await self._execute(query, operator_id, is_admin)

    async def change(
        self, command: BuildIgnoreCommand, operator_id: str, *, is_admin: bool
    ) -> dict[str, Any]:
        return await self._execute(command, operator_id, is_admin)

    async def _execute(self, command, operator_id, is_admin):
        from .build_ignore_rules import validate_required_paths

        started = monotonic()
        changing = isinstance(command, BuildIgnoreCommand)
        fields = dict(
            system="backend",
            direction="inbound",
            request_id=command.request_id,
            bot_id=command.bot_id,
            entity_id=command.entity_id,
            operator_id=operator_id,
            method="POST" if changing else "GET",
            operation=command.operation if changing else "query",
            route="/api/service-bot/publish/ops/build-ignore",
        )
        if changing:
            try:
                fields["path"] = normalize_build_ignore_path(command.path)
            except BuildIgnoreError:
                # COSEC: do not echo rejected control characters into boundary logs.
                fields["path_valid"] = False
        logger.info("backend.build_ignore.request %s", {
            **fields, "status": "received", "elapsed_ms": 0,
        })
        try:
            if not operator_id or operator_id == "anonymous":
                raise BuildIgnoreError("permission_denied")
            bot = await asyncio.to_thread(
                self.bots.get_by_id_and_entity, command.bot_id, command.entity_id
            )
            if not bot:
                raise BuildIgnoreError("bot_not_found")
            # COSEC: identifiers select a resource; only trusted actor permissions authorize it.
            if not is_admin:
                level = await asyncio.to_thread(
                    self.permissions.get_operable_permission_level,
                    bot=bot,
                    user_id=operator_id,
                    env=self.env,
                )
                if level < PermissionLevel.ADMIN:
                    raise BuildIgnoreError("permission_denied")
            if bot.get("bot_type") != "service":
                raise BuildIgnoreError("not_service_bot")
            provider = self.sandbox_registry.resolve(
                resolve_bot_engine(bot) or DEFAULT_ENGINE_TYPE
            )
            plan = provider.get_build_plan(bot=bot)
            fields["engine_type"] = plan.engine_type
            key = dict(
                env=self.env,
                bot_id=command.bot_id,
                entity_id=command.entity_id,
                engine_type=plan.engine_type,
            )
            changed = False
            if changing:
                path = normalize_build_ignore_path(command.path)
                fields["path"] = path
                if command.operation == "add":
                    try:
                        validate_required_paths((path,), plan)
                    except ValueError as exc:
                        raise BuildIgnoreError(str(exc)) from None
                config, changed = await asyncio.to_thread(
                    self.rules.change,
                    **key,
                    operation=command.operation,
                    path=path,
                    modifier=operator_id,
                )
            else:
                config = (
                    await asyncio.to_thread(self.rules.get, **key)
                    or BuildIgnoreConfig()
                )
            result = dict(
                success=True,
                scope="build",
                bot_id=command.bot_id,
                entity_id=command.entity_id,
                engine_type=plan.engine_type,
                paths=list(config.paths),
                revision=config.revision,
                changed=changed,
                request_id=command.request_id,
            )
            log_result = dict(result)
            if sum(len(p.encode()) for p in config.paths) > 4096:
                log_result.pop("paths")
                log_result["path_count"] = len(config.paths)
            logger.info(
                "backend.build_ignore.response %s",
                {
                    **fields,
                    **log_result,
                    "status": "succeeded",
                    "elapsed_ms": int((monotonic() - started) * 1000),
                },
            )
            return result
        except Exception as exc:
            # COSEC: SQL/driver exception messages can contain reusable credentials.
            logger.warning(
                "backend.build_ignore.failure %s",
                {
                    **fields,
                    "status": "failed",
                    "code": exc.code
                    if isinstance(exc, BuildIgnoreError)
                    else "operation_failed",
                    "error_type": type(exc).__name__,
                    "elapsed_ms": int((monotonic() - started) * 1000),
                },
            )
            raise

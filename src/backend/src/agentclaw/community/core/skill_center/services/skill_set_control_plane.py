"""Canonical SkillSet service: ACL, UoW command, one runtime reconcile."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetControlPlaneRepository, SkillSetMutation,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError, SkillSetControlPlaneConflictError,
)
from agentclaw.community.api.skill_set_access import SkillSetAccessProtocol
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.plugin_api.passport import PassportPlugin


class SkillSetRuntimeReconcilerProtocol(Protocol):
    async def reconcile(self, *, bot_id: str, owner_id: str) -> None: ...


class SkillSetRuntimeReconciler:
    """Runtime adapter shared with the legacy compatibility surface.

    The control-plane service deliberately calls this once after a successful
    UoW; it never calls the legacy per-item activate/deactivate paths.
    """

    @inject
    def __init__(self, factory: SkillSetServiceFactory, bot_repo: BotRepository) -> None:
        self._factory = factory
        self._bot_repo = bot_repo

    async def reconcile(self, *, bot_id: str, owner_id: str) -> None:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        service = self._factory.create(
            user_id=owner_id, entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id, engine_type=bot.get("active_engine"),
            entity_type=bot.get("entity_type") or "staff",
        )
        if not service.sync_runtime():
            raise SkillSetRuntimeReconcileError()


class SkillSetRuntimeReconcileError(Exception):
    """A committed desired-state change was compensated after runtime failure."""


class SkillSetControlPlaneService:
    @inject
    def __init__(self, repository: SkillSetControlPlaneRepository, runtime: SkillSetRuntimeReconcilerProtocol, legacy_factory: SkillSetServiceFactory, passport: PassportPlugin, access: SkillSetAccessProtocol, edit_guard: SkillsPoolEditGuard) -> None:
        self._repository = repository
        self._runtime = runtime
        self._legacy_factory = legacy_factory
        self._passport = passport
        self._access = access
        self._edit_guard = edit_guard

    def _bot(self, bot_id: str, actor_id: str) -> dict:
        return self._access.resolve_bot(bot_id=bot_id, actor_id=actor_id)

    def list_sets(self, *, bot_id: str, actor_id: str) -> list[dict]:
        self._bot(bot_id, actor_id)
        return self._repository.list_sets(bot_id=bot_id)

    def create_set(self, *, bot_id: str, actor_id: str, name: str, description: str | None, idempotency_key: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return self._repository.create_set(bot_id=bot_id, owner_id=str(bot.get("owner_id") or actor_id), name=name, description=description, idempotency_key=idempotency_key)

    def get_set(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        self._bot(bot_id, actor_id)
        return self._repository.get_set(bot_id=bot_id, set_id=set_id)

    def update_set(self, *, bot_id: str, actor_id: str, set_id: str, name: str | None, description: str | None) -> dict:
        self._bot(bot_id, actor_id)
        return self._repository.update_set(bot_id=bot_id, set_id=set_id, name=name, description=description)

    def delete_set(self, *, bot_id: str, actor_id: str, set_id: str) -> None:
        self._bot(bot_id, actor_id)
        self._repository.delete_set(bot_id=bot_id, set_id=set_id)

    def list_skills(self, *, bot_id: str, actor_id: str, set_id: str) -> list[dict]:
        self._bot(bot_id, actor_id)
        return self._repository.list_skills(bot_id=bot_id, set_id=set_id)

    async def add_skill(self, *, bot_id: str, actor_id: str, set_id: str, skill_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(bot_id, bot, lambda: self._repository.add_skill(bot_id=bot_id, set_id=set_id, skill_id=skill_id))

    async def remove_skill(self, *, bot_id: str, actor_id: str, set_id: str, skill_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(bot_id, bot, lambda: self._repository.remove_skill(bot_id=bot_id, set_id=set_id, skill_id=skill_id))

    async def activate(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(bot_id, bot, lambda: self._repository.set_active(bot_id=bot_id, set_id=set_id, active=True))

    async def deactivate(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(bot_id, bot, lambda: self._repository.set_active(bot_id=bot_id, set_id=set_id, active=False))

    def resources(self, *, bot_id: str, actor_id: str) -> list[dict]:
        bot = self._bot(bot_id, actor_id)
        owner_id = str(bot.get("owner_id") or actor_id)
        legacy = self._legacy_factory.create(
            user_id=owner_id, entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id, engine_type=bot.get("active_engine"),
            entity_type=bot.get("entity_type") or "staff",
        )
        # Passport is an external source of truth for Default CLI scope.  An
        # unavailable source is observable to callers; returning an empty list
        # would falsely state that there are no CLI capabilities.
        default_clis = self._passport.query_passport_clis(
            bot_id, str(bot.get("entity_id") or owner_id)
        )
        return [
            {
                **item,
                "mcps": legacy.get_set_mcp_servers(item["id"], user_id=owner_id, bot_id=bot_id),
                "clis": default_clis if item["is_default"] else [],
            }
            for item in self._repository.list_sets(bot_id=bot_id)
        ]

    async def _mutate(
        self, bot_id: str, bot: dict, command: Callable[[], SkillSetMutation]
    ) -> dict:
        """Serialize DB UoW, projection and compensation for one Bot.

        Releasing the guard after the compensating projection is essential: a
        later mutation must never snapshot a desired state while a failed
        predecessor is restoring it.
        """
        if not is_bot_ready(bot):
            raise SkillSetControlPlaneConflictError("BOT_NOT_READY")
        scope = BotSkillLayoutScope(
            env=str(bot["env"]), entity_id=str(bot["entity_id"]), bot_id=bot_id
        )
        lease = self._edit_guard.acquire_for_edit(scope=scope)
        try:
            mutation = command()
            return await self._reconcile(
                bot_id, str(bot.get("owner_id")), mutation
            )
        finally:
            self._edit_guard.release(lease)

    async def _reconcile(self, bot_id: str, owner_id: str, mutation: SkillSetMutation) -> dict:
        try:
            await self._runtime.reconcile(bot_id=bot_id, owner_id=owner_id)
        except Exception as exc:
            self._repository.restore_desired_state(bot_id=bot_id, state=mutation.previous_state)
            try:
                await self._runtime.reconcile(bot_id=bot_id, owner_id=owner_id)
            except Exception as restore_error:
                raise SkillSetRuntimeReconcileError() from restore_error
            raise SkillSetRuntimeReconcileError() from exc
        return {**mutation.item, "changed": mutation.changed}

"""Canonical SkillSet service: ACL, UoW command, one runtime reconcile."""

from __future__ import annotations

from typing import Protocol

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.skill_set_control_plane import (
    SkillSetControlPlaneRepositoryProtocol,
)
from agentclaw.community.core.repository.skill_set_control_plane_types import (
    SkillSetMutation,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneLockUnavailableError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.services.bot_capability_mutation_guard import (
    BotCapabilityMutationBusyError,
    BotCapabilityMutationGuard,
    BotCapabilityMutationLockUnavailableError,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.plugin_api.passport import PassportPlugin


class SkillSetRuntimeReconcilerProtocol(Protocol):
    async def reconcile(self, *, bot_id: str, owner_id: str) -> None: ...


class SkillSetRuntimeReconciler:
    """Runtime adapter shared with the legacy compatibility surface.

    The control-plane service deliberately calls this once after a successful
    UoW; it never calls the legacy per-item activate/deactivate paths.
    """

    @inject
    def __init__(
        self, factory: SkillSetServiceFactory, bot_repo: BotRepository
    ) -> None:
        self._factory = factory
        self._bot_repo = bot_repo

    async def reconcile(self, *, bot_id: str, owner_id: str) -> None:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        service = self._factory.create(
            user_id=owner_id,
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=bot.get("entity_type") or "staff",
        )
        if not service.sync_runtime():
            raise SkillSetRuntimeReconcileError()


class SkillSetControlPlaneService:
    @inject
    def __init__(
        self,
        repository: SkillSetControlPlaneRepositoryProtocol,
        bot_repo: BotRepository,
        runtime: SkillSetRuntimeReconcilerProtocol,
        legacy_factory: SkillSetServiceFactory,
        passport: PassportPlugin,
        collaborators: CollaboratorServiceProtocol,
        mutation_guard: BotCapabilityMutationGuard,
        edit_guard: SkillsPoolEditGuard,
        audit_log_repo: BotCollabLogRepositoryProtocol,
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo
        self._runtime = runtime
        self._legacy_factory = legacy_factory
        self._passport = passport
        self._collaborators = collaborators
        self._mutation_guard = mutation_guard
        self._edit_guard = edit_guard
        self._audit_log_repo = audit_log_repo

    def _bot(self, bot_id: str, actor_id: str) -> dict:
        # Resolve the record before authorizing.  ``actor_id`` is an audit and
        # ACL subject, never a substitute for the Bot's durable owner: a
        # collaborator (or an app delegated by one) must reconcile using the
        # true owner and its runtime layout.
        bot = self._bot_repo.get_by_id(bot_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        owner_id = str(bot["owner_id"])
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        return bot

    @staticmethod
    def _scope(bot: dict, bot_id: str) -> BotSkillLayoutScope:
        return BotSkillLayoutScope(
            env=str(bot["env"]), entity_id=str(bot["entity_id"]), bot_id=bot_id
        )

    def list_sets(self, *, bot_id: str, actor_id: str) -> list[dict]:
        bot = self._bot(bot_id, actor_id)
        return self._repository.list_sets(bot_id=bot_id, engine_type=self._engine(bot))

    def create_set(
        self,
        *,
        bot_id: str,
        actor_id: str,
        name: str,
        description: str | None,
        idempotency_key: str,
    ) -> dict:
        bot = self._bot(bot_id, actor_id)
        item = self._repository.create_set(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            name=name,
            description=description,
            idempotency_key=idempotency_key,
            engine_type=self._engine(bot),
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=actor_id,
            action="skill_set_create",
        )
        return item

    def create_legacy_set(
        self,
        *,
        bot_id: str,
        actor_id: str,
        name: str,
        description: str | None,
        idempotency_key: str,
    ) -> dict:
        """Preserve pre-Bot-record legacy creation without weakening canonical ACL."""
        bot = self._bot_repo.get_by_id(bot_id)
        if bot is not None:
            return self.create_set(
                bot_id=bot_id,
                actor_id=actor_id,
                name=name,
                description=description,
                idempotency_key=idempotency_key,
            )
        item = self._repository.create_set(
            bot_id=bot_id,
            owner_id=actor_id,
            name=name,
            description=description,
            idempotency_key=idempotency_key,
            engine_type="openclaw",
        )
        self._audit(
            bot_id=bot_id,
            owner_id=actor_id,
            actor_id=actor_id,
            action="skill_set_create_legacy",
        )
        return item

    def get_set(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return self._repository.get_set(
            bot_id=bot_id, set_id=set_id, engine_type=self._engine(bot)
        )

    def get_legacy_set(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        """Read a pre-Bot-record legacy set; canonical reads remain strict."""
        bot = self._bot_repo.get_by_id(bot_id)
        if bot is not None:
            return self.get_set(bot_id=bot_id, actor_id=actor_id, set_id=set_id)
        return self._repository.get_set(bot_id=bot_id, set_id=set_id, engine_type="openclaw")

    def update_set(
        self,
        *,
        bot_id: str,
        actor_id: str,
        set_id: str,
        name: str | None,
        description: str | None,
    ) -> dict:
        bot = self._bot(bot_id, actor_id)
        item = self._repository.update_set(
            bot_id=bot_id,
            set_id=set_id,
            name=name,
            description=description,
            engine_type=self._engine(bot),
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=actor_id,
            action="skill_set_update",
        )
        return item

    def delete_set(self, *, bot_id: str, actor_id: str, set_id: str) -> None:
        bot = self._bot(bot_id, actor_id)
        self._repository.delete_set(
            bot_id=bot_id, set_id=set_id, engine_type=self._engine(bot)
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=actor_id,
            action="skill_set_delete",
        )

    def list_skills(self, *, bot_id: str, actor_id: str, set_id: str) -> list[dict]:
        bot = self._bot(bot_id, actor_id)
        return self._repository.list_skills(
            bot_id=bot_id, set_id=set_id, engine_type=self._engine(bot)
        )

    def resolve_legacy_skill_id(
        self, *, bot_id: str, actor_id: str, identifier: str
    ) -> str:
        self._bot(bot_id, actor_id)
        return self._repository.resolve_legacy_skill_id(
            bot_id=bot_id, identifier=identifier
        )

    async def add_skill(
        self, *, bot_id: str, actor_id: str, set_id: str, skill_id: str
    ) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_add_skill",
            mutation=lambda: self._repository.add_skill(
                bot_id=bot_id,
                set_id=set_id,
                skill_id=skill_id,
                engine_type=self._engine(bot),
            ),
        )

    async def remove_skill(
        self, *, bot_id: str, actor_id: str, set_id: str, skill_id: str
    ) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_remove_skill",
            mutation=lambda: self._repository.remove_skill(
                bot_id=bot_id,
                set_id=set_id,
                skill_id=skill_id,
                engine_type=self._engine(bot),
            ),
        )

    async def activate(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_activate",
            mutation=lambda: self._repository.set_active(
                bot_id=bot_id, set_id=set_id, active=True, engine_type=self._engine(bot)
            ),
        )

    async def deactivate(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_deactivate",
            mutation=lambda: self._repository.set_active(
                bot_id=bot_id,
                set_id=set_id,
                active=False,
                engine_type=self._engine(bot),
            ),
        )

    async def switch(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        """Compatibility command for the deprecated single-select switch API."""
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_switch",
            mutation=lambda: self._repository.replace_active_set(
                bot_id=bot_id, set_id=set_id, engine_type=self._engine(bot)
            ),
        )

    async def sync(self, *, bot_id: str, actor_id: str, set_id: str) -> dict:
        """Compatibility command for legacy sync's single-select semantics."""
        bot = self._bot(bot_id, actor_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_sync",
            mutation=lambda: self._repository.replace_active_set(
                bot_id=bot_id, set_id=set_id, engine_type=self._engine(bot)
            ),
        )

    def resources(self, *, bot_id: str, actor_id: str) -> list[dict]:
        bot = self._bot(bot_id, actor_id)
        owner_id = str(bot["owner_id"])
        legacy = self._legacy_factory.create(
            user_id=owner_id,
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=bot.get("entity_type") or "staff",
        )
        # Resource reads preserve the legacy graceful degradation: an AgentPass
        # outage hides Default CLI entries but must not hide SkillSet/MCP data.
        try:
            default_clis = self._passport.query_passport_clis(
                bot_id, str(bot.get("entity_id") or owner_id)
            )
        except Exception:
            default_clis = []
        return [
            {
                **item,
                "mcps": legacy.get_set_mcp_servers(
                    item["id"], user_id=owner_id, bot_id=bot_id
                ),
                "clis": default_clis if item["is_default"] else [],
            }
            for item in self._repository.list_sets(
                bot_id=bot_id, engine_type=self._engine(bot)
            )
        ]

    async def _mutate(
        self, *, bot: dict, bot_id: str, actor_id: str, action: str, mutation
    ) -> dict:
        """Keep one Bot edit lease across UoW, projection and compensation.

        Releasing the lease after the database write would let a Direct command
        or another SkillSet request race ``restore_desired_state`` and have its
        desired state erased.  The lock is intentionally held for the complete
        externally-visible command, including the restore projection.
        """
        scope = self._scope(bot, bot_id)
        try:
            mutation_lease = self._mutation_guard.acquire(scope=scope)
        except BotCapabilityMutationBusyError as exc:
            raise SkillSetControlPlaneConflictError("BOT_MUTATION_BUSY") from exc
        except BotCapabilityMutationLockUnavailableError as exc:
            raise SkillSetControlPlaneLockUnavailableError() from exc
        try:
            try:
                lease = self._edit_guard.acquire_for_edit(scope=scope)
            except (
                SkillsPoolEditBusyError,
                SkillsPoolEditRollbackError,
                SkillsPoolEditPausedError,
            ) as exc:
                raise SkillSetControlPlaneConflictError("BOT_MUTATION_BUSY") from exc
            except SkillsPoolEditLockUnavailableError as exc:
                raise SkillSetControlPlaneLockUnavailableError() from exc
            try:
                if not is_bot_ready(bot):
                    raise LocalSkillNotReadyError()
                mutation_result = mutation()
                # An inactive-set membership change has no runtime projection
                # to apply.  Reconcile only becomes a required side effect
                # when that membership is active (or for all lifecycle/sync
                # commands), preserving the legacy inactive draft contract.
                if (
                    action in {"skill_set_add_skill", "skill_set_remove_skill"}
                    and not mutation_result.item.get("is_active")
                ):
                    result = {
                        **mutation_result.item,
                        "changed": mutation_result.changed,
                        **mutation_result.details,
                    }
                else:
                    result = await self._reconcile(
                        bot=bot,
                        bot_id=bot_id,
                        actor_id=actor_id,
                        mutation=mutation_result,
                    )
                self._audit(
                    bot_id=bot_id,
                    owner_id=str(bot["owner_id"]),
                    actor_id=actor_id,
                    action=action,
                )
                return result
            finally:
                self._edit_guard.release(lease)
        finally:
            self._mutation_guard.release(mutation_lease)

    async def _reconcile(
        self, *, bot: dict, bot_id: str, actor_id: str, mutation: SkillSetMutation
    ) -> dict:
        owner_id = str(bot["owner_id"])
        try:
            await self._runtime.reconcile(bot_id=bot_id, owner_id=owner_id)
        except Exception as exc:
            self._repository.restore_desired_state(
                bot_id=bot_id,
                state=mutation.previous_state,
                engine_type=self._engine(bot),
            )
            try:
                await self._runtime.reconcile(bot_id=bot_id, owner_id=owner_id)
            except Exception as restore_error:
                raise SkillSetRuntimeReconcileError() from restore_error
            raise SkillSetRuntimeReconcileError() from exc
        return {**mutation.item, "changed": mutation.changed, **mutation.details}

    def _audit(self, *, bot_id: str, owner_id: str, actor_id: str, action: str) -> None:
        self._audit_log_repo.insert(
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "operator_id": actor_id,
                "detail": f'{{"action":"{action}"}}',
            }
        )

    @staticmethod
    def _engine(bot: dict) -> str:
        return str(bot["active_engine"])

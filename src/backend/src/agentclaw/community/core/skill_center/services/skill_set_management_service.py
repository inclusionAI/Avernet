"""Canonical SkillSet service: ACL, UoW command, one runtime reconcile."""

from __future__ import annotations

from collections.abc import Sequence

from injector import inject

from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
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
    LocalSkillNotReadyError,
    McpPermissionDeniedError,
    SkillSetControlPlaneNotFoundError,
    SkillSetAccessDeniedError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetCompatibilityFactoryProtocol,
    LegacySkillSetScope,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectionReconcilerProtocol,
)
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.plugin_api.passport import PassportPlugin


class SkillSetManagementService:
    @inject
    def __init__(
        self,
        repository: SkillSetControlPlaneRepositoryProtocol,
        bot_repo: BotRepository,
        runtime: BotRuntimeProjectionReconcilerProtocol,
        legacy_factory: LegacySkillSetCompatibilityFactoryProtocol,
        passport: PassportPlugin,
        authorization: BotCapabilityAuthorizationHookProtocol,
        audit_log_repo: BotCollabLogRepositoryProtocol,
        mcp_center: MCPCenterPlugin,
        mcp_auth: MCPAuthPlugin,
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo
        self._runtime = runtime
        self._legacy_factory = legacy_factory
        self._passport = passport
        self._authorization = authorization
        self._audit_log_repo = audit_log_repo
        self._mcp_center = mcp_center
        self._mcp_auth = mcp_auth

    def _bot(self, *, bot_id: str, owner_id: str, user_id: str) -> dict:
        """Resolve the exact addressed Bot before applying caller policy."""
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            # The SkillSet control plane speaks one error vocabulary so the HTTP
            # adapter maps a single family: an invisible Bot scope is a SkillSet
            # not-found, not a Local Skill one.
            raise SkillSetControlPlaneNotFoundError()
        # **Stays, even though the nineteen /openapi/v1 rows are Check(MEMBER).**
        #
        # This service is reached from two surfaces, and the seam covers one.
        # ``adapters/http/skill_center/skillsets.py`` is mounted at
        # ``/api/skillsets``, outside ``/openapi/v1`` entirely and governed by no
        # row in ``AUTHORIZATION`` — and four of its routes carry no
        # ``CollaboratorPermissionInterceptor`` of their own:
        #
        #     GET  /api/skillsets/{skill_set_id}          -> get_set
        #     PUT  /api/skillsets/{skill_set_id}          -> update_set
        #     GET  /api/skillsets/{skill_set_id}/skills   -> list_skills
        #     GET  /api/skillsets/{skill_set_id}/mcps     -> get_set, list_mcps
        #
        # All four take ``entity_id`` and ``bot_id`` as caller-supplied query
        # parameters, so this call is the only thing standing between an
        # authenticated stranger and another owner's SkillSet — a read on three
        # of them and a **write** on the ``PUT``.
        #
        # Deleting it to "finish" the migration was tried and was wrong; a P1
        # review finding caught it. The row still migrates, and means what it
        # says: for the ``/openapi/v1`` operations ``bot_access`` is the declared
        # authority and adjudicates first, at this same MEMBER bar. Here that
        # makes this a redundant second gate; at ``/api/skillsets`` it is the
        # only one. See ``bot_skill_asset_service._resolve_local`` for the same
        # shape, and ``test_the_control_plane_check_the_legacy_surface_relies_on
        # _still_exists``, which pins it.
        if not self._authorization.can_manage_bot(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=user_id,
        ):
            raise SkillSetAccessDeniedError()
        return bot

    def _legacy_bot(self, *, bot_id: str, owner_id: str, actor_id: str) -> dict:
        """Resolve a Legacy-wire Bot by its durable owner-qualified identity.

        ``bot_id`` is not globally unique: the historical virtual ``default``
        Bot exists once per owner.  Legacy adapters already resolve the owner
        from their entity input, so they must never fall back to a global
        lookup here.
        """
        return self._bot(bot_id=bot_id, owner_id=owner_id, user_id=actor_id)

    def list_sets(self, *, bot_id: str, owner_id: str, user_id: str) -> list[dict]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return self._repository.list_sets(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    def create_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        name: str,
        description: str | None,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        # Creating an inactive SkillSet is metadata-only: it neither changes
        # the effective capability projection nor has a compensating runtime
        # action, so it does not enter the Pool edit boundary.
        item = self._repository.create_set(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            name=name,
            description=description,
            engine_type=self._engine(bot),
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=user_id,
            action="skill_set_create",
        )
        return item

    def get_set(self, *, bot_id: str, owner_id: str, user_id: str, set_id: str) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return self._repository.get_set(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    def resolve_legacy_set_scope(
        self,
        *,
        set_id: str,
        actor_id: str,
        owner_id_hint: str | None,
    ) -> LegacySkillSetScope | None:
        """Recover a deprecated wire's omitted Bot without weakening strict reads."""
        scope = self._repository.resolve_legacy_set_scope(set_id=set_id)
        if scope is None:
            return None
        if owner_id_hint is not None and owner_id_hint != scope.owner_id:
            raise SkillSetControlPlaneNotFoundError()
        bot = self._bot(
            bot_id=scope.bot_id,
            owner_id=scope.owner_id,
            user_id=actor_id,
        )
        return LegacySkillSetScope(
            owner_id=str(bot["owner_id"]),
            bot_id=scope.bot_id,
        )

    def update_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        name: str | None,
        description: str | None,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        item = self._repository.update_set(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            set_id=set_id,
            name=name,
            description=description,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=user_id,
            action="skill_set_update",
        )
        return item

    def delete_set(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> None:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        self._repository.delete_set(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=user_id,
            action="skill_set_delete",
        )

    def list_skills(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return self._repository.list_skills(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    def resolve_legacy_skill_id(
        self, *, bot_id: str, owner_id: str, actor_id: str, identifier: str
    ) -> str:
        """Resolve the published batch wire to a durable ``ac_skill.id``.

        The legacy ``POST /api/skillsets/{id}/skills`` endpoint accepted a
        database ID, name, or Git path.  For a market identifier not yet
        persisted it also materialised the Repo Skill before adding the
        membership.  Preserve that adapter-only behaviour here, then hand the
        stable identity to the normal atomic membership command.  Canonical
        requests never call this method and therefore never create assets from
        a name/path.
        """
        bot = self._legacy_bot(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )
        try:
            return self._repository.resolve_legacy_skill_id(
                bot_id=bot_id, identifier=identifier
            )
        except SkillSetControlPlaneNotFoundError:
            pass

        owner_id = str(bot["owner_id"])
        legacy = self._legacy_factory.create(
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
            engine_type=self._engine(bot),
            entity_type=bot.get("entity_type") or "staff",
        )
        try:
            return legacy.resolve_or_create_legacy_market_skill(
                identifier=identifier, owner_id=owner_id, bot_id=bot_id
            )
        except ValueError as exc:
            raise SkillSetControlPlaneNotFoundError() from exc

    async def add_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_id: str,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        runtime_required = self._set_is_active(
            bot=bot, bot_id=bot_id, set_id=set_id
        )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_add_skill",
            runtime_required=runtime_required,
            mutation=lambda: self._repository.add_skill(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                skill_id=skill_id,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def remove_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_id: str,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        runtime_required = self._set_is_active(
            bot=bot, bot_id=bot_id, set_id=set_id
        )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_remove_skill",
            runtime_required=runtime_required,
            mutation=lambda: self._repository.remove_skill(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                skill_id=skill_id,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    def list_mcps(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return self._repository.list_mcps(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    def mcp_permissions(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict]:
        mcps = self.list_mcps(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
            set_id=set_id,
        )
        return [
            {
                "server_code": item["server_code"],
                **self._mcp_center.check_mcp_permission_detail(
                    user_id, str(item["server_code"])
                ),
            }
            for item in mcps
        ]

    def request_mcp_permissions(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        reason: str,
    ) -> list[dict]:
        mcps = self.list_mcps(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
            set_id=set_id,
        )
        return [
            {
                "server_code": item["server_code"],
                **self._mcp_auth.apply_permission(
                    staff_no=user_id,
                    service_code=str(item["server_code"]),
                    tool_list=[],
                    is_public=self._is_public_mcp(str(item["server_code"])),
                    reason=reason,
                ),
            }
            for item in mcps
        ]

    async def add_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        server_code: str,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        self._require_mcp_permission(actor_id=user_id, server_code=server_code)
        runtime_required = self._set_is_active(
            bot=bot, bot_id=bot_id, set_id=set_id
        )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_add_mcp",
            runtime_required=runtime_required,
            mutation=lambda: self._repository.add_mcp(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                server_code=server_code,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def remove_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        server_code: str,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        runtime_required = self._set_is_active(
            bot=bot, bot_id=bot_id, set_id=set_id
        )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_remove_mcp",
            runtime_required=runtime_required,
            mutation=lambda: self._repository.remove_mcp(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                server_code=server_code,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def activate_mcp_direct(
        self, *, bot_id: str, owner_id: str, user_id: str, server_code: str
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        self._require_mcp_permission(actor_id=user_id, server_code=server_code)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="mcp_direct_activate",
            mutation=lambda: self._repository.activate_mcp_direct(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                server_code=server_code,
                engine_type=self._engine(bot),
            ),
        )

    async def deactivate_mcp_direct(
        self, *, bot_id: str, owner_id: str, user_id: str, server_code: str
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="mcp_direct_deactivate",
            mutation=lambda: self._repository.deactivate_mcp_direct(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                server_code=server_code,
                engine_type=self._engine(bot),
            ),
        )

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, user_id: str
    ) -> set[str]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return self._repository.list_installed_mcps(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            engine_type=self._engine(bot),
        )

    async def activate(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        target = self._repository.get_set(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )
        if not target["is_default"]:
            self._require_set_mcp_permissions(
                bot_id=bot_id, actor_id=user_id, set_id=set_id, bot=bot
            )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_activate",
            mutation=lambda: self._repository.set_active(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                active=True,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def deactivate(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_deactivate",
            mutation=lambda: self._repository.set_active(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                active=False,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def sync(
        self, *, bot_id: str, owner_id: str, actor_id: str, set_id: str
    ) -> dict:
        """Compatibility command that adds this Set without disabling peers."""
        bot = self._legacy_bot(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action="skill_set_sync",
            mutation=lambda: self._repository.set_active(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                active=True,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    def resources(self, *, bot_id: str, owner_id: str, user_id: str) -> list[dict]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        owner_id = str(bot["owner_id"])
        # Resource reads preserve the legacy graceful degradation: a passport-provider
        # outage hides Default CLI entries but must not hide SkillSet/MCP data.
        try:
            default_clis = self._passport.query_passport_clis(
                bot_id, str(bot.get("entity_id") or owner_id)
            )
        except Exception:
            default_clis = []
        items = self._repository.list_sets(
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )
        # Default MCPs are not stored as ordinary ac_skill_set_mcp rows.  The
        # legacy service combines engine/template defaults, explicit rows, and
        # ac_default_skillset_mcp_exclusion.  Keep that proven projection for
        # the published BFF resource response; ordinary Sets stay canonical.
        legacy = (
            self._legacy_factory.create(
                entity_id=str(bot.get("entity_id") or owner_id),
                bot_id=bot_id,
                engine_type=self._engine(bot),
                entity_type=bot.get("entity_type") or "staff",
            )
            if any(item["is_default"] for item in items)
            else None
        )
        resources: list[dict] = []
        for item in items:
            if item["is_default"]:
                assert legacy is not None
                mcps = legacy.get_set_mcp_servers(
                    str(item["id"]),
                    user_id=owner_id,
                    bot_id=bot_id,
                    engine_type=self._engine(bot),
                )
            else:
                mcps = self._repository.list_mcps(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    set_id=item["id"],
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                )
            resources.append(
                {
                    **item,
                    "mcps": mcps,
                    "clis": default_clis if item["is_default"] else [],
                }
            )
        return resources

    async def _mutate(
        self,
        *,
        bot: dict,
        bot_id: str,
        actor_id: str,
        action: str,
        mutation,
        runtime_required: bool = True,
    ) -> dict:
        """Apply one desired-state mutation and synchronously reconcile runtime."""
        if runtime_required:
            self._require_mutable_bot(bot)
        previous_mappings: Sequence[PoolSkillMapping] = ()
        if runtime_required:
            previous_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
            )
        mutation_result = mutation()
        # An inactive-set membership change has no runtime projection
        # to apply.  Reconcile only becomes a required side effect
        # when that membership is active (or for all lifecycle/sync
        # commands), preserving the legacy inactive draft contract.
        if not runtime_required:
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
                previous_mappings=previous_mappings,
            )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=actor_id,
            action=action,
        )
        return result

    async def _reconcile(
        self,
        *,
        bot: dict,
        bot_id: str,
        actor_id: str,
        mutation: SkillSetMutation,
        previous_mappings: Sequence[PoolSkillMapping],
    ) -> dict:
        owner_id = str(bot["owner_id"])
        current_mappings: Sequence[PoolSkillMapping] = ()
        try:
            current_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
            )
            await self._reconcile_runtime(
                bot_id=bot_id,
                owner_id=owner_id,
                retired_mappings=retired_logical_skill_mappings(
                    list(previous_mappings),
                    list(current_mappings),
                ),
            )
        except Exception as exc:
            self._repository.restore_desired_state(
                bot_id=bot_id,
                owner_id=owner_id,
                state=mutation.previous_state,
                engine_type=self._engine(bot),
            )
            try:
                await self._reconcile_runtime(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    retired_mappings=retired_logical_skill_mappings(
                        list(current_mappings),
                        list(previous_mappings),
                    ),
                )
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

    def _require_mcp_permission(self, *, actor_id: str, server_code: str) -> None:
        result = self._mcp_center.check_mcp_permission_detail(actor_id, server_code)
        # The catalogue endpoint deliberately reports fail-open during an
        # upstream outage.  Desired-state writes cannot use that advisory
        # answer: an empty access level is its documented outage sentinel, so
        # installing then must fail closed.
        if not bool(result.get("has_permission")) or not result.get("access_level"):
            raise McpPermissionDeniedError()

    def _is_public_mcp(self, server_code: str) -> bool:
        detail = self._mcp_center.get_mcp_detail(server_code)
        return bool(detail and detail.get("accessLevel") == "PUBLIC")

    def _require_set_mcp_permissions(
        self, *, bot_id: str, actor_id: str, set_id: str, bot: dict
    ) -> None:
        for mcp in self._repository.list_mcps(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        ):
            self._require_mcp_permission(
                actor_id=actor_id, server_code=str(mcp["server_code"])
            )

    @staticmethod
    def _engine(bot: dict) -> str:
        # Deliberately not ``bot_engine_scope.bot_engine_type``. That helper
        # maps a missing engine to ``None`` — "do not filter by engine" — which
        # is right for a listing and wrong here: these are writes, and an
        # unfiltered scope would reach every Set the Bot has across every
        # engine. Widening it is its own change, with its own tests.
        return str(bot["active_engine"])

    @classmethod
    def _default_engine_types(cls, bot: dict) -> tuple[str, ...]:
        """Select Default rows using runtime layout before persisted fallback."""
        return tuple(
            dict.fromkeys((runtime_layout_engine_for_bot(bot), cls._engine(bot)))
        )

    def _set_is_active(self, *, bot: dict, bot_id: str, set_id: str) -> bool:
        """Whether a membership edit must synchronously reconcile runtime."""
        item = self._repository.get_set(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )
        return bool(item.get("is_active"))

    @staticmethod
    def _require_mutable_bot(bot: dict) -> None:
        if not is_bot_ready(bot):
            raise LocalSkillNotReadyError()

    async def _reconcile_runtime(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        await self._runtime.reconcile(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
        )

"""Canonical SkillSet service: ACL, UoW command, one runtime reconcile."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from injector import inject

from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    McpPermissionDeniedError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
    SkillSetAccessDeniedError,
)
from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_server_codes,
)
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetCompatibilityFactoryProtocol,
    LegacySkillSetScope,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.services._mutation_flow import (
    MutationProjectionFlow,
    skill_claim_scope,
    skill_release_scope,
)
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)
from agentclaw.community.plugin_api.passport import PassportPlugin


class SkillSetManagementService:
    @inject
    def __init__(
        self,
        repository: CapabilityDesiredStateRepositoryProtocol,
        bot_repo: BotRepository,
        runtime: BotRuntimeProjectorProtocol,
        legacy_factory: LegacySkillSetCompatibilityFactoryProtocol,
        passport: PassportPlugin,
        authorization: BotCapabilityAuthorizationHookProtocol,
        audit_log_repo: BotCollabLogRepositoryProtocol,
        mcp_center: MCPCenterPlugin,
        mcp_auth: MCPAuthPlugin,
        ext_info_provider: Callable[[str], Mapping[str, Any] | None],
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo
        self._runtime = runtime
        self._flow = MutationProjectionFlow(repository=repository, runtime=runtime)
        self._legacy_factory = legacy_factory
        self._passport = passport
        self._authorization = authorization
        self._audit_log_repo = audit_log_repo
        self._mcp_center = mcp_center
        self._mcp_auth = mcp_auth
        self._ext_info_provider = ext_info_provider

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
        # only one. See ``skill_query_service._resolve_local`` for the same
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
        # A newly created empty SkillSet is active by default, matching the
        # legacy create semantics. With no members it does not change the
        # effective capability projection, so it does not enter the Pool edit
        # boundary or require a runtime action.
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
        # Scope comes from the mutation result, not from up here: a Skill can
        # carry ``mcp_dependencies``, and those codes join the Bot's MCP set
        # along with the Skill. The repository reads them under the row lock it
        # already holds, so the scope names what was actually installed rather
        # than what a second, unlocked query happened to see.
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if target["is_default"]:
            # Adding to the Default Set is the restored opt-out's other half:
            # it can only remove an existing exclusion. The membership itself
            # stays immutable.
            self._require_excluded_default_skill(
                owner_id=str(bot["owner_id"]), bot_id=bot_id,
                set_id=str(target["id"]), skill_id=skill_id,
            )
            return await self._mutate(
                bot=bot,
                bot_id=bot_id,
                actor_id=user_id,
                action="default_set_unexclude_skill",
                scope_from_result=skill_claim_scope,
                mutation=lambda: self._repository.unexclude_default_skill(
                    bot_id=bot_id,
                    owner_id=str(bot["owner_id"]),
                    set_id=set_id,
                    skill_id=skill_id,
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                ),
            )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_add_skill",
            runtime_required=bool(target.get("is_active")),
            scope_from_result=skill_claim_scope,
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
        # Scope from the result, mirroring ``add_skill``.
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if target["is_default"]:
            # A Default Set is always active and its membership immutable:
            # removing a member is the per-Bot exclusion (spec E.11).
            return await self._mutate(
                bot=bot,
                bot_id=bot_id,
                actor_id=user_id,
                action="default_set_exclude_skill",
                scope_from_result=skill_release_scope,
                mutation=lambda: self._repository.exclude_default_skill(
                    bot_id=bot_id,
                    owner_id=str(bot["owner_id"]),
                    set_id=set_id,
                    skill_id=skill_id,
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                ),
            )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_remove_skill",
            runtime_required=bool(target.get("is_active")),
            scope_from_result=skill_release_scope,
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

    def list_mcp_permissions(
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
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if target["is_default"]:
            # The MCP twin of add_skill's opt-out half: only an existing
            # exclusion can be removed; the membership stays immutable.
            codes = self._repository.excluded_default_mcp_codes(
                bot_id=bot_id, owner_id=str(bot["owner_id"]),
                set_id=str(target["id"]),
            )
            if server_code not in codes:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            return await self._mutate(
                bot=bot,
                bot_id=bot_id,
                actor_id=user_id,
                action="default_set_unexclude_mcp",
                scope=ProjectionScope(
                    mcp=True, claimed_mcp=frozenset({server_code})
                ),
                mutation=lambda: self._repository.unexclude_default_mcp(
                    bot_id=bot_id,
                    owner_id=str(bot["owner_id"]),
                    set_id=set_id,
                    server_code=server_code,
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                ),
            )
        catalog = self._mcp_catalog_entry(server_code)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_add_mcp",
            runtime_required=bool(target.get("is_active")),
            scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({server_code})),
            mutation=lambda: self._repository.add_mcp(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                server_code=server_code,
                name=catalog["name"],
                description=catalog["description"],
                icon=catalog["icon"],
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
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if target["is_default"]:
            return await self._mutate(
                bot=bot,
                bot_id=bot_id,
                actor_id=user_id,
                action="default_set_exclude_mcp",
                scope=ProjectionScope(
                    mcp=True, released_mcp=frozenset({server_code})
                ),
                mutation=lambda: self._repository.exclude_default_mcp(
                    bot_id=bot_id,
                    owner_id=str(bot["owner_id"]),
                    set_id=set_id,
                    server_code=server_code,
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                    platform_default_codes=self._platform_default_mcp_codes(
                        bot, bot_id
                    ),
                ),
            )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_remove_mcp",
            runtime_required=bool(target.get("is_active")),
            scope=ProjectionScope(mcp=True, released_mcp=frozenset({server_code})),
            mutation=lambda: self._repository.remove_mcp(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                server_code=server_code,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
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
            scope_from_result=lambda result: ProjectionScope(
                skills=True, mcp=True, claimed_mcp=result.mcp_codes
            ),
            mutation=lambda: self._repository.set_skill_set_active(
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
            scope_from_result=lambda result: ProjectionScope(
                skills=True, mcp=True, released_mcp=result.mcp_codes
            ),
            mutation=lambda: self._repository.set_skill_set_active(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                active=False,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def legacy_activate(
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
            scope_from_result=lambda result: ProjectionScope(
                skills=True, mcp=True, claimed_mcp=result.mcp_codes
            ),
            mutation=lambda: self._repository.set_skill_set_active(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                set_id=set_id,
                active=True,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    def list_resources(self, *, bot_id: str, owner_id: str, user_id: str) -> list[dict]:
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
        scope: ProjectionScope | None = None,
        scope_from_result: Callable[[DesiredStateMutation], ProjectionScope] | None = None,
    ) -> dict:
        """Apply one desired-state mutation and synchronously reconcile runtime.

        ``runtime_required=False`` preserves the legacy inactive draft
        contract: an inactive-set membership change has no runtime projection
        to apply. The mutate-project-compensate orchestration itself is the
        shared :class:`MutationProjectionFlow`.

        Both scope arguments stay optional *here* only because each command
        supplies whichever one it can — every one of the eleven call sites
        below passes exactly one. The flow enforces that; neither is a
        "forgot to say" default.
        """
        result = await self._flow.apply(
            bot=bot,
            bot_id=bot_id,
            engine_type=self._engine(bot),
            mutation=mutation,
            runtime_required=runtime_required,
            scope=scope,
            scope_from_result=scope_from_result,
        )
        self._audit(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=actor_id,
            action=action,
        )
        return result

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

    def _mcp_catalog_entry(self, server_code: str) -> dict[str, Any]:
        """The catalogue metadata a new membership row carries.

        The row is what every read-side answer renders, so it holds the
        catalogue's own name/description/icon rather than the server code
        standing in for all three. Resolved before the mutation opens: a code
        the catalogue does not know is a 404 at the boundary, not a membership
        row persisted under a placeholder name.

        A known entry that simply carries no display name still installs — the
        server code is a usable label, and refusing there would reject an
        install over a cosmetic gap in someone else's catalogue.
        """
        detail = self._mcp_center.get_mcp_detail(server_code)
        if not detail:
            raise SkillSetControlPlaneNotFoundError("MCP server not found")
        return {
            "name": str(detail.get("name") or server_code),
            "description": detail.get("description"),
            "icon": detail.get("icon"),
        }

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

    def _platform_default_mcp_codes(self, bot: dict, bot_id: str) -> frozenset[str]:
        """The unmaterialized platform Default MCP policy (spec A.2).

        Resolved at write time with the same context the read-side union
        uses — engine, template, ext info. A provider failure propagates
        rather than degrading to base defaults: for a template-preset-only
        default MCP, a silently narrowed set would make the exclusion
        command mis-read the genuine member as a stray and no-op the
        removal as ``changed=False`` — a wrong persisted answer, where an
        error is merely a retry.
        """
        return frozenset(
            get_default_mcp_server_codes(
                self._engine(bot),
                bot.get("template_type"),
                ext_info=self._ext_info_provider(bot_id),
            )
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

    def _target_set(self, *, bot: dict, bot_id: str, set_id: str) -> dict:
        """The addressed Set's item: routes Default-Set opt-out vs membership
        edits, and decides whether the edit must reconcile runtime (an
        inactive ordinary Set is a draft; a Default is always active)."""
        return self._repository.get_set(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            set_id=set_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    def _require_excluded_default_skill(
        self, *, owner_id: str, bot_id: str, set_id: str, skill_id: str
    ) -> None:
        """Only an excluded member can be "added" to a Default Set."""
        excluded = self._repository.excluded_default_skill_ids(
            bot_id=bot_id, owner_id=owner_id, set_id=set_id
        )
        if not skill_id.isdecimal() or int(skill_id) not in excluded:
            raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")

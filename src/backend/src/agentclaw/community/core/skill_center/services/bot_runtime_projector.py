"""Deep module for applying one complete Bot capability projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from injector import inject

from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
    passport_mcp_items_from_codes,
    resolve_mcp_identity_modes,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.mcp.errors import McpIdentityUnresolvedError
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    ResolvedCapabilityPlan,
)
from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
    EngineRuntimeProjectionRegistry,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeProjection,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import McpScopeItem, PassportPlugin


logger = get_logger()


class BotRuntimeProjector:
    """Resolve and apply Skill, MCP, and CLI state through one boundary.

    Callers own command compensation. This module owns desired-state loading,
    runtime contract selection, full mapping publication, MCP delivery, and the
    overwrite-style Passport MCP/CLI manifest.

    Resolving and applying are separated by the ``ProjectionScope``: the plan
    is always built whole — it is read-only, and every pre-flight failure in it
    must happen before anything is written — while the scope decides which
    halves are *written*. Keeping the reads unconditional is what lets a
    projection abort cleanly, with nothing half-applied for a compensation to
    unpick.
    """

    @inject
    def __init__(
        self,
        factory: SkillSetServiceFactory,
        bot_repo: BotRepository,
        repository: CapabilityDesiredStateRepositoryProtocol,
        reader: BotCapabilityStateReaderProtocol,
        registry: EngineRuntimeProjectionRegistry,
        passport: PassportPlugin,
        caller_identity_repo: CallerIdentityRepositoryProtocol,
    ) -> None:
        self._factory = factory
        self._bot_repo = bot_repo
        self._repository = repository
        self._reader = reader
        # Which runtime contract a Bot's engine obeys. The Skills Pool
        # collaborators moved with the per-domain implementation that is their
        # only user, so this module no longer holds them.
        self._registry = registry
        self._passport = passport
        self._caller_identity_repo = caller_identity_repo

    async def snapshot_skill_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> tuple[PoolSkillMapping, ...]:
        """Resolve the current desired Skills without changing runtime state.

        Callers use this before a mutating reconcile so a later desired-state
        rollback can retire mappings that were already published.
        """
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        engine = str(bot.get("active_engine") or "openclaw")
        skill_assets = list(
            self._reader.active_skill_assets(
                bot_id=bot_id, owner_id=owner_id, bot=bot
            )
        )
        if engine == "teclaw" and any(
            asset.git_path.startswith("center://") for asset in skill_assets
        ):
            raise SkillSetRuntimeReconcileError()
        return tuple(build_logical_skill_mappings(skill_assets))

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> None:
        plan = self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
        )
        # How a runtime consumes a projection — how many calls converging takes,
        # and whether the scope's halves mean anything to it at all — is the
        # engine's fact, so the engine's own implementation answers it. Nothing
        # below this line tests which engine this is.
        await self._registry.for_engine(plan.engine).apply(
            plan=plan,
            scope=scope,
            retired_mappings=retired_mappings,
        )
        # The Passport is not a runtime and not engine-shaped: it is the
        # platform's authorization record, the same for every engine. Its
        # trigger is unchanged.
        if scope.mcp:
            self._apply_passport_projection(plan=plan)
        else:
            logger.info(
                "[BotRuntimeProjector] Passport update skipped, scope declares "
                "no MCP change: bot_id=%s, engine=%s",
                plan.bot_id, plan.engine,
            )

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> None:
        """Rebuild MCP/CLI when a cutover task exclusively owns Skill mappings.

        The same four steps as ``project``, with no retirements: declaring the
        Skill half here would fight the cutover that owns it, and a scope whose
        ``skills`` flag is false already says exactly that — so no separate
        entry point into the engine's projection is needed.
        """
        plan = self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
        )
        await self._registry.for_engine(plan.engine).apply(
            plan=plan,
            scope=scope,
        )
        if scope.mcp:
            self._apply_passport_projection(plan=plan)

    async def project_for_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> None:
        """Safely remove legacy state without granting new runtime writes.

        Historical engines use their existing full legacy synchronizer for
        Local/Repo removal.  Center requires the Pool v3 contract and is never
        permitted on this compatibility path.

        The Skill half is written here rather than through the engine's
        projection precisely because it must *not* take the Pool path this
        engine would normally choose — that is what "compatibility path"
        means. Only the MCP half is delegated. Note this method has no
        production caller today: it is reachable only through the Service API
        protocol, so treat any behaviour change here as unexercised.
        """
        plan = self._resolve_cleanup_plan(bot_id=bot_id, owner_id=owner_id)
        if any(
            mapping.corpus == "center"
            for mapping in plan.projection.skill_mappings
        ):
            raise SkillSetRuntimeReconcileError()
        if not plan.service.sync_runtime(
            desired_skills=self._desired_skills(plan.projection)
        ):
            raise SkillSetRuntimeReconcileError()
        # ``skills=False`` regardless of what the caller declared: the Skill
        # half was just written by the legacy synchronizer above, and letting
        # the engine's projection write it again would either duplicate that
        # or route it onto the Pool path this compatibility path exists to
        # avoid. Only the MCP half is the engine's here.
        await self._registry.for_engine(plan.engine).apply(
            plan=plan,
            scope=replace(scope, skills=False),
        )
        if scope.mcp:
            self._apply_passport_projection(plan=plan)

    def _resolve_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> ResolvedCapabilityPlan:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        return self._build_plan(
            bot=bot,
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
        )

    def _resolve_cleanup_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> ResolvedCapabilityPlan:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        return self._build_plan(bot=bot, bot_id=bot_id, owner_id=owner_id)

    def _resolve_mcp_identity_modes(
        self, *, bot: dict, bot_id: str, engine: str
    ) -> Mapping[str, object]:
        """Read each MCP's execution identity, or refuse to project.

        Part of plan resolution rather than delivery: the Passport manifest is
        overwrite-style, so a projection that cannot establish identity must
        not have written anything yet when it gives up.

        The read itself — and its refusal to default to Owner — lives in
        ``resolve_mcp_identity_modes``, shared with the two non-projector
        callers that assemble the same overwrite-style scope. Only the
        translation to this module's error type is local.
        """
        try:
            return resolve_mcp_identity_modes(
                self._caller_identity_repo,
                bot_pk=bot.get("id"),
                engine_type=engine,
                bot_id=bot_id,
            )
        except McpIdentityUnresolvedError as exc:
            raise SkillSetRuntimeReconcileError() from exc

    def _build_plan(
        self,
        *,
        bot: dict,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> ResolvedCapabilityPlan:
        engine = str(bot.get("active_engine") or "openclaw")
        service = self._factory.create(
            user_id=owner_id,
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
            engine_type=engine,
            entity_type=bot.get("entity_type") or "staff",
        )
        # The reader flushes before answering, so the plan is always built
        # over Installation that agrees with Set configuration — the lazy
        # flush every read runs, not a projector-only repair.
        skill_assets = tuple(
            self._reader.active_skill_assets(
                bot_id=bot_id, owner_id=owner_id, bot=bot
            )
        )
        if engine == "teclaw" and (
            any(asset.git_path.startswith("center://") for asset in skill_assets)
            or any(mapping.corpus == "center" for mapping in retired_mappings)
        ):
            # Reject before querying or writing any external MCP, Passport, or
            # runtime boundary. Teclaw Center delivery belongs to Phase 2.
            raise SkillSetRuntimeReconcileError()
        # Resolved here, with the other pre-flight checks, because it can fail:
        # doing it at the Passport call would abort after the device allow-list
        # was already written, and the compensating projection would then hit
        # the same failure and be unable to counter-project.
        identity_modes = self._resolve_mcp_identity_modes(
            bot=bot, bot_id=bot_id, engine=engine
        )
        # The legacy SkillSet service remains the authority for effective
        # System Defaults during Phase 1.  It resolves template presets and
        # applies ac_default_skillset_mcp_exclusion; rebuilding defaults from
        # static constants here would silently resurrect user exclusions.
        try:
            effective_mcp_entries = service.collect_bot_active_mcps(
                entity_id=str(bot.get("entity_id") or owner_id),
                bot_id=bot_id,
                user_id=owner_id,
                entity_type=bot.get("entity_type") or "staff",
                engine_type=engine,
                strict_policy_context=True,
            )
        except Exception as exc:
            # A failed policy-context lookup is not an empty Default policy.
            # Fail before any overwrite-style MCP or Passport projection so a
            # transient dependency outage cannot remove template-only MCPs.
            raise SkillSetRuntimeReconcileError() from exc
        effective_default_mcp_codes = frozenset(
            str(item.get("server_code") or item.get("serverCode") or "").strip()
            for item in effective_mcp_entries
            if item.get("server_code") or item.get("serverCode")
        )
        try:
            # CLI removal is currently persisted by the authorization service. Its
            # current scope is therefore the only effective Default CLI fact;
            # merging static engine defaults here would undo that removal.
            effective_cli_items = self._passport.query_passport_clis(
                bot_id, owner_id
            )
        except Exception as exc:
            raise SkillSetRuntimeReconcileError() from exc
        projection = RuntimeProjectionResolver().resolve(
            RuntimeDesiredState(
                skills=skill_assets,
                installed_mcp_server_codes=frozenset(
                    self._repository.list_installed_mcps(
                        bot_id=bot_id, owner_id=owner_id
                    )
                ),
                system_default_mcp_server_codes=effective_default_mcp_codes,
                system_default_cli_commands=tuple(
                    str(item["cli_code"])
                    for item in effective_cli_items
                    if item.get("cli_code")
                ),
            )
        )

        return ResolvedCapabilityPlan(
            bot_id=bot_id,
            owner_id=owner_id,
            service=service,
            bot=bot,
            engine=engine,
            projection=projection,
            effective_cli_items=effective_cli_items,
            identity_modes=identity_modes,
        )

    def _passport_mcp_items(
        self,
        *,
        identity_modes: Mapping[str, object],
        bot_id: str,
        engine: str,
        codes: list[str],
    ) -> list[McpScopeItem]:
        """Colour the declared MCP codes with their execution identity.

        ``updatePassport`` replaces each resource list wholesale, and the
        Passport port fills a missing ``identity_mode`` with ``"owner"``
        rather than leaving the field off the wire. Sending codes without
        identity therefore does not "leave identity alone" — it asserts Owner
        for every MCP and silently discards Caller configuration that
        ``update_mcp_identity_to_agent_principal`` wrote through the same
        field.

        The codes decide the scope; ``identity_modes`` — resolved during plan
        resolution, see ``_resolve_mcp_identity_modes`` — only colours them,
        so a stale row for an MCP this Bot no longer holds cannot re-grant it.
        """
        items = passport_mcp_items_from_codes(codes, identity_modes=identity_modes)
        caller_count = sum(
            1 for item in items if item.get("identity_mode") == "caller"
        )
        logger.info(
            "[BotRuntimeProjector] Passport MCP scope resolved: bot_id=%s, "
            "engine=%s, mcps=%s, caller=%s, owner=%s",
            bot_id,
            engine,
            len(items),
            caller_count,
            len(items) - caller_count,
        )
        return items

    def _apply_passport_projection(
        self,
        *,
        plan: ResolvedCapabilityPlan,
    ) -> None:
        """Declare the Bot's complete MCP/CLI scope to the authorization service.

        Not a runtime write, and deliberately not part of
        ``EngineRuntimeProjection``: the Passport is the platform's
        authorization record — which MCPs this Bot may reach and under whose
        identity — and it is the same record for every engine. A whole-artifact
        engine needs it no less than a per-domain one: its container is issued
        a passport-service token as an egress rule (see
        ``TeclawPublishTaskHandler``), so an un-updated manifest would leave a
        valid token pointed at a scope that no longer matches the
        configuration the container was given.

        Kept on the projector in one copy, for the reason
        ``_passport_mcp_items`` exists: this scope is overwrite-style, so a
        second copy that drifted would silently reassert
        ``identity_mode: "owner"`` for every MCP.
        """
        try:
            passport_codes = filter_passport_mcp_codes(
                plan.projection.mcp_server_codes
            )
            # Mandatory, not an optimisation — see ``_passport_mcp_items``.
            mcp_items = self._passport_mcp_items(
                identity_modes=plan.identity_modes,
                bot_id=plan.bot_id,
                engine=plan.engine,
                codes=passport_codes,
            )
            self._passport.update_passport(
                bot_id=plan.bot_id,
                user_id=plan.owner_id,
                engine_type=plan.engine,
                resource_scope={
                    # Derived from the items rather than passed separately:
                    # ``unpack_resource_scope`` ignores ``mcp_codes`` once
                    # ``mcp_items`` is present, so two independent lists could
                    # silently diverge and only one would reach the passport service.
                    "mcp_codes": [item["mcp_code"] for item in mcp_items],
                    "mcp_items": mcp_items,
                    "cli_items": plan.effective_cli_items,
                },
            )
        except Exception as exc:
            raise SkillSetRuntimeReconcileError() from exc

    @staticmethod
    def _desired_skills(projection: RuntimeProjection) -> list[dict[str, str | None]]:
        return [
            {
                "id": str(asset.skill_id),
                "name": asset.name,
                "git_path": asset.git_path,
                "skill_uuid": asset.skill_uuid,
                "sc_version_number": asset.sc_version_number,
            }
            for asset in projection.skill_assets
        ]



__all__ = [
    "BotRuntimeProjector",
]

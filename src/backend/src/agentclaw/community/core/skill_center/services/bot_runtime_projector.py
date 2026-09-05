"""Deep module for applying one complete Bot capability projection."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from injector import inject

from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
    passport_mcp_items_from_codes,
    resolve_mcp_identity_modes,
)
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    build_passport_resource_scope,
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
    ResolvedSkillPlan,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
    EngineRuntimeProjectionRegistry,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import McpScopeItem, PassportPlugin
from agentclaw.community.core.skill_center.bot_runtime_projector_protocol import (
    BotRuntimeProjectorProtocol,
)


logger = get_logger()


class BotRuntimeProjector(BotRuntimeProjectorProtocol):
    """Resolve and apply Skill, MCP, and CLI state through one boundary.

    Callers own command compensation. This module owns desired-state loading,
    runtime contract selection, full mapping publication, MCP delivery, and the
    overwrite-style Passport MCP/CLI manifest.

    Resolving and applying are separated by the ``ProjectionScope``. The
    Skill state is always resolved into ``ResolvedSkillPlan``, including its
    Installation flush and engine validation. MCP/CLI/Passport pre-flight is
    added only when the scope can write that half, producing the stricter
    ``ResolvedCapabilityPlan``. A whole-artifact runtime independently
    composes its retained MCP state from DB during its one delivery; CLI
    authorization remains in Passport.
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
            self._reader.active_skill_assets(bot_id=bot_id, owner_id=owner_id, bot=bot)
        )
        # The engine refuses what its runtime has no contract for. Asked here
        # rather than tested here, so a caller of this module never has to
        # know which engines those are.
        self._registry.for_engine(engine).validate_plan(skill_assets=skill_assets)
        return (
            RuntimeProjectionResolver()
            .resolve_skills(tuple(skill_assets))
            .skill_mappings
        )

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        plan = self.resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
            scope=scope,
        )
        return await self.apply_plan(
            plan=plan,
            retired_mappings=retired_mappings,
            scope=scope,
        )

    def resolve_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> ResolvedSkillPlan:
        """Build the sole Reader-backed plan consumed by one projection."""
        return self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
            scope=scope,
        )

    async def apply_plan(
        self,
        *,
        plan: ResolvedSkillPlan,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        """Apply one already-resolved plan without reading desired state again."""
        self._registry.for_engine(plan.engine).validate_plan(
            skill_assets=plan.projection.skill_assets,
            retired_mappings=retired_mappings,
        )
        if scope.mcp and not isinstance(plan, ResolvedCapabilityPlan):
            # Contract guard before any engine write: an MCP scope must never
            # cross the seam with a Skill-only plan.
            raise SkillSetRuntimeReconcileError()
        # How a runtime consumes a projection — how many calls converging takes,
        # and whether the scope's halves mean anything to it at all — is the
        # engine's fact, so the engine's own implementation answers it. Nothing
        # below this line tests which engine this is.
        result = await self._registry.for_engine(plan.engine).apply(
            plan=plan,
            scope=scope,
            retired_mappings=retired_mappings,
        )
        # The Passport is not a runtime and not engine-shaped: it is the
        # platform's authorization record, the same for every engine. Its
        # trigger is unchanged.
        if scope.mcp:
            assert isinstance(plan, ResolvedCapabilityPlan)
            try:
                self._apply_passport_projection(plan=plan)
            except Exception:
                logger.exception(
                    "[BotRuntimeProjector] Passport projection unavailable "
                    "bot_id=%s engine=%s",
                    plan.bot_id,
                    plan.engine,
                )
                return RuntimeProjectionResult.combine(
                    result,
                    RuntimeProjectionResult.pending(
                        code="PASSPORT_RUNTIME_UNAVAILABLE",
                        reason="MCP 授权配置尚未完成同步",
                    ),
                )
        else:
            logger.info(
                "[BotRuntimeProjector] Passport update skipped, scope declares "
                "no MCP change: bot_id=%s, engine=%s",
                plan.bot_id,
                plan.engine,
            )
        return result

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        """Rebuild MCP/CLI when a cutover task exclusively owns Skill mappings.

        The same four steps as ``project``, with no retirements: declaring the
        Skill half here would fight the cutover that owns it, and a scope whose
        ``skills`` flag is false already says exactly that — so no separate
        entry point into the engine's projection is needed.
        """
        if not scope.mcp:
            raise ValueError("project_mcp_and_cli requires scope.mcp=True")
        plan = self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            scope=scope,
        )
        assert isinstance(plan, ResolvedCapabilityPlan)
        result = await self._registry.for_engine(plan.engine).apply(
            plan=plan,
            scope=scope,
        )
        try:
            self._apply_passport_projection(plan=plan)
        except Exception:
            logger.exception(
                "[BotRuntimeProjector] Passport projection unavailable "
                "bot_id=%s engine=%s",
                plan.bot_id,
                plan.engine,
            )
            return RuntimeProjectionResult.combine(
                result,
                RuntimeProjectionResult.pending(
                    code="PASSPORT_RUNTIME_UNAVAILABLE",
                    reason="MCP 授权配置尚未完成同步",
                ),
            )
        return result

    def _resolve_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> ResolvedSkillPlan:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        skill_plan_started_at = time.perf_counter()
        try:
            skill_plan = self._build_skill_plan(
                bot=bot,
                bot_id=bot_id,
                owner_id=owner_id,
                retired_mappings=retired_mappings,
            )
        except Exception:
            self._log_plan_timing(
                stage="build_skill_plan",
                bot_id=bot_id,
                engine=str(bot.get("active_engine") or "openclaw"),
                started_at=skill_plan_started_at,
                outcome="error",
            )
            raise
        self._log_plan_timing(
            stage="build_skill_plan",
            bot_id=bot_id,
            engine=skill_plan.engine,
            started_at=skill_plan_started_at,
            outcome="success",
            skill_count=len(skill_plan.projection.skill_assets),
        )
        if not scope.mcp:
            logger.info(
                "[BotRuntimeProjector] Non-Skill plan skipped for Skill-only "
                "scope: bot_id=%s, engine=%s",
                bot_id,
                skill_plan.engine,
            )
            return skill_plan
        capability_plan_started_at = time.perf_counter()
        try:
            capability_plan = self._build_capability_plan(skill_plan)
        except Exception:
            self._log_plan_timing(
                stage="build_mcp_plan",
                bot_id=bot_id,
                engine=skill_plan.engine,
                started_at=capability_plan_started_at,
                outcome="error",
            )
            raise
        self._log_plan_timing(
            stage="build_mcp_plan",
            bot_id=bot_id,
            engine=skill_plan.engine,
            started_at=capability_plan_started_at,
            outcome="success",
            mcp_count=len(capability_plan.projection.mcp_server_codes),
            cli_count=len(capability_plan.effective_cli_items),
        )
        return capability_plan

    @staticmethod
    def _log_plan_timing(
        *,
        stage: str,
        bot_id: str,
        engine: str,
        started_at: float,
        outcome: str,
        skill_count: int | None = None,
        mcp_count: int | None = None,
        cli_count: int | None = None,
    ) -> None:
        logger.info(
            "[BotRuntimeProjector] timing stage=%s bot_id=%s engine=%s "
            "duration_ms=%.3f outcome=%s skill_count=%s mcp_count=%s "
            "cli_count=%s",
            stage,
            bot_id,
            engine,
            (time.perf_counter() - started_at) * 1000,
            outcome,
            skill_count,
            mcp_count,
            cli_count,
        )

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

    def _build_skill_plan(
        self,
        *,
        bot: dict,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> ResolvedSkillPlan:
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
            self._reader.active_skill_assets(bot_id=bot_id, owner_id=owner_id, bot=bot)
        )
        # Reject before querying or writing any external MCP, Passport, or
        # runtime boundary. What an engine's runtime cannot carry is the
        # engine's own statement — see ``EngineRuntimeProjection.validate_plan``.
        self._registry.for_engine(engine).validate_plan(
            skill_assets=skill_assets,
            retired_mappings=retired_mappings,
        )
        return ResolvedSkillPlan(
            bot_id=bot_id,
            owner_id=owner_id,
            service=service,
            bot=bot,
            engine=engine,
            projection=RuntimeProjectionResolver().resolve_skills(skill_assets),
        )

    def _build_capability_plan(
        self, skill_plan: ResolvedSkillPlan
    ) -> ResolvedCapabilityPlan:
        bot = skill_plan.bot
        bot_id = skill_plan.bot_id
        owner_id = skill_plan.owner_id
        engine = skill_plan.engine
        service = skill_plan.service
        # Resolved here, with the other pre-flight checks, because it can fail:
        # doing it at the Passport call would abort after the device allow-list
        # was already written, and compensation would hit the same failure.
        started_at = time.perf_counter()
        identity_modes = self._resolve_mcp_identity_modes(
            bot=bot, bot_id=bot_id, engine=engine
        )
        self._log_plan_timing(
            stage="resolve_mcp_identity_modes", bot_id=bot_id, engine=engine,
            started_at=started_at, outcome="success", mcp_count=len(identity_modes),
        )
        # The legacy SkillSet service remains the authority for effective
        # System Defaults during Phase 1. It resolves template presets and
        # applies ac_default_skillset_mcp_exclusion.
        try:
            started_at = time.perf_counter()
            effective_mcp_entries = service.collect_bot_active_mcps(
                entity_id=str(bot.get("entity_id") or owner_id),
                bot_id=bot_id,
                user_id=owner_id,
                entity_type=bot.get("entity_type") or "staff",
                engine_type=engine,
                strict_policy_context=True,
            )
        except Exception as exc:
            self._log_plan_timing(
                stage="collect_effective_mcps", bot_id=bot_id, engine=engine,
                started_at=started_at, outcome="error",
            )
            raise SkillSetRuntimeReconcileError() from exc
        self._log_plan_timing(
            stage="collect_effective_mcps", bot_id=bot_id, engine=engine,
            started_at=started_at, outcome="success", mcp_count=len(effective_mcp_entries),
        )
        effective_default_mcp_codes = frozenset(
            str(item.get("server_code") or item.get("serverCode") or "").strip()
            for item in effective_mcp_entries
            if item.get("server_code") or item.get("serverCode")
        )
        try:
            started_at = time.perf_counter()
            # Passport is the authority for the effective Default CLI scope.
            effective_cli_items = self._passport.query_passport_clis(bot_id, owner_id)
        except Exception as exc:
            self._log_plan_timing(
                stage="query_passport_clis", bot_id=bot_id, engine=engine,
                started_at=started_at, outcome="error",
            )
            raise SkillSetRuntimeReconcileError() from exc
        self._log_plan_timing(
            stage="query_passport_clis", bot_id=bot_id, engine=engine,
            started_at=started_at, outcome="success", cli_count=len(effective_cli_items),
        )
        started_at = time.perf_counter()
        installed_mcp_codes = frozenset(
            self._repository.list_installed_mcps(bot_id=bot_id, owner_id=owner_id)
        )
        self._log_plan_timing(
            stage="read_installed_mcps", bot_id=bot_id, engine=engine,
            started_at=started_at, outcome="success", mcp_count=len(installed_mcp_codes),
        )
        started_at = time.perf_counter()
        projection = RuntimeProjectionResolver().resolve(
            RuntimeDesiredState(
                skills=skill_plan.projection.skill_assets,
                installed_mcp_server_codes=installed_mcp_codes,
                system_default_mcp_server_codes=effective_default_mcp_codes,
                system_default_cli_commands=tuple(
                    str(item["cli_code"])
                    for item in effective_cli_items
                    if item.get("cli_code")
                ),
            )
        )
        self._log_plan_timing(
            stage="resolve_effective_capabilities", bot_id=bot_id, engine=engine,
            started_at=started_at, outcome="success", mcp_count=len(projection.mcp_server_codes),
        )

        return ResolvedCapabilityPlan(
            bot_id=bot_id,
            owner_id=owner_id,
            service=service,
            bot=bot,
            engine=engine,
            projection=projection,
            effective_cli_items=effective_cli_items,
            effective_mcp_entries=effective_mcp_entries,
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
        caller_count = sum(1 for item in items if item.get("identity_mode") == "caller")
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
        started = time.monotonic()
        stage = "snapshot"
        try:
            passport_codes = filter_passport_mcp_codes(plan.projection.mcp_server_codes)
            # Mandatory, not an optimisation — see ``_passport_mcp_items``.
            mcp_items = self._passport_mcp_items(
                identity_modes=plan.identity_modes,
                bot_id=plan.bot_id,
                engine=plan.engine,
                codes=passport_codes,
            )
            logger.info(
                "agentpass_runtime_scope_update_requested bot_id=%s engine_type=%s "
                "branch=runtime_projection stage=%s mcp_count=%s cli_count=%s duration_ms=%s",
                plan.bot_id,
                plan.engine,
                stage,
                len(mcp_items),
                len(plan.effective_cli_items),
                0,
            )
            resource_scope = build_passport_resource_scope(
                self._passport.query_agent_passport(plan.bot_id, plan.owner_id),
                desired_mcp_items=mcp_items,
                mcp_identity_modes=plan.identity_modes,
                additional_cli_items=plan.effective_cli_items,
            )
            stage = "update"
            self._passport.update_passport(
                bot_id=plan.bot_id,
                user_id=plan.owner_id,
                engine_type=plan.engine,
                resource_scope=resource_scope,
            )
            logger.info(
                "agentpass_runtime_scope_update_succeeded bot_id=%s engine_type=%s "
                "branch=runtime_projection stage=%s status=succeeded mcp_count=%s cli_count=%s duration_ms=%s",
                plan.bot_id,
                plan.engine,
                stage,
                len(resource_scope["mcp_items"]),
                len(resource_scope["cli_items"]),
                int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            logger.error(
                "agentpass_runtime_scope_update_failed bot_id=%s engine_type=%s "
                "branch=runtime_projection stage=%s status=failed error_type=%s duration_ms=%s",
                plan.bot_id,
                plan.engine,
                stage,
                type(exc).__name__,
                int((time.monotonic() - started) * 1000),
            )
            raise SkillSetRuntimeReconcileError() from None


__all__ = [
    "BotRuntimeProjector",
]

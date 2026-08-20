"""Deep module for applying one complete Bot capability projection."""

from __future__ import annotations

from collections.abc import Sequence

from injector import inject

from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_set_control_plane import (
    SkillSetControlPlaneRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeProjection,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skill_center.runtime_policy import (
    require_supported_bot_skill_runtime,
)
from agentclaw.community.core.skills_pool.mapping_intent import mapping_contract_for
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    runtime_uses_pool_paths,
)
from agentclaw.community.plugin_api.passport import PassportPlugin


class BotRuntimeProjectionReconciler:
    """Resolve and apply Skill, MCP, and CLI state through one boundary.

    Callers own command compensation. This module owns desired-state loading,
    runtime contract selection, full mapping publication, MCP delivery, and the
    overwrite-style Passport MCP/CLI manifest.
    """

    @inject
    def __init__(
        self,
        factory: SkillSetServiceFactory,
        bot_repo: BotRepository,
        repository: SkillSetControlPlaneRepositoryProtocol,
        pool_skills: SkillsPoolSkillRepositoryProtocol,
        pool_runtime: SkillsPoolRuntimeProtocol,
        pool_layouts: SkillsPoolLayoutRepositoryProtocol,
        passport: PassportPlugin,
    ) -> None:
        self._factory = factory
        self._bot_repo = bot_repo
        self._repository = repository
        self._pool_skills = pool_skills
        self._pool_runtime = pool_runtime
        self._pool_layouts = pool_layouts
        self._passport = passport

    async def reconcile(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        service, bot, engine, projection, effective_cli_items = self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
        )
        await self._apply_skill_projection(
            service=service,
            bot=bot,
            engine=engine,
            bot_id=bot_id,
            owner_id=owner_id,
            projection=projection,
            retired_mappings=retired_mappings,
        )
        await self._apply_non_skill_projection(
            service=service,
            engine=engine,
            bot_id=bot_id,
            owner_id=owner_id,
            projection=projection,
            effective_cli_items=effective_cli_items,
        )

    async def reconcile_non_skill_projection(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None:
        """Rebuild MCP/CLI when a cutover task exclusively owns Skill mappings."""
        service, _bot, engine, projection, effective_cli_items = self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
        )
        await self._apply_non_skill_projection(
            service=service,
            engine=engine,
            bot_id=bot_id,
            owner_id=owner_id,
            projection=projection,
            effective_cli_items=effective_cli_items,
        )

    def _resolve_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ):
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        require_supported_bot_skill_runtime(bot)

        engine = str(bot.get("active_engine") or "openclaw")
        service = self._factory.create(
            user_id=owner_id,
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
            engine_type=engine,
            entity_type=bot.get("entity_type") or "staff",
        )
        skill_assets = tuple(
            self._pool_skills.list_bot_active_assets(
                env=str(bot["env"]),
                bot_id=bot_id,
                user_id=owner_id,
                engine=engine,
            )
        )
        if engine == "teclaw" and (
            any(asset.git_path.startswith("center://") for asset in skill_assets)
            or any(mapping.corpus == "center" for mapping in retired_mappings)
        ):
            # Reject before querying or writing any external MCP, Passport, or
            # runtime boundary. Teclaw Center delivery belongs to Phase 2.
            raise SkillSetRuntimeReconcileError()
        # The legacy SkillSet service remains the authority for effective
        # System Defaults during Phase 1.  It resolves template presets and
        # applies ac_default_skillset_mcp_exclusion; rebuilding defaults from
        # static constants here would silently resurrect user exclusions.
        effective_mcp_entries = service.collect_bot_active_mcps(
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
            user_id=owner_id,
            entity_type=bot.get("entity_type") or "staff",
            engine_type=engine,
        )
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
                    self._repository.list_installed_mcps(bot_id=bot_id)
                ),
                system_default_mcp_server_codes=effective_default_mcp_codes,
                system_default_cli_commands=tuple(
                    str(item["cli_code"])
                    for item in effective_cli_items
                    if item.get("cli_code")
                ),
            )
        )

        return service, bot, engine, projection, effective_cli_items

    async def _apply_skill_projection(
        self,
        *,
        service,
        bot: dict,
        engine: str,
        bot_id: str,
        owner_id: str,
        projection: RuntimeProjection,
        retired_mappings: Sequence[PoolSkillMapping],
    ) -> None:
        mappings = list(projection.skill_mappings)
        retired = list(retired_mappings)
        if engine == "teclaw" and any(
            mapping.corpus == "center" for mapping in [*mappings, *retired]
        ):
            # Teclaw v4 has no Center request contract. Phase 2 adds its
            # OSS-backed Center Store; Phase 1 must fail before any runtime,
            # MCP, Passport, probe, or mapping request is emitted.
            raise SkillSetRuntimeReconcileError()

        desired_skills = [
            {
                "id": str(asset.skill_id),
                "name": asset.name,
                "git_path": asset.git_path,
                "skill_uuid": asset.skill_uuid,
                "sc_version_number": asset.sc_version_number,
            }
            for asset in projection.skill_assets
        ]
        if engine == "teclaw":
            # Teclaw v4 consumes a complete Artifact projection through the
            # existing DeviceSync dispatcher. It has no Skills Pool mapping
            # endpoint; Repo/Local and their retirements must stay on v4.
            if not service.sync_runtime(desired_skills=desired_skills):
                raise SkillSetRuntimeReconcileError()
            return

        scope = BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot.get("entity_id") or owner_id),
            bot_id=bot_id,
        )
        layout_state = self._pool_layouts.get(scope)
        pool_owns_runtime = layout_state is not None and runtime_uses_pool_paths(
            layout_state
        )
        if (
            pool_owns_runtime
            or any(
                mapping.corpus in {"repo", "center"}
                for mapping in [*mappings, *retired]
            )
            or retired
        ):
            await self._apply_pool_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
                engine=engine,
                mappings=mappings,
                retired_mappings=retired,
                source_layout=(
                    SkillMappingSourceLayout.POOL
                    if pool_owns_runtime
                    else SkillMappingSourceLayout.LEGACY
                ),
            )
        elif not service.sync_runtime(desired_skills=desired_skills):
            raise SkillSetRuntimeReconcileError()

    async def _apply_non_skill_projection(
        self,
        *,
        service,
        engine: str,
        bot_id: str,
        owner_id: str,
        projection: RuntimeProjection,
        effective_cli_items: list[dict],
    ) -> None:
        if not await service.sync_mcp_desired_state(
            server_codes=set(projection.mcp_server_codes)
        ):
            raise SkillSetRuntimeReconcileError()

        try:
            self._passport.update_passport(
                bot_id=bot_id,
                user_id=owner_id,
                engine_type=engine,
                resource_scope={
                    "mcp_codes": filter_passport_mcp_codes(
                        projection.mcp_server_codes
                    ),
                    "cli_items": effective_cli_items,
                },
            )
        except Exception as exc:
            raise SkillSetRuntimeReconcileError() from exc

    async def _apply_pool_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: list[PoolSkillMapping],
        source_layout: SkillMappingSourceLayout,
    ) -> None:
        try:
            contract_mappings = [*mappings, *retired_mappings]
            supported_versions: object = None
            if any(mapping.corpus == "center" for mapping in contract_mappings):
                probe = await self._pool_runtime.probe(
                    bot_id=bot_id,
                    user_id=owner_id,
                    engine=engine,
                )
                supported_versions = probe.evidence.get(
                    "supported_mapping_contract_versions"
                )
            contract = mapping_contract_for(contract_mappings, supported_versions)
            published = await self._pool_runtime.publish_mappings(
                bot_id=bot_id,
                user_id=owner_id,
                mappings=mappings,
                retired_mappings=retired_mappings,
                source_layout=source_layout,
                mapping_contract_version=contract,
            )
            verified = published and await self._pool_runtime.verify_mappings(
                bot_id=bot_id,
                user_id=owner_id,
                mappings=mappings,
                retired_mappings=retired_mappings,
                source_layout=source_layout,
                mapping_contract_version=contract,
            )
        except Exception as exc:
            raise SkillSetRuntimeReconcileError() from exc
        if not verified:
            raise SkillSetRuntimeReconcileError()


# Compatibility names for existing constructors and tests. They are aliases,
# not subclasses: the implementation authority remains this Bot-level module.
SkillSetRuntimeReconciler = BotRuntimeProjectionReconciler


__all__ = [
    "BotRuntimeProjectionReconciler",
    "SkillSetRuntimeReconciler",
]

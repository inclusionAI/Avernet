"""Deep module for applying one complete Bot capability projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from injector import inject

from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
    passport_mcp_items_from_codes,
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
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeProjection,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    mapping_contract_for,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    runtime_uses_pool_paths,
)
from agentclaw.community.core.workspace.skill_layout import runtime_layout_engine_for_bot
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import McpScopeItem, PassportPlugin


logger = get_logger()


class BotRuntimeProjector:
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
        repository: CapabilityDesiredStateRepositoryProtocol,
        reader: BotCapabilityStateReaderProtocol,
        pool_runtime: SkillsPoolRuntimeProtocol,
        pool_layouts: SkillsPoolLayoutRepositoryProtocol,
        passport: PassportPlugin,
        caller_identity_repo: CallerIdentityRepositoryProtocol,
    ) -> None:
        self._factory = factory
        self._bot_repo = bot_repo
        self._repository = repository
        self._reader = reader
        self._pool_runtime = pool_runtime
        self._pool_layouts = pool_layouts
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
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        (
            service,
            bot,
            engine,
            projection,
            effective_cli_items,
            identity_modes,
        ) = self._resolve_plan(
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
            scope=scope,
            identity_modes=identity_modes,
            engine=engine,
            bot_id=bot_id,
            owner_id=owner_id,
            projection=projection,
            effective_cli_items=effective_cli_items,
        )

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        """Rebuild MCP/CLI when a cutover task exclusively owns Skill mappings."""
        (
            service,
            bot,
            engine,
            projection,
            effective_cli_items,
            identity_modes,
        ) = self._resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
        )
        await self._apply_non_skill_projection(
            service=service,
            scope=scope,
            identity_modes=identity_modes,
            engine=engine,
            bot_id=bot_id,
            owner_id=owner_id,
            projection=projection,
            effective_cli_items=effective_cli_items,
        )

    async def project_for_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        """Safely remove legacy state without granting new runtime writes.

        Historical engines use their existing full legacy synchronizer for
        Local/Repo removal.  Center requires the Pool v3 contract and is never
        permitted on this compatibility path.
        """
        (
            service,
            bot,
            engine,
            projection,
            effective_cli_items,
            identity_modes,
        ) = self._resolve_cleanup_plan(bot_id=bot_id, owner_id=owner_id)
        if any(mapping.corpus == "center" for mapping in projection.skill_mappings):
            raise SkillSetRuntimeReconcileError()
        if not service.sync_runtime(
            desired_skills=self._desired_skills(projection)
        ):
            raise SkillSetRuntimeReconcileError()
        await self._apply_non_skill_projection(
            service=service,
            scope=scope,
            identity_modes=identity_modes,
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
    ):
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        return self._build_plan(bot=bot, bot_id=bot_id, owner_id=owner_id)

    def _resolve_mcp_identity_modes(
        self, *, bot: dict, bot_id: str, engine: str
    ) -> Mapping[str, object]:
        """Read each MCP's execution identity for this Bot.

        Part of plan resolution rather than delivery: the Passport manifest is
        overwrite-style, so a projection that cannot establish identity must
        not have written anything yet when it gives up.

        A missing primary key fails the projection rather than defaulting.
        Every persisted Bot carries one (``BotModel.to_dict``), so its absence
        means the record is not what this path assumes — and on a privilege
        boundary, guessing Owner is precisely the silent demotion this whole
        change exists to prevent.
        """
        bot_pk = bot.get("id")
        if bot_pk is None:
            logger.error(
                "[BotRuntimeProjector] Bot record has no primary key, refusing "
                "to project MCP identity: bot_id=%s, engine=%s",
                bot_id, engine,
            )
            raise SkillSetRuntimeReconcileError()
        try:
            return self._caller_identity_repo.list_draft_call_types(
                int(bot_pk), engine
            )
        except Exception as exc:
            # Name the Bot and engine: the generic reconcile error carries
            # neither, and a stale row here blocks every SkillSet mutation.
            logger.exception(
                "[BotRuntimeProjector] MCP execution identity unreadable: "
                "bot_id=%s, engine=%s",
                bot_id, engine,
            )
            raise SkillSetRuntimeReconcileError() from exc

    def _build_plan(
        self,
        *,
        bot: dict,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ):
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

        return (
            service,
            bot,
            engine,
            projection,
            effective_cli_items,
            identity_modes,
        )

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

        desired_skills = self._desired_skills(projection)
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
                layout_engine=runtime_layout_engine_for_bot(bot),
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

    async def _apply_non_skill_projection(
        self,
        *,
        service,
        scope: ProjectionScope,
        identity_modes: Mapping[str, object],
        engine: str,
        bot_id: str,
        owner_id: str,
        projection: RuntimeProjection,
        effective_cli_items: list[dict],
    ) -> None:
        codes = set(projection.mcp_server_codes)
        if scope.reconcile:
            # Nothing declared anything — a device-activated restart or a
            # Skill upload. The device may hold no configuration at all, so
            # every projected code counts as newly claimed.
            claimed, released = frozenset(codes), frozenset()
        else:
            # A guard, never a source. ``claimed`` cannot grow past what the
            # mutation declared, so a single-MCP add stays a single device
            # write. ``- codes`` stops a release from deleting a code the
            # default policy or a Skill dependency still supplies without any
            # Set claiming it.
            claimed = scope.claimed_mcp & codes
            released = scope.released_mcp - codes
            if claimed != scope.claimed_mcp or released != scope.released_mcp:
                logger.info(
                    "[BotRuntimeProjector] MCP scope guarded against the "
                    "projected set: bot_id=%s, claimed %s->%s, released %s->%s",
                    bot_id,
                    sorted(scope.claimed_mcp), sorted(claimed),
                    sorted(scope.released_mcp), sorted(released),
                )
        # Configuration lands before the allow-list cites it, and is withdrawn
        # before the allow-list stops covering it.
        if not await service.sync_mcp_delivery(claimed=claimed, released=released):
            raise SkillSetRuntimeReconcileError()

        if not await service.sync_mcp_desired_state(server_codes=codes):
            raise SkillSetRuntimeReconcileError()

        try:
            passport_codes = filter_passport_mcp_codes(projection.mcp_server_codes)
            # Mandatory, not an optimisation — see ``_passport_mcp_items``.
            mcp_items = self._passport_mcp_items(
                identity_modes=identity_modes,
                bot_id=bot_id,
                engine=engine,
                codes=passport_codes,
            )
            self._passport.update_passport(
                bot_id=bot_id,
                user_id=owner_id,
                engine_type=engine,
                resource_scope={
                    # Derived from the items rather than passed separately:
                    # ``unpack_resource_scope`` ignores ``mcp_codes`` once
                    # ``mcp_items`` is present, so two independent lists could
                    # silently diverge and only one would reach the passport service.
                    "mcp_codes": [item["mcp_code"] for item in mcp_items],
                    "mcp_items": mcp_items,
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
        layout_engine: str,
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
                    engine=layout_engine,
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

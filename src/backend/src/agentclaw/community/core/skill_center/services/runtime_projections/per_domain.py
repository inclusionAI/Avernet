"""Runtime projection for engines whose halves have separate endpoints."""

from __future__ import annotations

from collections.abc import Sequence

from agentclaw.community.core.skill_center.errors import (
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    ResolvedCapabilityPlan,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeProjection
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
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
from agentclaw.community.core.workspace.skill_layout import runtime_layout_engine_for_bot
from agentclaw.community.log import get_logger


logger = get_logger()


class PerDomainRuntimeProjection:
    """Write each half of the projection to its own runtime endpoint.

    The contract every filesystem engine obeys: Skills reach the device as a
    symlink/mapping publish, MCPs as configuration delivery plus an allow-list
    declaration. The two are independent writes to independent endpoints, so
    re-sending the half a mutation did not touch costs a device round trip (or
    a Pool publish plus verify) to restate what is already there — which is
    what makes ``ProjectionScope``'s halves worth honouring here.
    """

    def __init__(
        self,
        *,
        pool_runtime: SkillsPoolRuntimeProtocol,
        pool_layouts: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._pool_runtime = pool_runtime
        self._pool_layouts = pool_layouts

    def validate_plan(
        self,
        *,
        skill_assets: Sequence[object],
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        """Accept every plan: these engines have the full Center contract.

        Not an oversight and not a stub. Center-corpus Skills reach a
        filesystem engine through the Skills Pool v3 mapping contract, which
        this projection publishes and verifies below, so there is nothing here
        to refuse. The method exists because *some* engine has to be able to
        say no — see ``WholeArtifactRuntimeProjection.validate_plan``.
        """

    async def apply(
        self,
        *,
        plan: ResolvedCapabilityPlan,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        """Write the halves ``scope`` declares, and only those.

        A mutation that changed one half has nothing to say to the other, and
        both halves are whole-snapshot writes: re-sending the unchanged one
        costs a round trip to restate what is already there.
        ``ProjectionScope.everything()`` sets both flags, so a caller with
        nothing to declare still projects both.

        ``retired_mappings`` overrides the Skill flag rather than trusting it:
        those retirements were computed from the actual before/after
        snapshots, so they are evidence that Skills moved. Skipping them would
        strand a published mapping the desired state no longer holds.
        """
        if scope.skills or retired_mappings:
            await self._apply_skill_projection(
                plan=plan, retired_mappings=retired_mappings
            )
        else:
            logger.info(
                "[PerDomainRuntimeProjection] Skill projection skipped, scope "
                "declares no Skill change: bot_id=%s, engine=%s",
                plan.bot_id, plan.engine,
            )
        if scope.mcp:
            await self._apply_mcp_projection(plan=plan, scope=scope)
        else:
            logger.info(
                "[PerDomainRuntimeProjection] MCP projection skipped, scope "
                "declares no MCP change: bot_id=%s, engine=%s",
                plan.bot_id, plan.engine,
            )

    async def _apply_skill_projection(
        self,
        *,
        plan: ResolvedCapabilityPlan,
        retired_mappings: Sequence[PoolSkillMapping],
    ) -> None:
        mappings = list(plan.projection.skill_mappings)
        retired = list(retired_mappings)
        bot = plan.bot

        # TRANSITIONAL — removed once ``WholeArtifactRuntimeProjection`` is
        # registered to receive teclaw. Until then this class is still the
        # only implementation, so teclaw arrives here and must keep behaving
        # exactly as it does today; deleting these two arms before the
        # registry can route teclaw elsewhere would silently push it onto the
        # Pool/legacy path below. Both move to that class's ``validate_plan``
        # and ``apply``.
        if plan.engine == "teclaw" and any(
            mapping.corpus == "center" for mapping in [*mappings, *retired]
        ):
            # Teclaw v4 has no Center request contract. Phase 2 adds its
            # OSS-backed Center Store; Phase 1 must fail before any runtime,
            # MCP, Passport, probe, or mapping request is emitted.
            raise SkillSetRuntimeReconcileError()
        if plan.engine == "teclaw":
            # Teclaw v4 consumes a complete Artifact projection through the
            # existing DeviceSync dispatcher. It has no Skills Pool mapping
            # endpoint; Repo/Local and their retirements must stay on v4.
            if not plan.service.sync_runtime(
                desired_skills=self._desired_skills(plan.projection)
            ):
                raise SkillSetRuntimeReconcileError()
            return

        scope = BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot.get("entity_id") or plan.owner_id),
            bot_id=plan.bot_id,
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
                bot_id=plan.bot_id,
                owner_id=plan.owner_id,
                layout_engine=runtime_layout_engine_for_bot(bot),
                mappings=mappings,
                retired_mappings=retired,
                source_layout=(
                    SkillMappingSourceLayout.POOL
                    if pool_owns_runtime
                    else SkillMappingSourceLayout.LEGACY
                ),
            )
        elif not plan.service.sync_runtime(
            desired_skills=self._desired_skills(plan.projection)
        ):
            raise SkillSetRuntimeReconcileError()

    async def _apply_mcp_projection(
        self,
        *,
        plan: ResolvedCapabilityPlan,
        scope: ProjectionScope,
    ) -> None:
        codes = set(plan.projection.mcp_server_codes)
        if scope.claim_all_mcp:
            # The device-activated listener, and only it. A freshly active
            # container holds no MCP configuration, so there is nothing to
            # refresh against — the allow-list alone would whitelist every MCP
            # with no endpoint or api_key behind it. The caller cannot name
            # the codes itself: the projected set is only known here, after
            # the plan resolves. Nothing is released on this path, so it can
            # only ever add configuration.
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
                    "[PerDomainRuntimeProjection] MCP scope guarded against the "
                    "projected set: bot_id=%s, claimed %s->%s, released %s->%s",
                    plan.bot_id,
                    sorted(scope.claimed_mcp), sorted(claimed),
                    sorted(scope.released_mcp), sorted(released),
                )
        # One call, not two: how many device writes an MCP projection takes,
        # and in what order, is decided by the service that owns device
        # resolution. See ``SkillSetService.sync_mcp_projection``.
        if not await plan.service.sync_mcp_projection(
            claimed=claimed, released=released, declared=codes
        ):
            raise SkillSetRuntimeReconcileError()

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
    def _desired_skills(
        projection: RuntimeProjection,
    ) -> list[dict[str, str | None]]:
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


__all__ = ["PerDomainRuntimeProjection"]

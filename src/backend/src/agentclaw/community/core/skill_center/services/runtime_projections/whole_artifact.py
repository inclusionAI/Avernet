"""Runtime projection for engines that consume one complete artifact."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from agentclaw.community.core.skill_center.errors import (
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    EngineRuntimeProjection,
    ProjectionScope,
    ResolvedCapabilityPlan,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeProjection
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)
from agentclaw.community.log import get_logger


logger = get_logger()


class WholeArtifactRuntimeProjection(EngineRuntimeProjection):
    """Deliver the Bot's complete capability state in a single runtime call.

    The contract teclaw v4 obeys. Every ``DeviceSync`` entry point on such an
    engine funnels into one place that recomposes the Bot's whole
    configuration *from the database* and posts it, discarding the arguments
    it was handed. Two consequences shape everything below.

    First, the scope cannot select content. It never reaches the runtime: the
    composer reads persisted desired state, so what arrives is a function of
    the database at the moment of the call and nothing else. A second call
    would restate in full what the first already delivered — which is why a
    projection here is one call regardless of which halves a mutation
    declared, and why the scope only decides whether to deliver at all.

    Second, one call after ``_resolve_plan`` is *sufficient*. Plan resolution
    is what flushes SkillSet configuration into Installation, so by the time
    an implementation is chosen the persisted state is final and the composer
    re-reads exactly that. There is no ordering in which a later delivery
    could carry something an earlier one missed.
    """

    def validate_plan(
        self,
        *,
        skill_assets: Sequence[RegisteredSkillAsset],
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        """Refuse Center-corpus desired state: v4 has no request contract for it.

        Phase 2 adds the OSS-backed Center Store. Until then this must fail
        before any runtime, MCP, Passport, probe or mapping request is
        emitted, which is why it is asked during plan resolution rather than
        at delivery.
        """
        if any(
            asset.git_path.startswith("center://") for asset in skill_assets
        ) or any(mapping.corpus == "center" for mapping in retired_mappings):
            raise SkillSetRuntimeReconcileError()

    async def apply(
        self,
        *,
        plan: ResolvedCapabilityPlan,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        """Deliver once, or not at all.

        The scope is read for one bit only — did this mutation change
        anything — because that is the only question a whole-artifact runtime
        can answer differently. Which half changed decides nothing: both ride
        in the same document.
        """
        if not (scope.skills or scope.mcp or retired_mappings):
            logger.info(
                "[WholeArtifactRuntimeProjection] Delivery skipped, scope "
                "declares no change: bot_id=%s, engine=%s",
                plan.bot_id, plan.engine,
            )
            return

        # Defence in depth. Plan resolution already refused this, but the
        # delivery is the thing that would strand Center state on a runtime
        # with no contract for it, so the guard sits next to the write too.
        self.validate_plan(
            skill_assets=plan.projection.skill_assets,
            retired_mappings=retired_mappings,
        )

        logger.info(
            "[WholeArtifactRuntimeProjection] Delivering whole artifact: "
            "bot_id=%s, engine=%s, skills=%s, mcps=%s",
            plan.bot_id,
            plan.engine,
            len(plan.projection.skill_assets),
            len(plan.projection.mcp_server_codes),
        )
        # Off the event loop: ``sync_runtime`` is synchronous and, on a
        # whole-artifact engine, expensive — device resolution (including a
        # blocking ws-info HTTP call), a full artifact compose, and the
        # outbound apply request. Callers reach here from async HTTP handlers
        # such as ``DirectActivationService.activate_mcp``, so leaving it in
        # the coroutine would let one slow container stall unrelated requests
        # on the same worker. Same rule, and the same reason, as
        # ``SkillSetService.sync_mcp_desired_state``.
        if not await asyncio.to_thread(
            plan.service.sync_runtime,
            desired_skills=self._desired_skills(plan.projection),
        ):
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


__all__ = ["WholeArtifactRuntimeProjection"]

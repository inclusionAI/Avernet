"""Mutate-project-compensate: the one orchestration under every activation
command.

Internal to the command services (the Set service and the direct-activation
service) — not a public layer. Both promise the same synchronous contract:
success means the runtime converged on the new desired state, failure means
desired state was compensated back. This class is that promise, extracted so
the two services cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotReadyError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


class MutationProjectionFlow:
    """Apply one desired-state mutation and synchronously project the runtime.

    On projection failure the mutation is compensated: desired state is
    restored from the mutation's snapshot and the runtime is counter-projected
    before ``SkillSetRuntimeReconcileError`` surfaces.
    """

    def __init__(
        self,
        *,
        repository: CapabilityDesiredStateRepositoryProtocol,
        runtime: BotRuntimeProjectorProtocol,
    ) -> None:
        self._repository = repository
        self._runtime = runtime

    async def apply(
        self,
        *,
        bot: dict,
        bot_id: str,
        engine_type: str | None,
        mutation: Callable[[], DesiredStateMutation],
        runtime_required: bool = True,
    ) -> dict:
        """Run the command; return ``{**item, "changed": ..., **details}``.

        ``runtime_required=False`` skips readiness and projection entirely —
        an inactive-set membership change has no runtime projection to apply,
        preserving the legacy inactive draft contract. ``engine_type`` scopes
        a compensation's restore to the Sets the mutation could have touched.
        """
        if not runtime_required:
            result = mutation()
            return {**result.item, "changed": result.changed, **result.details}
        if not is_bot_ready(bot):
            raise LocalSkillNotReadyError()
        owner_id = str(bot["owner_id"])
        previous_mappings = await self._runtime.snapshot_skill_mappings(
            bot_id=bot_id,
            owner_id=owner_id,
        )
        result = mutation()
        await self._project_or_compensate(
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            mutation=result,
            previous_mappings=previous_mappings,
        )
        return {**result.item, "changed": result.changed, **result.details}

    async def _project_or_compensate(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        mutation: DesiredStateMutation,
        previous_mappings: Sequence[PoolSkillMapping],
    ) -> None:
        current_mappings: Sequence[PoolSkillMapping] = ()
        try:
            current_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
            )
            await self._runtime.project(
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
                engine_type=engine_type,
            )
            try:
                await self._runtime.project(
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

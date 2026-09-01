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
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


def skill_claim_scope(result: DesiredStateMutation) -> ProjectionScope:
    """A Skill mutation that adds the Skill, and with it its MCP dependencies.

    ``mcp`` follows the dependencies rather than being hard-coded: a Skill with
    none leaves the MCP set untouched, so projecting that half would re-declare
    an unchanged allow-list and re-push an unchanged Passport manifest.

    The codes are candidates. The projector intersects them with the set it
    actually resolved, so a dependency that does not survive projection is
    never delivered.
    """
    return ProjectionScope(
        skills=True,
        mcp=bool(result.mcp_codes),
        claimed_mcp=result.mcp_codes,
    )


def skill_release_scope(result: DesiredStateMutation) -> ProjectionScope:
    """The mirror of ``skill_claim_scope`` for a Skill leaving the Bot.

    Also candidates: another Skill or the default policy may still supply the
    same code, and the projector subtracts the projected set before deleting
    any device configuration.
    """
    return ProjectionScope(
        skills=True,
        mcp=bool(result.mcp_codes),
        released_mcp=result.mcp_codes,
    )


def mcp_claim_scope(result: DesiredStateMutation) -> ProjectionScope:
    """Project only the MCP codes the committed mutation actually claimed."""
    return ProjectionScope(
        mcp=bool(result.mcp_codes),
        claimed_mcp=result.mcp_codes,
    )


def mcp_release_scope(result: DesiredStateMutation) -> ProjectionScope:
    """Project only the MCP codes the committed mutation actually released."""
    return ProjectionScope(
        mcp=bool(result.mcp_codes),
        released_mcp=result.mcp_codes,
    )


class MutationProjectionFlow:
    """Commit desired state, then make a best-effort runtime projection.

    Runtime is an observed delivery result, not a prerequisite for a legal
    capability mutation.  Repository/UoW failures still surface from
    ``mutation`` unchanged; once it returns, a device failure must never
    restore the committed Installation state.
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
        scope: ProjectionScope | None = None,
        scope_from_result: (
            Callable[[DesiredStateMutation], ProjectionScope] | None
        ) = None,
        skip_projection_when_unchanged: bool = False,
    ) -> dict:
        """Run the command; return ``{**item, "changed": ..., **details}``.

        ``runtime_required=False`` skips readiness and projection entirely —
        an inactive-set membership change has no runtime projection to apply,
        preserving the legacy inactive draft contract. ``engine_type`` scopes
        a compensation's restore to the Sets the mutation could have touched.

        ``scope`` is what this mutation changed, declared by the command that
        knows it. Exactly one of ``scope`` / ``scope_from_result`` must be
        given: there is no "forgot to say" default, because the fallback would
        be a full reconcile — the expensive answer, and never the one a
        mutation wants. A caller with genuinely nothing to narrow passes
        ``ProjectionScope.everything()`` and says so out loud.

        ``scope_from_result`` covers the commands that cannot name their scope
        up front: activate and membership commands learn which MCPs they
        claimed or released only from the mutation result, which the repository
        fills in under the row lock it already holds. Building the scope from a
        second, unlocked query instead could disagree with what was actually
        installed.
        """
        if (scope is None) == (scope_from_result is None):
            raise ValueError("exactly one of scope / scope_from_result is required")
        if not runtime_required:
            result = mutation()
            return {
                **result.item,
                "changed": result.changed,
                **result.details,
                "desired_state": self._desired_state(result.changed),
                "runtime_projection": {"status": "SKIPPED", "issues": []},
            }
        owner_id = str(bot["owner_id"])
        previous_mappings: Sequence[PoolSkillMapping] = ()
        snapshot_failed = False
        try:
            # Retirement is a diff between the committed pre- and post-mutation
            # snapshots.  A failed runtime read is itself a projection issue,
            # never a reason to reject the following desired-state write.
            previous_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
            )
        except Exception:
            snapshot_failed = True
        result = mutation()
        if skip_projection_when_unchanged and not result.changed:
            return {
                **result.item,
                "changed": False,
                **result.details,
                "desired_state": self._desired_state(False),
                "runtime_projection": {"status": "SKIPPED", "issues": []},
            }
        if not is_bot_ready(bot):
            return {
                **result.item,
                "changed": result.changed,
                **result.details,
                "desired_state": self._desired_state(result.changed),
                "runtime_projection": {
                    "status": "PENDING",
                    "issues": [{
                        "code": "BOT_NOT_READY",
                        "retryable": True,
                        "suggested_action": "Wait for the Bot to become ready.",
                    }],
                },
            }
        if snapshot_failed:
            return {
                **result.item,
                "changed": result.changed,
                **result.details,
                "desired_state": self._desired_state(result.changed),
                "runtime_projection": self._pending_projection(),
            }
        effective_scope = (
            scope_from_result(result) if scope_from_result is not None else scope
        )
        assert effective_scope is not None  # guaranteed by the check above
        projection = await self._project_best_effort(
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            previous_mappings=previous_mappings,
            scope=effective_scope,
        )
        return {
            **result.item,
            "changed": result.changed,
            **result.details,
            "desired_state": self._desired_state(result.changed),
            "runtime_projection": projection,
        }

    async def _project_best_effort(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        previous_mappings: Sequence[PoolSkillMapping],
        scope: ProjectionScope,
    ) -> dict[str, object]:
        del engine_type
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
                scope=scope,
            )
        except Exception as exc:
            # Do not leak device addresses, paths, or backend exception text
            # through the product wire.  Structured logs retain the exception
            # chain while callers receive a stable, retryable outcome.
            return self._pending_projection()
        return {"status": "CONVERGED", "issues": []}

    @staticmethod
    def _pending_projection() -> dict[str, object]:
        return {
            "status": "PENDING",
            "issues": [{
                "code": "RUNTIME_PROJECTION_UNAVAILABLE",
                "retryable": True,
                "suggested_action": "Retry after the runtime is available.",
            }],
        }

    @staticmethod
    def _desired_state(changed: bool) -> dict[str, object]:
        return {"status": "COMMITTED" if changed else "UNCHANGED", "changed": changed}

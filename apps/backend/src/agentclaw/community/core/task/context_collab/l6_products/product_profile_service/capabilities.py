"""Safe public defaults. Missing capabilities never produce simulated success."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)


class UnavailableRolloutExecution:
    def require(self, execution_ref, acceptance_ref, stages):
        raise Invalid("product execution and evidence capabilities are not configured")

    async def start(self, execution_ref, candidate, content, attempt, context):
        raise Invalid("product execution is not configured")

    async def observe(self, route):
        raise Invalid("product execution observation is not configured")

    async def cancel(self, route):
        raise Invalid("product cancellation is not configured")

    async def verify(
        self, acceptance_ref, route, conditions, artifacts, acceptances, context
    ):
        raise Invalid("trusted product acceptance is not configured")


class ConservativeRolloutInteraction:
    async def propose(self, snapshot, content, context):
        # Do not pretend to interpret arbitrary language as privileged commands.
        from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
            Adjustment,
        )
        from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
            AdjustTypeEnum,
        )

        return Adjustment(
            AdjustTypeEnum.DO_NOTHING,
            "No adjustment applied: a trusted interaction capability must be configured.",
        )


class UnavailableStageMessages:
    async def read(self, route, limit):
        raise Invalid("stage session message provider is not configured")

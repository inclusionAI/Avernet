"""Fail-closed execution capability until the host installs a real worker port."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageExecutionReport,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
    SwarmExecutionProfile,
)


class UnavailableStageExecutionService:
    def execute(
        self,
        stage: Stage,
        participant: Participant,
        profile: SwarmExecutionProfile,
        allocation_ref: str,
        collet_id: str,
        binding_id: str,
    ) -> None:
        raise ContextCollabValidationError(
            "Swarm Stage execution capability is not configured"
        )

    def accepts(self, stage: Stage, report: StageExecutionReport) -> bool:
        return False

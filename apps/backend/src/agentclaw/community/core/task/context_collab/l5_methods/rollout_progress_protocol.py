"""Independent authenticated progress Service API; not a sixth product method."""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ReportContext,
)


@runtime_checkable
class RolloutProgressServiceProtocol(Protocol):
    async def report(self, event: ProgressEvent, context: ReportContext) -> dict:
        """Accept a fact under trusted actor/scope plus allocation and CAS fences.

        Delivery must authenticate the actor and tenant independently of the
        body. ReportContext's attempt/revision are untrusted claims checked
        against durable allocation facts. A replay returns its original receipt.
        """
        ...

"""Local product reviewer for capability declarations; not an L5 implementation."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationApplication,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenAdmissionPolicy,
)


class LocalCapabilityAdmissionService:
    """Simulate the owner's review, without admitting or granting participation.

    For this product each capability ref in an application is a self-declaration.
    Any exact match with the configured recruiting needs qualifies; one Bot need
    not cover every Swarm stage. Empty needs or declarations do not qualify.
    No Bot identity, role allowlist, remote evidence lookup or LLM is involved.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[OpenAdmissionPolicy, ParticipationApplication]] = []

    def should_admit(
        self, policy: OpenAdmissionPolicy, application: ParticipationApplication
    ) -> bool:
        if policy.decision_rule != "capability_evidence":
            raise ContextCollabValidationError(
                "local capability reviewer requires capability_evidence rule"
            )
        self.calls.append((policy, application))
        return bool(
            set(policy.required_capabilities).intersection(
                application.capability_evidence_refs
            )
        )

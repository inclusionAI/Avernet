"""Match explicit L4 RoleBinding requirements, without granting participation."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ParticipationFacts,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import Stage


def stage_role_candidates(
    collet_id: str, stage: Stage, facts: ParticipationFacts
) -> tuple[Participant, ...]:
    """Read-only role intersection, not an allocation or a resource grant.

    Requirements reference actual L4 RoleBindings. Unknown or foreign role refs
    fail closed. Opaque capability/resource requirements are left to the
    controller; this function does not assert those are satisfied.
    """
    requirements = {ref for ref in stage.requirement_refs if "/role-bindings/" in ref}
    bindings = {role_binding_ref(collet_id, b): b for b in facts.role_bindings}
    if not requirements <= bindings.keys():
        raise ContextCollabValidationError("stage references an unknown L4 RoleBinding")
    return tuple(
        p
        for p in facts.participants
        if p.agent_ref is not None
        and (stage.workflow_node is None or p.agent_ref in stage.workflow_node.agents)
        and all(
            bindings[ref].participant_id == p.participant_id for ref in requirements
        )
    )

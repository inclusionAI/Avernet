"""Translate declared ScenarioRoles + supplied identities into actual L4 facts."""

from urllib.parse import quote

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    RoleBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ParticipationFacts,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    validate_role_sources,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)


def product_participation(
    profile: ProductProfileDefinition,
    principal: PrincipalRef,
    agents: tuple[AgentRef, ...],
    executor: AgentRef | None,
) -> ParticipationFacts:
    for agent in agents:
        profile.participant_scope.require(agent)
    validate_role_sources(profile, principal, executor)
    participants = [Participant("participant_principal_ref", principal, None)]
    for agent in agents:
        participant_id = (
            "participant_executor_ref"
            if agent == executor
            else "participant_"
            + quote(agent.agent_type, safe="")
            + ":"
            + quote(agent.agent_id, safe="")
        )
        participants.append(Participant(participant_id, None, agent))
    bindings = tuple(
        RoleBinding(role.l4_role, "participant_" + role.participant_source)
        for role in profile.scenario_roles
        if role.participant_source == "principal_ref" or executor is not None
    )
    return ParticipationFacts(tuple(participants), bindings)

"""Translate declared ScenarioRoles + supplied identities into actual L4 facts."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
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
    agent_participant_id,
    scenario_participant_id,
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
    # A concrete profile assignment must be supplied explicitly, even for an
    # optional role. A declaration alone must not fabricate participation.
    for role in profile.scenario_roles:
        if role.participant_source == "agent_ref" and role.agent_ref not in agents:
            raise ContextCollabValidationError(
                f"product role {role.name!r} requires its Agent in participant_refs"
            )
    participants = [Participant("participant_principal_ref", principal, None)]
    for agent in agents:
        participants.append(
            Participant(agent_participant_id(agent, executor), None, agent)
        )
    bindings = tuple(
        RoleBinding(role.l4_role, scenario_participant_id(role, executor))
        for role in profile.scenario_roles
        if role.participant_source != "executor_ref" or executor is not None
    )
    return ParticipationFacts(tuple(participants), bindings)

"""Product eligibility boundary; OPEN is not admission, selection or a grant.

This is a local typed Profile contract, not a new ACCS membership authority.
"""

from dataclasses import dataclass
from enum import Enum

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    normalize_executor_refs,
)


class ParticipantScopeKind(str, Enum):
    OPEN = "OPEN"
    ALLOWLIST = "ALLOWLIST"


@dataclass(frozen=True)
class ParticipantScope:
    kind: ParticipantScopeKind = ParticipantScopeKind.OPEN
    agent_refs: tuple[AgentRef, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", ParticipantScopeKind(self.kind))
        except (ValueError, TypeError) as error:
            raise ContextCollabValidationError(
                "invalid participant scope kind"
            ) from error
        object.__setattr__(self, "agent_refs", normalize_executor_refs(self.agent_refs))
        if self.kind is ParticipantScopeKind.OPEN and self.agent_refs:
            raise ContextCollabValidationError(
                "OPEN participant scope cannot contain an allowlist"
            )
        # An empty ALLOWLIST intentionally allows no Agents (owner-only setup).

    def allows(self, agent: AgentRef) -> bool:
        if not isinstance(agent, AgentRef):
            return False
        AgentRef.from_dict(agent.to_dict())
        return self.kind is ParticipantScopeKind.OPEN or agent in self.agent_refs

    def require(self, agent: AgentRef) -> None:
        if not self.allows(agent):
            raise ContextCollabValidationError(
                "Agent is outside the ProductProfile participant scope"
            )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "agent_refs": [ref.to_dict() for ref in self.agent_refs],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ParticipantScope":
        if not isinstance(data, dict) or set(data) != {"kind", "agent_refs"}:
            raise ContextCollabValidationError("invalid participant scope fields")
        refs = data["agent_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
            raise ContextCollabValidationError(
                "participant scope agent_refs must be a list of AgentRefs"
            )
        if any(set(ref) != {"agent_type", "agent_id"} for ref in refs):
            raise ContextCollabValidationError(
                "invalid participant scope AgentRef fields"
            )
        return cls(data["kind"], tuple(AgentRef.from_dict(ref) for ref in refs))

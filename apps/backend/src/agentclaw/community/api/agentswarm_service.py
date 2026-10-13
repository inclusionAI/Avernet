"""Service API for agentswarm swarm runs — submit, poll, list, cancel, stream.

Re-export only. The Protocol, its ``SubmitRunRequest`` DTO, the run-events
read model, and the typed errors its implementations raise are defined in the
owning core module (``core/agentswarm/service/protocols.py``) so the
concrete service can inherit it without a ``core -> api`` waiver; adapters
keep importing them from here.
"""

from __future__ import annotations

from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmServiceProtocol,
    AgentEvent,
    GuardEventSummary,
    RoundEvent,
    RunEvents,
    SubmitRunRequest,
)

__all__ = [
    "AgentSwarmServiceProtocol",
    "AgentEvent",
    "GuardEventSummary",
    "RoundEvent",
    "RunEvents",
    "SubmitRunRequest",
]

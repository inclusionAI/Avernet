"""Service API Protocol for applying a bot's configuration manifest (#1472).

Re-export only. The Protocol is defined in its owning core module
(``core/apply_engine/run/service_protocol.py``) so
the concrete service can inherit it without a ``core -> api`` waiver; adapters
keep importing it from here — the same arrangement the sibling manifest contract
uses.
"""

from __future__ import annotations

from agentclaw.community.core.apply_engine.run.service_protocol import (
    ApplyAccepted,
    ApplyPhase,
    ApplyReport,
    ApplyRunServiceProtocol,
    ApplyInProgressError,
    StateRef,
)

__all__ = [
    "ApplyAccepted",
    "ApplyPhase",
    "ApplyReport",
    "ApplyRunServiceProtocol",
    "ApplyInProgressError",
    "StateRef",
]

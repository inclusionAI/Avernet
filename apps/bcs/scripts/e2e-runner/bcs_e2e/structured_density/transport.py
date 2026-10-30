"""Transport boundary used by the structured density executor.

The executor accepts an injected transport.  It does not construct HTTP
clients, resolve credentials, or contact preprod.  Tests use the replay
transport below; a future registration seam may provide an owned-local
transport without changing assertion projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Observation:
    status: int
    body: Any


class StructuredDensityTransport(Protocol):
    def request(self, operation: str, assertion_id: str) -> Observation:
        """Return one deterministic observation for an assertion site."""


class ReplayTransport:
    """Offline fake transport with call recording and no network capability."""

    def __init__(self, observations: dict[str, Observation]):
        self.observations = observations
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, assertion_id: str) -> Observation:
        self.calls.append((operation, assertion_id))
        return self.observations[assertion_id]

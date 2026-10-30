"""Transport boundary and the canonical defect-faithful offline replay.

The density executor never opens a network connection and never launches a
process: it accepts an injected transport (Protocol) and evaluates the frozen
14-clause roster against the returned observations.  Two offline transports
live here:

* ``ReplayTransport`` — assertion-id-keyed fake used by tests and by any
  future owned-local seam, with call recording and no network capability;
* ``ProjectionReplayTransport`` — operation-keyed adapter that turns the
  recorded DM journey projections (submit/read/cancel/terminal bodies) into
  clause observations, which is the shape the describe-only mainline hunk
  proposes to hang under the existing ``dual-direct-chat-run`` envelope.

``canonical_defect_faithful_observations`` builds the deterministic offline
replay of the reviewed main journey.  Its cancelled terminal deliberately
carries ``error_message="downstream_error"`` — the station-designed abort
product on file (DM-TERMINAL-CANCEL-PROJECTION) — so a PASS on the raw parsed
clauses can never be mistaken for a repair of the strict DM admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contract import (
    MAIN_CANCEL_OPERATION,
    MAIN_READ_OPERATION,
    MAIN_SUBMIT_OPERATION,
    MAIN_TERMINAL_OPERATION,
)
from .defect import DOWNSTREAM_ERROR_MARKER

CANONICAL_RUN_ID = "run-direct-density-1"
CANONICAL_SESSION_ID = "sess-direct-density-1"
CANONICAL_MEMBER_BOT_ID = "bot-pm-direct-density-1"

DEFAULT_OPERATION_STATUSES = {
    MAIN_SUBMIT_OPERATION: 202,
    MAIN_READ_OPERATION: 200,
    MAIN_CANCEL_OPERATION: 200,
    MAIN_TERMINAL_OPERATION: 200,
}


@dataclass(frozen=True)
class Observation:
    """One deterministic wire observation for a clause site."""

    status: int
    body: Any


class DensityTransport(Protocol):
    def request(self, operation: str, assertion_id: str) -> Observation:
        """Return one deterministic observation for a clause site."""


class ReplayTransport:
    """Offline fake transport with call recording and no network capability."""

    def __init__(self, observations: dict[str, Observation]):
        self.observations = observations
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, assertion_id: str) -> Observation:
        self.calls.append((operation, assertion_id))
        return self.observations[assertion_id]


class ProjectionReplayTransport(DensityTransport):
    """Replay recorded DM journey projections as clause observations.

    ``bodies`` maps the four reviewed main-journey operations to their recorded
    response bodies; statuses default to the reviewed DM wire expectations and
    can be overridden per operation for negative rehearsals.
    """

    def __init__(self, bodies: dict[str, dict[str, Any]],
                 statuses: dict[str, int] | None = None):
        self.bodies = bodies
        self.statuses = {**DEFAULT_OPERATION_STATUSES, **(statuses or {})}
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, assertion_id: str) -> Observation:
        self.calls.append((operation, assertion_id))
        return Observation(status=self.statuses[operation], body=self.bodies[operation])


def canonical_defect_faithful_observations() -> dict[str, Observation]:
    """The reviewed main journey as a deterministic defect-faithful replay.

    Main submit returns 202 with the exact Run/session identity and PM target;
    the mid-flight read exposes a non-empty running state; the cancel
    acknowledgement is the reviewed cancelled ack; the terminal read projects
    the station-designed CANCELLED terminal **carrying the on-file
    downstream_error marker**, exactly as the r4 preprod convergence journal
    recorded it.  Raw parsed clauses C01-C14 evaluate against these truthfully.
    """
    return {
        "DIRECT_DENSITY.C01": Observation(202, {
            "run_id": CANONICAL_RUN_ID,
            "session_id": CANONICAL_SESSION_ID,
            "bot_uuid": CANONICAL_MEMBER_BOT_ID,
        }),
        "DIRECT_DENSITY.C02": Observation(202, {"run_id": CANONICAL_RUN_ID}),
        "DIRECT_DENSITY.C03": Observation(202, {"session_id": CANONICAL_SESSION_ID}),
        "DIRECT_DENSITY.C04": Observation(202, {"bot_uuid": CANONICAL_MEMBER_BOT_ID}),
        "DIRECT_DENSITY.C05": Observation(202, {"run_id": CANONICAL_RUN_ID}),
        "DIRECT_DENSITY.C06": Observation(200, {}),
        "DIRECT_DENSITY.C07": Observation(200, {"run_id": CANONICAL_RUN_ID}),
        "DIRECT_DENSITY.C08": Observation(200, {"state": "running"}),
        "DIRECT_DENSITY.C09": Observation(200, {}),
        "DIRECT_DENSITY.C10": Observation(200, {"cancelled": True}),
        "DIRECT_DENSITY.C11": Observation(200, {"state": "cancelled"}),
        "DIRECT_DENSITY.C12": Observation(200, {}),
        "DIRECT_DENSITY.C13": Observation(200, {
            "run_id": CANONICAL_RUN_ID,
            "state": "cancelled",
            "is_terminal": True,
            "error_message": DOWNSTREAM_ERROR_MARKER,
            "version": 14,
            "response": {"content": ""},
            "completed_at_ms": 1761897600000,
        }),
        "DIRECT_DENSITY.C14": Observation(200, {"is_terminal": True}),
    }

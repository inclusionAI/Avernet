"""Driver SPI — the contract for DAG plan execution.

Static drivers walk the plan once. Dynamic drivers may invoke a
``Replanner`` between waves to extend the plan with additional nodes.

Import direction: ``spi/`` MUST NOT import from ``core/`` at runtime.
``RunResult`` / ``RunLog`` / ``RunEvent`` are defined here (not in
``core/_driver.py``) so they belong to the SPI contract. The concrete
``StaticDriver`` in ``core/_driver.py`` imports from here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core._dag import DAGPlan
    from ..core._node import NodeStatus

__all__ = ["Driver", "EventCallback", "RunEvent", "RunLog", "RunResult"]


@dataclass
class RunLog:
    """Execution log: per-node status, results, errors.

    ``results`` is a read-only view of ``plan.nodes[n].result`` — the
    single source of truth is the plan's DAGNode objects. This dict
    is populated during execution for convenience access.
    """

    statuses: dict[str, NodeStatus] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    plan: DAGPlan
    log: RunLog
    succeeded: bool
    final_output: Any = None


@dataclass
class RunEvent:
    """Structured event for non-node occurrences during a run.

    ``kind`` is one of: ``"replan_called"``, ``"replan_applied"``,
    ``"replan_rejected"``, ``"halt"``.  Delivered via the ``on_event``
    callback on ``Driver.run()`` — separate from ``on_progress`` which
    only carries per-node status updates.
    """

    kind: str
    extension_count: int = 0
    nodes_added: list[str] = field(default_factory=list)
    rationale: str = ""
    halt_reason: str | None = None


ProgressCallback = Callable[[str, "NodeStatus", float], None]
EventCallback = Callable[[RunEvent], None]


class Driver(ABC):
    """Driver SPI: execute a ``DAGPlan`` and return a ``RunResult``.

    The optional ``on_progress`` and ``on_event`` callbacks are
    passed at run time (not constructor time) so the same driver
    instance can be reused across runs with different observers
    (CLI printer vs SSE streamer vs test). When provided, they
    override whatever was passed to ``__init__``.
    """

    @abstractmethod
    def run(
        self,
        plan: DAGPlan,
        *,
        on_progress: ProgressCallback | None = None,
        on_event: EventCallback | None = None,
        max_workers: int | None = None,
    ) -> RunResult:
        raise NotImplementedError
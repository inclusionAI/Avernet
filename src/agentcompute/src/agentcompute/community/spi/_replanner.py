"""Replanner SPI — the contract for dynamic plan extension.

Separate from ``Planner`` (Oracle review B9/S1): a static planner produces
the initial plan; a replanner extends an in-flight plan based on execution
results. ``DynamicDriver`` depends on ``Optional[Replanner]`` — ``None``
means fully static (no mid-run replanning).

Import direction: ``spi/`` MUST NOT import from ``core/`` at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core._dag import DAGPlan
    from ..core._node import DAGNode

__all__ = ["HaltReason", "PlanExtension", "Replanner", "ReplanError"]


class ReplanError(RuntimeError):
    """Raised when a replanner call fails (LLM error, timeout, invalid output)."""


class HaltReason(StrEnum):
    DONE = "done"
    ABORT = "abort"
    DRIFT = "drift"


@dataclass
class PlanExtension:
    """Structured delta returned by the replanner.

    ``halt_reason`` is ``None`` to continue, or one of:
    - ``DONE``: goal achieved early → skip remaining PENDING, succeeded=True
    - ``ABORT``: goal cannot be achieved → skip remaining, succeeded=False
    - ``DRIFT``: goal drifted too far → skip remaining, succeeded=False
    """

    extensions: list[DAGNode] = field(default_factory=list)
    new_edges: list[tuple[str, str]] = field(default_factory=list)
    halt_reason: HaltReason | None = None
    rationale: str = ""


class Replanner(ABC):
    """Dynamic replanner SPI: extend an in-flight plan based on results.

    Returns a ``PlanExtension`` delta (not a full plan). The driver merges
    it transactionally — a failed merge leaves the plan unchanged.

    ``agents`` is a list of agent NAME strings (matching
    ``DAGPlan.available_agents``) so the SPI stays compatible with the
    plan-stored roster without needing full ``AgentSpec`` resolution.
    """

    @abstractmethod
    def extend(
        self,
        goal: str,
        agents: list[str],
        prior: DAGPlan,
        results: dict[str, Any],
        errors: dict[str, str],
    ) -> PlanExtension:
        raise NotImplementedError
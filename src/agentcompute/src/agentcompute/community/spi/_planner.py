"""Planner SPI — the contract for goal → DAGPlan planning.

Static planners produce a plan from a goal + agent roster. Dynamic
replanning is handled by the separate ``Replanner`` SPI (see
``spi/replanner.py``). This interface is intentionally minimal.

Import direction: ``spi/`` MUST NOT import from ``core/`` at runtime.
Type hints referencing core types use ``TYPE_CHECKING`` guards so
the hexagonal architecture (spi is the lowest layer) is preserved.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core._dag import DAGPlan
    from ._agent import AgentSpec

__all__ = ["PlanError", "Planner"]


class PlanError(RuntimeError):
    """Raised when a planner cannot produce a valid plan."""


class Planner(ABC):
    """Static planner SPI: produce a ``DAGPlan`` from a goal + agents.

    Implementations populate ``plan.goal`` and ``plan.available_agents``
    so downstream consumers (including dynamic replanners) have access
    without needing the original arguments re-passed.
    """

    @abstractmethod
    def plan(self, goal: str, agents: list[AgentSpec]) -> DAGPlan:
        """Decompose ``goal`` into a DAG of nodes assignable to ``agents``."""
        raise NotImplementedError

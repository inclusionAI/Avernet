"""Backward-compat shim.

``StaticPlanner`` and its business logic have moved to
``agentcompute.community.plugins._static_planner`` (hexagonal architecture:
concrete impls live in plugins, not core). This module re-exports the names
that legacy imports (``from agentcompute.community.core._planner import
PlanError, Planner``) continue to resolve.
"""

from ..plugins._static_planner import StaticPlanner
from ..spi._planner import PlanError

Planner = StaticPlanner

__all__ = ["PlanError", "Planner", "StaticPlanner"]
"""Backward-compat aliases — old names still importable after the SPI split.

Pre-SPI code referenced ``Planner``, ``Driver``, ``RunLog``,
``RunResult``, ``RunEvent``, ``PlanError``, ``ReplanError``,
``HaltReason``, ``PlanExtension`` from various modules. After the SPI
split, types moved:

  - Planner ABC + PlanError        → ``spi.planner``
  - Replanner ABC + ReplanError    → ``spi.replanner``
  - Driver ABC + Run* types        → ``spi.driver``
  - StaticPlanner concrete class   → ``plugins.static_planner``
  - StaticDriver concrete class    → ``plugins.static_driver``
  - DynamicReplanner concrete      → ``plugins.dynamic_replanner``
  - DynamicDriver concrete         → ``plugins.dynamic_driver``

For backward compat, ``Planner`` and ``Driver`` here are bound to the
CONCRETE ``StaticPlanner`` / ``StaticDriver`` classes that pre-SPI code
expected. The ABC contracts remain accessible via
``spi.planner.Planner`` and ``spi.driver.Driver`` respectively.
"""

from .plugins._static_driver import StaticDriver
from .plugins._static_planner import StaticPlanner
from .spi._driver import (
    EventCallback,
    ProgressCallback,
    RunEvent,
    RunLog,
    RunResult,
)
from .spi._planner import PlanError
from .spi._replanner import (
    HaltReason,
    PlanExtension,
    ReplanError,
    Replanner,
)

Driver = StaticDriver
Planner = StaticPlanner

__all__ = [
    "Driver",
    "EventCallback",
    "HaltReason",
    "PlanError",
    "PlanExtension",
    "Planner",
    "ProgressCallback",
    "ReplanError",
    "Replanner",
    "RunEvent",
    "RunLog",
    "RunResult",
    "StaticDriver",
    "StaticPlanner",
]
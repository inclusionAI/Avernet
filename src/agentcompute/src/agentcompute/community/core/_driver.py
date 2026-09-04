"""Backward-compat shim.

``StaticDriver`` and its business logic have moved to
``agentcompute.community.plugins._static_driver`` (hexagonal architecture:
concrete impls live in plugins, not core). This module re-exports the names
that legacy imports continue to resolve, including ``RunLog`` / ``RunResult``
/ ``RunEvent`` now defined in ``spi._driver`` (SPI contract).
"""

from ..plugins._static_driver import StaticDriver
from ..spi._driver import RunEvent, RunLog, RunResult

Driver = StaticDriver

__all__ = ["Driver", "RunEvent", "RunLog", "RunResult", "StaticDriver"]
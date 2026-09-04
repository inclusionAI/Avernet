"""Spark-style driver/executor demo over agent executors.

Mirrors the ocb-public BAAS hexagonal architecture in a single ``community``
package:

- ``spi`` — SPI contracts (leaf level).
- ``core`` — domain logic (DAG, driver, planner, repository, reporter).
- ``plugins`` — concrete implementations (LLM providers, agents, database,
  logger, tracer, runner) registered at import time.
- ``adapters`` — IO ports (HTTP).
- ``bootstrap`` — DI container + config.

Plugin selection is config-driven via the DI container.
"""

from ._cli import main, run

__all__ = ["__version__", "main", "run"]

__version__ = "0.1.0"

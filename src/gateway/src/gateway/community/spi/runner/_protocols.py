"""AppRunnerPlugin Protocol — application bootstrap/startup abstraction.

Implementations encapsulate framework-specific startup (SOFA runner vs a
plain ``uvicorn.run``) so ``main.py`` has zero ``sofapy_base`` imports.

Implementations:
- ``BareAppRunnerPlugin`` (community): calls ``uvicorn.run()`` directly.
- ``SofaAppRunnerPlugin`` (enterprise): delegates to ``sofapy_base.runner.run()``.
"""

from __future__ import annotations

from typing import Protocol


class AppRunnerPlugin(Protocol):
    """Plugin protocol for application bootstrap and startup."""

    def run(self, config_path: str | None = None) -> None:
        """Start the application.

        Args:
            config_path: Optional path to the configuration directory or file.
        """
        ...

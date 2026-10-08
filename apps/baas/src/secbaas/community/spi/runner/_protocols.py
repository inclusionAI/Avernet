"""AppRunnerPlugin Protocol — application bootstrap/runner abstraction.

Implementations encapsulate framework-specific startup (SOFA runner vs
uvicorn runner) so ``main.py`` has zero ``sofapy_base`` imports.
"""

from __future__ import annotations

from typing import Protocol


class AppRunnerPlugin(Protocol):
    """Plugin protocol for application bootstrap and startup.

    Implementations:
    - SofaAppRunnerPlugin: delegates to ``sofapy_base.runner.run()``
    - BareAppRunnerPlugin: calls ``uvicorn.run()`` directly
    """

    def run(self, config_path: str | None = None) -> None:
        """Start the application.

        Args:
            config_path: Optional path to the configuration directory.
        """
        ...

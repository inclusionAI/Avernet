"""Bare app runner — starts the app with ``uvicorn`` directly (no SOFA)."""

from __future__ import annotations

import os

from gateway.community.config import ConfigLoader
from gateway.community.spi.runner import AppRunnerPlugin


class BareAppRunnerPlugin(AppRunnerPlugin):
    """Application runner that uses uvicorn directly (bare mode)."""

    def run(self, config_path: str | None = None) -> None:
        os.environ["GATEWAY_RUN_MODE"] = "bare"

        if config_path:
            os.environ["GATEWAY_CONFIG_PATH"] = config_path

        config = ConfigLoader.load()
        port = config.module_config.web.port if config.module_config.web else 8888

        # Logging is configured early so messages emitted before the
        # lifespan's configure_logging() call are visible. Plugin selection
        # (bare vs sofa) happens via entry points, keyed off GATEWAY_RUN_MODE.
        workers = config.workers if config.workers > 1 else 1

        import uvicorn

        uvicorn.run(
            app="gateway.community.adapters.web.app:app",
            host="0.0.0.0",
            port=port,
            workers=workers,
            factory=False,
        )

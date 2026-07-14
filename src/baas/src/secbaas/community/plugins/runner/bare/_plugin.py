"""Bare app runner — starts the app with ``uvicorn`` directly."""

from __future__ import annotations

import os

from secbaas.community.config import ConfigLoader
from secbaas.community.spi.runner import AppRunnerPlugin


class BareAppRunnerPlugin(AppRunnerPlugin):
    """Application runner that uses uvicorn directly (bare mode)."""

    def run(self, config_path: str | None = None) -> None:
        os.environ["SECBAAS_RUN_MODE"] = "bare"

        if config_path:
            os.environ["SOFAPY_CONFIG_PATH"] = config_path

        config = ConfigLoader.load()
        port = config.module_config.web.port if config.module_config.web else 8888

        # Logging is configured early so messages emitted before the lifespan's
        # configure_logging() call are visible. Plugin selection (Baas vs Sofa)
        # happens at module level in app.py, keyed off SECBAAS_RUN_MODE.
        workers = config.workers if config.workers > 1 else 1
        import uvicorn

        uvicorn.run(
            app="secbaas.community.adapters.web.app:app",
            host="0.0.0.0",
            port=port,
            workers=workers,
            factory=False,
        )

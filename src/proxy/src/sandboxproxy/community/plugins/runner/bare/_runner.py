"""Bare app runner — starts the app with ``uvicorn`` directly (no SOFA)."""

from __future__ import annotations

import os

from sandboxproxy.community.logger import get_logger

logger = get_logger("runner-bare")


class BareAppRunnerPlugin:
    """Application runner that uses uvicorn directly (bare mode)."""

    def run(self, config_path: str | None = None) -> None:
        os.environ["SANDBOXPROXY_RUN_MODE"] = "bare"

        if config_path:
            os.environ["SANDBOXPROXY_CONFIG_PATH"] = config_path

        from sandboxproxy.community.config import ConfigLoader

        config = ConfigLoader.load()
        port = config.module_config.web.port if config.module_config.web else 8888
        workers = config.workers if config.workers > 1 else 1

        logger.info("starting bare runner: port=%d workers=%d", port, workers)

        import uvicorn

        uvicorn.run(
            app="sandboxproxy.community.adapters.web.app:app",
            host="0.0.0.0",
            port=port,
            workers=workers,
            factory=False,
        )

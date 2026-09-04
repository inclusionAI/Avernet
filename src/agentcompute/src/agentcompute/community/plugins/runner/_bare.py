"""Bare app runner — boots uvicorn directly (no framework runner)."""

from __future__ import annotations

import os

from ...spi._runner import AppRunnerPlugin

__all__ = ["BareAppRunnerPlugin"]


class BareAppRunnerPlugin(AppRunnerPlugin):
    """Application runner that delegates to ``uvicorn.run``."""

    def run(self, config_path: str | None = None) -> None:
        os.environ["AGENT_COMPUTE_RUN_MODE"] = "bare"

        import uvicorn

        from agentcompute.community.adapters.http import create_app

        from ...bootstrap._yaml_config import load_config

        path = config_path or "config/application.yaml"
        app_config = load_config(path)
        app = create_app(path)
        uvicorn.run(
            app,
            host=os.environ.get("AGENT_COMPUTE_HOST") or app_config.get("app.host", "0.0.0.0"),
            port=int(os.environ.get("AGENT_COMPUTE_PORT") or app_config.get("app.port", 8000)),
        )

"""Bare tracer plugin — no-op tracer (no sidecar / telemetry sink)."""

from __future__ import annotations

from typing import Any


class BareTracerPlugin:
    """No-op tracer: records nothing, installs no middleware."""

    def setup(self, app_name: str) -> None:
        return None

    def install_middleware(self, app: Any) -> None:
        return None

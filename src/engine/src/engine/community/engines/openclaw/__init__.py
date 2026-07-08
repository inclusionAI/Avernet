"""OpenClaw engine implementation.

OpenClaw is the reference engine for the heterogeneous-engine architecture. It
communicates with a remote OpenClaw Gateway over WebSocket, multiplexing
session / chat / cron traffic over a single client connection shared across
the engine's plugins.
"""
from __future__ import annotations

from engine.community.core.engine.registry import DEFAULT_REGISTRY
from engine.community.engines.openclaw.engine import OpenClawEngine

# Register with the default registry on import so EngineManager can look up
# OpenClawEngine by name. Re-importing this module is a no-op (register() is
# idempotent for the same class).
DEFAULT_REGISTRY.register(OpenClawEngine)

__all__ = ["OpenClawEngine"]

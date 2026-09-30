"""ProcessModule — binds the settings the engine runtime needs.

Two of these are genuinely process-wide singletons; the third is per-engine.

- ``MCPTokenSettings`` — process-wide, straight off ``config.mcp_token``. Singleton.
- ``DingTalkSettings`` — process-wide; ``config.dingtalk``, **may be None** when
  DingTalk credentials are absent (matches the ``load_dingtalk_settings()``
  Optional semantics). Singleton.
- ``Callable[[str], EngineProcessSettings]`` — a **per-engine resolver**, NOT a
  baked instance. Process settings describe how to start/stop a specific
  engine's subprocess, and the active engine can change at runtime via
  ``EngineManager.switch()`` — so binding one engine's settings as a singleton
  would be stale after a switch. Consumers inject the resolver and call it with
  the engine name they actually need (the manager's current engine at
  process-creation time). This mirrors backend's ``Callable[[], X]`` provider
  patterns.
"""
from __future__ import annotations

from typing import Callable

from injector import Module, inject, provider, singleton

from engine.community.config import (
    DingTalkSettings,
    EngineConfig,
    EngineProcessSettings,
    MCPTokenSettings,
    load_engine_process_settings,
)


class ProcessModule(Module):
    """Production bindings for the settings the runtime consumes."""

    @singleton
    @provider
    @inject
    def mcp_token_settings(self, config: EngineConfig) -> MCPTokenSettings:
        return config.mcp_token

    @singleton
    @provider
    @inject
    def dingtalk_settings(self, config: EngineConfig) -> DingTalkSettings:
        # None when unconfigured — preserves legacy Optional behaviour.
        return config.dingtalk  # type: ignore[return-value]

    @singleton
    @provider
    def engine_process_settings_resolver(
        self,
    ) -> Callable[[str], EngineProcessSettings]:
        """Resolve `EngineProcessSettings` by engine name, on demand.

        Per-engine (not a singleton instance) so switching engines picks up the
        right subprocess config. Backed by the pure
        `load_engine_process_settings` reader (engine.json + env, no module
        global).
        """
        return load_engine_process_settings

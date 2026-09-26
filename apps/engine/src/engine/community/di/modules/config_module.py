"""ConfigModule — binds the immutable :class:`EngineConfig` singleton.

Single source of truth for engine configuration in the DI graph. The provider
calls :func:`engine.community.config.load_engine_config` once (singleton scope), so the
file/env read happens at first resolution and the resulting frozen snapshot is
shared by every downstream consumer.

Local boots / tests override this binding by layering
``TestingConfigModule`` (Task 23) via ``extra_modules``.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.config import EngineConfig, load_engine_config


class ConfigModule(Module):
    """Production binding for :class:`EngineConfig`."""

    @singleton
    @provider
    def engine_config(self) -> EngineConfig:
        return load_engine_config()

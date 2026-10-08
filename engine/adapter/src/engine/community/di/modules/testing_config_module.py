"""TestingConfigModule — override :class:`EngineConfig` for a single test.

Layered *after* :class:`ConfigModule` in ``build_injector(extra_modules=...)``;
injector applies modules in order and the later binding wins, so this replaces
the production ``load_engine_config()`` binding with a test-supplied snapshot.
Tests use it (via the ``test_injector`` fixture) instead of mutating the
module-global config — the global mutators (``set_chat_engine`` etc.) are
removed in Group G.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.config import EngineConfig


class TestingConfigModule(Module):
    """Bind a caller-supplied :class:`EngineConfig` (overrides ConfigModule)."""

    def __init__(self, config: EngineConfig) -> None:
        self._config = config

    @singleton
    @provider
    def engine_config(self) -> EngineConfig:
        return self._config

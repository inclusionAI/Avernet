"""Tests for the DI composition root (``engine.community.di``).

Grows with the container: Task 20 binds ``EngineConfig``; Task 21 adds the
settings dataclasses + ``EngineManager``.
"""
from __future__ import annotations

from typing import Callable

from injector import Injector

from engine.community.config import (
    EngineConfig,
    EngineProcessSettings,
    MCPTokenSettings,
)
from engine.community.di.container import build_injector
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
from engine.community.manager import EngineManager


def test_build_injector_returns_injector():
    inj = build_injector(config=RuntimeConfig.detect())
    assert isinstance(inj, Injector)


def test_engine_config_resolves_as_singleton():
    inj = build_injector(config=RuntimeConfig(runtime=RuntimeMode.PROD))
    cfg = inj.get(EngineConfig)
    assert isinstance(cfg, EngineConfig)
    assert inj.get(EngineConfig) is cfg  # singleton scope


def test_engine_config_has_expected_fields():
    cfg = build_injector(config=RuntimeConfig.detect()).get(EngineConfig)
    assert isinstance(cfg.default_engine, str) and cfg.default_engine
    assert isinstance(cfg.max_connections, int)
    assert cfg.mcp_token is not None
    assert cfg.mcporter_config_path is not None
    # dingtalk may be None when creds are absent — just assert the attr exists
    assert hasattr(cfg, "dingtalk")


def test_settings_dataclasses_resolve():
    inj = build_injector(config=RuntimeConfig.detect())
    assert isinstance(inj.get(MCPTokenSettings), MCPTokenSettings)
    # DingTalkSettings resolves to either an instance or None (unconfigured).
    from engine.community.config import DingTalkSettings

    assert inj.get(DingTalkSettings) is None or isinstance(
        inj.get(DingTalkSettings), DingTalkSettings
    )


def test_engine_process_settings_resolver_is_per_engine():
    inj = build_injector(config=RuntimeConfig.detect())
    resolve = inj.get(Callable[[str], EngineProcessSettings])
    settings = resolve("openclaw")
    assert isinstance(settings, EngineProcessSettings)
    assert settings.engine == "openclaw"
    # Different engine name yields its own settings object.
    assert resolve("aicoding").engine == "aicoding"


def test_engine_manager_resolves_with_default_engine():
    cfg_inj = build_injector(config=RuntimeConfig.detect())
    cfg = cfg_inj.get(EngineConfig)
    mgr = cfg_inj.get(EngineManager)
    assert isinstance(mgr, EngineManager)
    # Manager boots from the config default; live engine is then manager-owned.
    assert mgr.engine == cfg.default_engine
    assert cfg_inj.get(EngineManager) is mgr  # singleton scope


def test_get_instance_resolves_from_bound_injector():
    """Composition root: once an injector is bound, get_instance() returns the
    DI singleton; reset_instance() unbinds it (restoring the legacy path)."""
    inj = build_injector(config=RuntimeConfig.detect())
    try:
        EngineManager.bind_injector(inj)
        assert EngineManager.get_instance() is inj.get(EngineManager)
    finally:
        EngineManager.reset_instance()
    assert EngineManager._injector is None

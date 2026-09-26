"""Tests for engine workspace_root utility module."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch


def test_workspace_root_returns_env_value_when_set():
    """OPENCLAW_WORKSPACE_DIR 设了 → 返回 Path(env_value)"""
    from engine.community.plugin_api.workspace_root import workspace_root
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": "/tmp/my-bot/openclaw/workspace"}):
        result = workspace_root()
    assert result == Path("/tmp/my-bot/openclaw/workspace")


def test_workspace_root_falls_back_to_home_when_unset():
    """env 未设 → fallback 到 Path.home() / .openclaw / workspace"""
    from engine.community.plugin_api.workspace_root import workspace_root
    env_without = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    with patch.dict(os.environ, env_without, clear=True):
        result = workspace_root()
    assert result == Path.home() / ".openclaw" / "workspace"


def test_workspace_root_falls_back_when_env_empty_string():
    """env 是空字符串 → fallback (因为 os.environ.get 返 '' 是 falsy)"""
    from engine.community.plugin_api.workspace_root import workspace_root
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": ""}):
        result = workspace_root()
    assert result == Path.home() / ".openclaw" / "workspace"


def test_workspace_root_strict_returns_path_when_set():
    """strict 版:env 设了 → 返回 Path"""
    from engine.community.plugin_api.workspace_root import workspace_root_strict
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": "/tmp/my-bot/openclaw/workspace"}):
        result = workspace_root_strict()
    assert result == Path("/tmp/my-bot/openclaw/workspace")


def test_workspace_root_strict_returns_none_when_unset():
    """strict 版:env 未设 → 返回 None (不 fallback)"""
    from engine.community.plugin_api.workspace_root import workspace_root_strict
    env_without = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    with patch.dict(os.environ, env_without, clear=True):
        result = workspace_root_strict()
    assert result is None


def test_workspace_root_strict_returns_none_when_empty():
    """strict 版:env 是空字符串 → 返回 None"""
    from engine.community.plugin_api.workspace_root import workspace_root_strict
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": ""}):
        result = workspace_root_strict()
    assert result is None


def test_workspace_root_accepts_relative_path():
    """workspace_root 非 strict 版:接受相对路径 env(不校验是否绝对路径)"""
    from engine.community.plugin_api.workspace_root import workspace_root
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": "relative/workspace"}):
        result = workspace_root()
    assert result == Path("relative/workspace")

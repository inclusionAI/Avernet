"""Tests for arca local_proc _process_manager env injection (Task 6).

Spec: docs/superpowers/specs/2026-06-10-engine-per-bot-workspace-design.md §4.3.A3
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from secbaas.plugins.sandbox.arca.local_proc import _process_manager as pm


def _repo_bcn_plugin_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "src" / "plugin" / "packages" / "openclaw-channel-bcn"
        if candidate.is_dir():
            return candidate
    raise AssertionError("repo BCN plugin path not found")


@pytest.mark.skip(
    reason="depends on BCN plugin repo checkout; needs tmp_path fixture to replace ~/.openclaw fallback"
)
def test_default_bcn_plugin_path_prefers_ocb_repo_plugin(monkeypatch):
    monkeypatch.delenv("BCN_PLUGIN_PATH", raising=False)

    assert Path(pm._default_bcn_plugin_path()) == _repo_bcn_plugin_path()


def test_spawn_adapter_source_injects_workspace_dir_env():
    """_spawn_adapter 函数源码里必须包含 env['OPENCLAW_WORKSPACE_DIR'] = str(workspace_dir)"""
    src = inspect.getsource(pm.LocalProcessManager._spawn_adapter)
    assert 'env["OPENCLAW_WORKSPACE_DIR"]' in src, (
        "_spawn_adapter 应该注入 OPENCLAW_WORKSPACE_DIR 给 adapter 进程"
    )
    assert "workspace_dir" in src, "注入值应该是 workspace_dir 参数"


def test_resolve_engine_src_dir_prefers_configured_path(monkeypatch, tmp_path):
    engine_src_dir = tmp_path / "engine" / "src"
    engine_src_dir.mkdir(parents=True)
    monkeypatch.setenv("LOCAL_ENGINE_SRC_DIR", str(engine_src_dir))

    assert pm.LocalProcessManager._resolve_engine_src_dir() == engine_src_dir.resolve()


def test_resolve_engine_src_dir_rejects_missing_configured_path(monkeypatch, tmp_path):
    missing_engine_src_dir = tmp_path / "missing" / "engine" / "src"
    monkeypatch.setenv("LOCAL_ENGINE_SRC_DIR", str(missing_engine_src_dir))

    with pytest.raises(pm.DeviceAllocateError) as exc_info:
        pm.LocalProcessManager._resolve_engine_src_dir()

    assert "Configured LOCAL_ENGINE_SRC_DIR does not exist" in str(exc_info.value)
    assert str(missing_engine_src_dir) in str(exc_info.value)


def test_resolve_engine_src_dir_falls_back_to_repo_layout(monkeypatch):
    monkeypatch.delenv("LOCAL_ENGINE_SRC_DIR", raising=False)

    engine_src_dir = pm.LocalProcessManager._resolve_engine_src_dir()

    assert engine_src_dir.name == "src"
    assert engine_src_dir.parent.name == "engine"
    assert (engine_src_dir / "engine" / "community" / "api" / "app.py").is_file()

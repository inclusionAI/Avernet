"""Port-impl tests for OpenClawPluginImpl.get_default_config (transport — local JSON).

Preserves the legacy engines/openclaw/tests/test_default_config.py coverage: the
env-resolved path, the {"path","config"} primitive dict, and the
FileNotFoundError / IsADirectoryError / ValueError(bad-json / non-object) cases.
The DefaultConfigResult DTO build is covered by core/adapters/openclaw/tests.
"""
from __future__ import annotations

import json

import pytest

from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl

_ENV = "OPENCLAW_DEFAULT_CONFIG_PATH"


async def test_reads_and_returns_path_and_config(tmp_path, monkeypatch):
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"model": "gpt-4", "nested": {"a": 1}}), encoding="utf-8")
    monkeypatch.setenv(_ENV, str(cfg))
    out = await OpenClawPluginImpl().get_default_config()
    assert out["path"] == str(cfg)
    assert out["config"] == {"model": "gpt-4", "nested": {"a": 1}}


async def test_missing_file_raises_filenotfound(tmp_path, monkeypatch):
    monkeypatch.setenv(_ENV, str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        await OpenClawPluginImpl().get_default_config()


async def test_directory_path_raises_isadirectory(tmp_path, monkeypatch):
    monkeypatch.setenv(_ENV, str(tmp_path))  # a directory, not a file
    with pytest.raises(IsADirectoryError):
        await OpenClawPluginImpl().get_default_config()


async def test_bad_json_raises_valueerror(tmp_path, monkeypatch):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv(_ENV, str(cfg))
    with pytest.raises(ValueError):
        await OpenClawPluginImpl().get_default_config()


async def test_non_object_top_level_raises_valueerror(tmp_path, monkeypatch):
    cfg = tmp_path / "list.json"
    cfg.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    monkeypatch.setenv(_ENV, str(cfg))
    with pytest.raises(ValueError):
        await OpenClawPluginImpl().get_default_config()

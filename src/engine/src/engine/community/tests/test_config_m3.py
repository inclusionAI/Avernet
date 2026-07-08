"""Unit tests for config.py M3 changes.

Covers:
  - `EngineConfig` resolution via `load_engine_config()`: reflects env, fresh
    loads re-resolve, instances are immutable (no module-global caching)
  - `_resolve_default_engine` precedence: env var → engine.json → "openclaw"
  - `_read_process_block` dual-schema reader (new §18.1 vs legacy)
  - `load_engine_process_settings` works for any engine name (no allowlist)
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from engine.community.config import (
    _read_process_block,
    _resolve_default_engine,
    load_engine_config,
    load_engine_process_settings,
)


@pytest.fixture(autouse=True)
def _clean_engine_env(monkeypatch):
    """Start each test with no CHAT_ENGINE in the environment."""
    monkeypatch.delenv("CHAT_ENGINE", raising=False)


def _write_json(path: Path, data: dict) -> Path:
    config_file = path / "engine.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")
    return config_file


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_default_engine
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveDefaultEngine:
    def test_env_var_wins_over_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHAT_ENGINE", "from-env")
        cfg = _write_json(tmp_path, {"engine": {"default": "from-json"}})
        assert _resolve_default_engine(cfg) == "from-env"

    def test_falls_back_to_json_engine_default(self, tmp_path):
        cfg = _write_json(tmp_path, {"engine": {"default": "from-json"}})
        assert _resolve_default_engine(cfg) == "from-json"

    def test_falls_back_to_openclaw_when_neither_present(self, tmp_path):
        cfg = _write_json(tmp_path, {})
        assert _resolve_default_engine(cfg) == "openclaw"

    def test_missing_file_returns_default(self, tmp_path):
        # No file written
        assert _resolve_default_engine(tmp_path / "missing.json") == "openclaw"

    def test_empty_env_var_treated_as_unset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHAT_ENGINE", "   ")
        cfg = _write_json(tmp_path, {"engine": {"default": "from-json"}})
        assert _resolve_default_engine(cfg) == "from-json"

    def test_engine_default_must_be_string(self, tmp_path):
        cfg = _write_json(tmp_path, {"engine": {"default": 42}})
        assert _resolve_default_engine(cfg) == "openclaw"


# ─────────────────────────────────────────────────────────────────────────────
# load_engine_config — env-reflecting, fresh-load, immutable (no global cache)
# ─────────────────────────────────────────────────────────────────────────────


class TestEngineConfigResolution:
    def test_default_engine_reflects_env(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENGINE", "from-env")
        assert load_engine_config().default_engine == "from-env"

    def test_instance_immutable_fresh_load_reflects_env(self, monkeypatch):
        # An existing EngineConfig is a frozen snapshot — it does NOT change
        # when the environment later mutates (this is what replaces the old
        # global lazy cache). A *fresh* load() re-resolves from current env.
        monkeypatch.setenv("CHAT_ENGINE", "first")
        cfg = load_engine_config()
        assert cfg.default_engine == "first"
        monkeypatch.setenv("CHAT_ENGINE", "second")
        assert cfg.default_engine == "first"  # snapshot stable
        assert load_engine_config().default_engine == "second"  # fresh re-resolves

    def test_frozen_dataclass_rejects_mutation(self):
        cfg = load_engine_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.default_engine = "nope"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# _read_process_block — dual-schema reader
# ─────────────────────────────────────────────────────────────────────────────


class TestReadProcessBlock:
    def test_prefers_new_schema_over_legacy(self):
        data = {
            "engines": {"openclaw": {"process": {"start_cmd": ["new"]}}},
            "process": {"openclaw": {"start_cmd": ["legacy"]}},
        }
        assert _read_process_block(data, "openclaw") == {"start_cmd": ["new"]}

    def test_falls_back_to_legacy_schema(self):
        data = {"process": {"openclaw": {"start_cmd": ["legacy"]}}}
        assert _read_process_block(data, "openclaw") == {"start_cmd": ["legacy"]}

    def test_returns_empty_when_neither_schema_has_engine(self):
        data = {"engines": {"other": {"process": {}}}, "process": {"other": {}}}
        assert _read_process_block(data, "openclaw") == {}

    def test_empty_new_block_falls_through_to_legacy(self):
        # `engines.openclaw.process` is present but empty — should still fall
        # through to legacy because empty dict means "nothing useful here".
        data = {
            "engines": {"openclaw": {"process": {}}},
            "process": {"openclaw": {"start_cmd": ["legacy"]}},
        }
        assert _read_process_block(data, "openclaw") == {"start_cmd": ["legacy"]}

    def test_handles_missing_top_level_keys(self):
        assert _read_process_block({}, "openclaw") == {}


# ─────────────────────────────────────────────────────────────────────────────
# load_engine_process_settings — no allowlist, dual-schema works end-to-end
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadEngineProcessSettings:
    def test_loads_arbitrary_engine_name_from_new_schema(self, tmp_path):
        # Previously hardcoded to {"openclaw", "moltis"}; now any name works.
        cfg = _write_json(
            tmp_path,
            {
                "engines": {
                    "hermes-agent": {
                        "process": {
                            "start_cmd": ["cargo", "run", "--package", "bcs"],
                            "startup_timeout_sec": 60,
                            "healthcheck_tcp": "127.0.0.1:21000",
                        }
                    }
                }
            },
        )
        settings = load_engine_process_settings("hermes-agent", path=cfg)
        assert settings.engine == "hermes-agent"
        assert settings.start_cmd == ("cargo", "run", "--package", "bcs")
        assert settings.startup_timeout_sec == 60
        assert settings.healthcheck_tcp == "127.0.0.1:21000"
        assert settings.enabled is True

    def test_loads_legacy_schema_for_back_compat(self, tmp_path):
        cfg = _write_json(
            tmp_path,
            {
                "process": {
                    "openclaw": {
                        "start_cmd": ["sudo", "supervisorctl", "start", "openclaw"],
                        "startup_timeout_sec": 600,
                    }
                }
            },
        )
        settings = load_engine_process_settings("openclaw", path=cfg)
        assert settings.start_cmd == (
            "sudo",
            "supervisorctl",
            "start",
            "openclaw",
        )
        assert settings.startup_timeout_sec == 600

    def test_unknown_engine_yields_disabled_settings(self, tmp_path):
        # No allowlist anymore — unknown names just get empty settings,
        # which `enabled` reports as False (NoOpEngineProcess territory).
        cfg = _write_json(tmp_path, {})
        settings = load_engine_process_settings("claude-code", path=cfg)
        assert settings.engine == "claude-code"
        assert settings.start_cmd == ()
        assert settings.enabled is False

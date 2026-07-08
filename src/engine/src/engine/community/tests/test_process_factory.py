"""Unit tests for `process.create_engine_process` + `NoOpEngineProcess`.

The M3 generalization removed the hardcoded `OpenClawProcess` class and the
`if engine == "openclaw"` branch in the factory. Now `create_engine_process`
returns either:
  - `CommandEngineProcess` if a `start_cmd` is configured
  - `NoOpEngineProcess` otherwise (relay-style engines, e.g. Claude Code)

Settings are passed in directly (the DI path) — these tests construct an
`EngineProcessSettings` and hand it to `create_engine_process(..., settings=...)`
instead of mutating/patching the module-global config accessor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.community.config import EngineProcessSettings, load_engine_process_settings
from engine.community.process import (
    CommandEngineProcess,
    NoOpEngineProcess,
    create_engine_process,
)


def _write_json(path: Path, data: dict) -> Path:
    config_file = path / "engine.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")
    return config_file


def _settings(engine: str, start_cmd: tuple[str, ...] = (), **kw) -> EngineProcessSettings:
    """Build EngineProcessSettings with test defaults; override via kwargs."""
    return EngineProcessSettings(
        engine=engine,
        start_cmd=start_cmd,
        stop_cmd=kw.get("stop_cmd", ()),
        restart_cmd=kw.get("restart_cmd", ()),
        workdir=kw.get("workdir"),
        startup_timeout_sec=kw.get("startup_timeout_sec", 10.0),
        graceful_timeout_sec=kw.get("graceful_timeout_sec", 5.0),
        healthcheck_tcp=kw.get("healthcheck_tcp"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# create_engine_process — generic factory
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateEngineProcess:
    def test_returns_command_process_when_start_cmd_configured(self):
        settings = _settings(
            "openclaw", ("sudo", "supervisorctl", "start", "openclaw")
        )
        proc = create_engine_process("openclaw", settings=settings)
        assert isinstance(proc, CommandEngineProcess)

    def test_returns_noop_when_start_cmd_empty(self):
        # Relay-style engine — Claude Code's `engine.json` block has no
        # `process` key, so settings.start_cmd is () and settings.enabled is False.
        settings = _settings("claude-code")
        proc = create_engine_process("claude-code", settings=settings)
        assert isinstance(proc, NoOpEngineProcess)

    def test_no_engine_name_allowlist(self):
        # Previously hardcoded to "openclaw" — now any name works.
        settings = _settings("some-future-engine")
        proc = create_engine_process("some-future-engine", settings=settings)
        assert isinstance(proc, NoOpEngineProcess)

    def test_aicoding_returns_noop_when_cmd_empty(self):
        """aicoding 当 start_cmd 为空时返回 NoOpEngineProcess（enabled=False）。"""
        settings = _settings(
            "aicoding",
            startup_timeout_sec=30.0,
            graceful_timeout_sec=10.0,
            healthcheck_tcp="127.0.0.1:18900",
        )
        proc = create_engine_process("aicoding", settings=settings)
        assert isinstance(proc, NoOpEngineProcess)

    def test_aicoding_returns_command_process_when_cmd_configured(self):
        """填了 supervisorctl cmd 也返回 CommandEngineProcess。"""
        settings = _settings(
            "aicoding",
            ("sudo", "supervisorctl", "start", "relay"),
            stop_cmd=("sudo", "supervisorctl", "stop", "relay"),
            restart_cmd=("sudo", "supervisorctl", "restart", "relay"),
            startup_timeout_sec=30.0,
            graceful_timeout_sec=10.0,
            healthcheck_tcp="127.0.0.1:18900",
        )
        proc = create_engine_process("aicoding", settings=settings)
        assert isinstance(proc, CommandEngineProcess)

    def test_aicoding_case_insensitive(self):
        """`AiCoding` / `AICODING` 都应该归一化到 'aicoding'。"""
        settings = _settings(
            "aicoding",
            startup_timeout_sec=30.0,
            graceful_timeout_sec=10.0,
            healthcheck_tcp="127.0.0.1:18900",
        )
        # When start_cmd empty, returns NoOpEngineProcess
        assert isinstance(
            create_engine_process("AiCoding", settings=settings), NoOpEngineProcess
        )
        assert isinstance(
            create_engine_process("AICODING", settings=settings), NoOpEngineProcess
        )


# ─────────────────────────────────────────────────────────────────────────────
# NoOpEngineProcess — lifecycle is a no-op, is_running is always True
# ─────────────────────────────────────────────────────────────────────────────


class TestNoOpEngineProcess:
    @pytest.mark.asyncio
    async def test_start_succeeds_without_doing_anything(self):
        proc = NoOpEngineProcess("claude-code")
        await proc.start()  # no exception

    @pytest.mark.asyncio
    async def test_stop_succeeds_without_doing_anything(self):
        proc = NoOpEngineProcess("claude-code")
        await proc.stop()  # no exception

    @pytest.mark.asyncio
    async def test_restart_succeeds_without_doing_anything(self):
        proc = NoOpEngineProcess("claude-code")
        await proc.restart()  # no exception

    @pytest.mark.asyncio
    async def test_is_running_always_true(self):
        proc = NoOpEngineProcess("claude-code")
        assert await proc.is_running() is True

    def test_status_reports_unmanaged(self):
        proc = NoOpEngineProcess("claude-code")
        status = proc.status()
        assert status["running"] is True
        assert status["managed_process"] is False
        assert status["engine"] == "claude-code"


# ─────────────────────────────────────────────────────────────────────────────
# AiCoding engine — CommandEngineProcess with healthcheck probe
# ─────────────────────────────────────────────────────────────────────────────


class TestAiCodingEngineProcess:
    @pytest.mark.asyncio
    async def test_aicoding_returns_command_process_with_start_cmd(self):
        """aicoding 当 start_cmd 非空时返回 CommandEngineProcess。"""
        settings = _settings(
            "aicoding",
            ("sudo", "supervisorctl", "start", "relay"),
            startup_timeout_sec=30.0,
            graceful_timeout_sec=10.0,
            healthcheck_tcp="127.0.0.1:18900",
        )
        proc = create_engine_process("aicoding", settings=settings)
        assert isinstance(proc, CommandEngineProcess)

    @pytest.mark.asyncio
    async def test_aicoding_status_with_supervisor_cmd(self):
        """填了 supervisor cmd 时 status 暴露运行信息。"""
        settings = _settings(
            "aicoding",
            ("sudo", "supervisorctl", "start", "relay"),
            stop_cmd=("sudo", "supervisorctl", "stop", "relay"),
            restart_cmd=("sudo", "supervisorctl", "restart", "relay"),
            startup_timeout_sec=30.0,
            graceful_timeout_sec=10.0,
            healthcheck_tcp="127.0.0.1:18900",
        )
        proc = create_engine_process("aicoding", settings=settings)
        status = proc.status()
        # CommandEngineProcess exposes command info when process enabled
        assert status["command_enabled"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Integration — factory picks the right type from engine.json directly
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryFromEngineJson:
    def test_new_schema_with_process_block_yields_command_process(self, tmp_path):
        cfg = _write_json(
            tmp_path,
            {
                "engines": {
                    "openclaw": {
                        "process": {
                            "start_cmd": ["echo", "openclaw"],
                            "startup_timeout_sec": 1,
                        }
                    }
                }
            },
        )
        settings = load_engine_process_settings("openclaw", path=cfg)
        proc = create_engine_process("openclaw", settings=settings)
        assert isinstance(proc, CommandEngineProcess)

    def test_engine_with_no_process_block_yields_noop(self, tmp_path):
        cfg = _write_json(
            tmp_path,
            {"engines": {"claude-code": {"cli_path": "claude"}}},
        )
        settings = load_engine_process_settings("claude-code", path=cfg)
        proc = create_engine_process("claude-code", settings=settings)
        assert isinstance(proc, NoOpEngineProcess)

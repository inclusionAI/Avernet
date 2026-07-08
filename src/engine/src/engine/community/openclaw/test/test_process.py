import json
import sys
from pathlib import Path

import pytest

from engine.community.config import EngineProcessSettings, load_engine_process_settings
from engine.community.process import CommandEngineProcess


def test_load_engine_process_settings_from_file(tmp_path: Path):
    config_file = tmp_path / "engine.json"
    config_file.write_text(
        json.dumps(
            {
                "process": {
                    "moltis": {
                        "start_cmd": ["python", "-m", "http.server", "19999"],
                        "stop_cmd": ["pkill", "-f", "http.server 19999"],
                        "startup_timeout_sec": 12,
                        "graceful_timeout_sec": 7,
                        "healthcheck_tcp": "127.0.0.1:19999",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_engine_process_settings("moltis", path=config_file)
    assert settings.engine == "moltis"
    assert settings.start_cmd == ("python", "-m", "http.server", "19999")
    assert settings.stop_cmd == ("pkill", "-f", "http.server 19999")
    assert settings.startup_timeout_sec == 12
    assert settings.graceful_timeout_sec == 7
    assert settings.healthcheck_tcp == "127.0.0.1:19999"


@pytest.mark.asyncio
async def test_command_engine_process_start_stop():
    settings = EngineProcessSettings(
        engine="openclaw",
        start_cmd=(sys.executable, "-c", "import time; time.sleep(30)"),
        stop_cmd=(),
        restart_cmd=(),
        workdir=None,
        startup_timeout_sec=3,
        graceful_timeout_sec=1,
        healthcheck_tcp=None,
    )
    process = CommandEngineProcess(settings)

    await process.start()
    assert await process.is_running() is True
    status = process.status()
    assert status["running"] is True
    assert status["pid"] is not None

    await process.stop()
    assert await process.is_running() is False


@pytest.mark.asyncio
async def test_command_engine_process_disabled_mode():
    settings = EngineProcessSettings(
        engine="moltis",
        start_cmd=(),
        stop_cmd=(),
        restart_cmd=(),
        workdir=None,
        startup_timeout_sec=3,
        graceful_timeout_sec=1,
        healthcheck_tcp=None,
    )
    process = CommandEngineProcess(settings)
    await process.start()
    assert await process.is_running() is False
    assert process.status()["command_enabled"] is False


@pytest.mark.asyncio
async def test_command_engine_process_external_health_running(monkeypatch):
    settings = EngineProcessSettings(
        engine="moltis",
        start_cmd=(sys.executable, "-c", "import time; time.sleep(30)"),
        stop_cmd=(),
        restart_cmd=(),
        workdir=None,
        startup_timeout_sec=3,
        graceful_timeout_sec=1,
        healthcheck_tcp="127.0.0.1:20001",
    )
    process = CommandEngineProcess(settings)

    async def fake_health(_: str) -> bool:
        return True

    monkeypatch.setattr(process, "_check_tcp_health", fake_health)
    await process.start()
    assert await process.is_running() is True
    status = process.status()
    assert status["managed_process"] is False
    assert status["pid"] is None


@pytest.mark.asyncio
async def test_command_engine_process_http_health_preferred(monkeypatch):
    settings = EngineProcessSettings(
        engine="openclaw",
        start_cmd=(sys.executable, "-c", "import time; time.sleep(30)"),
        stop_cmd=(),
        restart_cmd=(),
        workdir=None,
        startup_timeout_sec=3,
        graceful_timeout_sec=1,
        healthcheck_tcp="127.0.0.1:18789",
        healthcheck_http="http://127.0.0.1:18789/healthz",
    )
    process = CommandEngineProcess(settings)

    calls = {"http": 0, "tcp": 0}

    async def fake_http(_: str) -> bool:
        calls["http"] += 1
        return True

    async def fake_tcp(_: str) -> bool:
        calls["tcp"] += 1
        return True

    monkeypatch.setattr(process, "_check_http_health", fake_http)
    monkeypatch.setattr(process, "_check_tcp_health", fake_tcp)
    await process.start()
    assert await process.is_running() is True
    assert calls["http"] > 0
    assert calls["tcp"] == 0


def test_load_engine_process_settings_http_healthcheck(tmp_path: Path):
    config_file = tmp_path / "engine.json"
    config_file.write_text(
        json.dumps(
            {
                "engines": {
                    "openclaw": {
                        "process": {
                            "start_cmd": ["echo", "hi"],
                            "healthcheck_tcp": "127.0.0.1:18789",
                            "healthcheck_http": "http://127.0.0.1:18789/healthz",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_engine_process_settings("openclaw", path=config_file)
    assert settings.healthcheck_tcp == "127.0.0.1:18789"
    assert settings.healthcheck_http == "http://127.0.0.1:18789/healthz"


@pytest.mark.asyncio
async def test_command_engine_process_stop_cmd_when_unmanaged(monkeypatch):
    settings = EngineProcessSettings(
        engine="openclaw",
        start_cmd=(sys.executable, "-c", "import time; time.sleep(30)"),
        stop_cmd=("echo", "stop"),
        restart_cmd=(),
        workdir=None,
        startup_timeout_sec=3,
        graceful_timeout_sec=1,
        healthcheck_tcp=None,
    )
    process = CommandEngineProcess(settings)
    called = {"stop_cmd": False}

    async def fake_run_stop_cmd() -> None:
        called["stop_cmd"] = True

    monkeypatch.setattr(process, "_run_stop_cmd", fake_run_stop_cmd)
    await process.stop()
    assert called["stop_cmd"] is True

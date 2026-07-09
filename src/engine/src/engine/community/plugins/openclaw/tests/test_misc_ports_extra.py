from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.kernel.frames import ResponseFrame
from engine.community.plugins.openclaw._default_config import _DefaultConfigPortMixin
from engine.community.plugins.openclaw._relay import _RelayPortMixin
from engine.community.plugins.openclaw import web_shell
from engine.community.plugins.openclaw.web_shell import OpenClawWebShellSession, _WebShellPortMixin


class DefaultConfigPort(_DefaultConfigPortMixin):
    pass


@pytest.mark.asyncio
async def test_default_config_reads_env_path_and_rejects_bad_inputs(tmp_path, monkeypatch):
    port = DefaultConfigPort()

    good = tmp_path / "openclaw.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_DEFAULT_CONFIG_PATH", str(good))
    assert port._resolve_config_path() == good
    assert await port.get_default_config() == {"path": str(good), "config": {"a": 1}}

    missing = tmp_path / "missing.json"
    monkeypatch.setenv("OPENCLAW_DEFAULT_CONFIG_PATH", str(missing))
    with pytest.raises(FileNotFoundError):
        await port.get_default_config()

    monkeypatch.setenv("OPENCLAW_DEFAULT_CONFIG_PATH", str(tmp_path))
    with pytest.raises(IsADirectoryError):
        await port.get_default_config()

    bad_json = tmp_path / "bad.json"
    bad_json.write_text('{bad', encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_DEFAULT_CONFIG_PATH", str(bad_json))
    with pytest.raises(ValueError, match="JSON"):
        await port.get_default_config()

    array_json = tmp_path / "array.json"
    array_json.write_text('[1, 2]', encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_DEFAULT_CONFIG_PATH", str(array_json))
    with pytest.raises(ValueError, match="object"):
        await port.get_default_config()


class RelayPort(_RelayPortMixin):
    def __init__(self):
        self.client = SimpleNamespace(
            send_request_with_id=AsyncMock(return_value=ResponseFrame.ok_response("r1", {"ok": True})),
            send_raw_frame=AsyncMock(),
        )
        self.calls = []

    async def _pooled_client(self, token=None):
        self.calls.append(token)
        return self.client


@pytest.mark.asyncio
async def test_relay_port_forwards_request_and_raw_frame():
    port = RelayPort()

    response = await port.forward_request("r1", "method.x", {"p": 1}, token="tok", timeout=7)
    assert response.ok is True
    assert port.calls == ["tok"]
    port.client.send_request_with_id.assert_awaited_once_with(
        request_id="r1",
        method="method.x",
        params={"p": 1},
        timeout=7,
    )

    await port.forward_raw_frame({"type": "event"}, token="tok2")
    assert port.calls == ["tok", "tok2"]
    port.client.send_raw_frame.assert_awaited_once_with({"type": "event"})


class WebShellPort(_WebShellPortMixin):
    pass


def test_web_shell_token_and_env(monkeypatch):
    port = WebShellPort()
    monkeypatch.delenv("DEBUG_TOKEN", raising=False)
    monkeypatch.delenv("SHELL_USER", raising=False)
    assert port._debug_token() == ""
    assert port._shell_user() == "admin"
    assert port.check_token("anything") is True

    monkeypatch.setenv("DEBUG_TOKEN", "secret")
    monkeypatch.setenv("SHELL_USER", "nobody")
    assert port._debug_token() == "secret"
    assert port._shell_user() == "nobody"
    assert port.check_token("secret") is True
    assert port.check_token("wrong") is False


@pytest.mark.asyncio
async def test_web_shell_open_session_uses_shell_user(monkeypatch):
    port = WebShellPort()
    monkeypatch.setenv("SHELL_USER", "tester")
    monkeypatch.setattr(web_shell, "_open_shell", lambda user: (123, 456))

    session = await port.open_session()

    assert isinstance(session, OpenClawWebShellSession)
    assert session._master_fd == 123
    assert session._child_pid == 456


@pytest.mark.asyncio
async def test_web_shell_session_closed_and_oserror_branches(monkeypatch):
    session = OpenClawWebShellSession(master_fd=10, child_pid=20)

    async def fake_run_in_executor(executor, func):
        raise OSError("read failed")

    loop = SimpleNamespace(run_in_executor=fake_run_in_executor)
    monkeypatch.setattr(web_shell.asyncio, "get_running_loop", lambda: loop)
    assert await session.read() == b""

    monkeypatch.setattr(web_shell.os, "write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write")))
    session.write(b"x")

    monkeypatch.setattr(web_shell, "_set_winsize", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("resize")))
    session.resize(80, 24)

    monkeypatch.setattr(web_shell.os, "kill", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("kill")))
    monkeypatch.setattr(web_shell.os, "close", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("close")))
    session.close()
    session.close()
    assert session._closed is True
    assert session._master_fd is None
    assert session._child_pid is None

    closed = OpenClawWebShellSession(master_fd=1, child_pid=2)
    closed._master_fd = None
    closed._child_pid = None
    assert await closed.read() == b""
    closed.write(b"ignored")
    closed.resize(1, 1)
    closed.close()

from __future__ import annotations

import asyncio

import pytest

from engine.community.core.bash.base import BaseBashService


class _FakeProc:
    returncode = None

    def __init__(self) -> None:
        self.communicate_calls = 0
        self.kill_calls = 0

    async def communicate(self):  # noqa: ANN202
        self.communicate_calls += 1
        return b"", b""

    def kill(self) -> None:
        self.kill_calls += 1
        raise ProcessLookupError


async def test_exec_timeout_ignores_process_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc()

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return proc

    async def fake_wait_for(awaitable, timeout):  # noqa: ANN001, ANN202
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await BaseBashService().exec("sleep 10", "/home/admin/work", timeout=1)

    assert result.exit_code == -1
    assert result.stderr == "command timed out after 1s"
    assert proc.kill_calls == 1
    assert proc.communicate_calls == 1

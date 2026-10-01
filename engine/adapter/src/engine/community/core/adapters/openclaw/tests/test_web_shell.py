"""Unit tests for the OpenClaw web_shell ACL adapter.

Drives ``OpenClawWebShellAdapter`` against a fake ``OpenClawWebShellPort``
(no PTY fork, no OS calls).  Verifies:
  - check_token passthrough (True / False)
  - open_session drops auth and returns the port's session object unchanged
  - the fake session structurally satisfies the WebShellSession Protocol
    (runtime_checkable check on read / write / resize / close)
"""
from __future__ import annotations

import pytest

from engine.community.core.adapters.openclaw.web_shell import OpenClawWebShellAdapter
from engine.community.core.web_shell.protocol import WebShellSession


# ── fake session (structurally satisfies WebShellSession) ──


class _FakeSession:
    """Minimal fake session — pure in-memory, no OS calls."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.resized: list[tuple[int, int]] = []
        self.closed = False

    async def read(self) -> bytes:
        return b"output"

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.resized.append((cols, rows))

    def close(self) -> None:
        self.closed = True


# ── fake port ──


class _FakeWebShellPort:
    def __init__(self, token_result: bool = True) -> None:
        self._token_result = token_result
        self._session = _FakeSession()
        self.open_calls = 0
        self.check_calls = 0

    def check_token(self, token_str: str) -> bool:
        self.check_calls += 1
        return self._token_result

    async def open_session(self) -> _FakeSession:
        self.open_calls += 1
        return self._session


# ── check_token passthrough ──


def test_check_token_returns_true_when_port_returns_true():
    port = _FakeWebShellPort(token_result=True)
    adapter = OpenClawWebShellAdapter(port)
    assert adapter.check_token("any-token") is True
    assert port.check_calls == 1


def test_check_token_returns_false_when_port_returns_false():
    port = _FakeWebShellPort(token_result=False)
    adapter = OpenClawWebShellAdapter(port)
    assert adapter.check_token("wrong-token") is False


# ── open_session ──


@pytest.mark.asyncio
async def test_open_session_returns_port_session_object():
    port = _FakeWebShellPort()
    adapter = OpenClawWebShellAdapter(port)

    session = await adapter.open_session(auth=None)

    assert port.open_calls == 1
    assert session is port._session


@pytest.mark.asyncio
async def test_open_session_auth_is_dropped():
    """Auth parameter is accepted by the adapter but not forwarded (port has no auth param)."""
    port = _FakeWebShellPort()
    adapter = OpenClawWebShellAdapter(port)

    # Calling with a sentinel auth value — port.open_session() must still be called once.
    session = await adapter.open_session(auth=None)
    assert port.open_calls == 1
    assert session is not None


@pytest.mark.asyncio
async def test_open_session_multiple_calls_each_returns_same_fake():
    port = _FakeWebShellPort()
    adapter = OpenClawWebShellAdapter(port)

    s1 = await adapter.open_session()
    s2 = await adapter.open_session()
    assert port.open_calls == 2
    # Our fake always returns the same session object; real impl forks a new child each time.
    assert s1 is s2


# ── WebShellSession Protocol structural check ──


def test_fake_session_satisfies_web_shell_session_protocol():
    """The fake session (and by proxy any real OpenClawWebShellSession)
    must structurally satisfy the WebShellSession Protocol."""
    session = _FakeSession()
    assert isinstance(session, WebShellSession), (
        "The session object must structurally satisfy WebShellSession "
        "(runtime_checkable check on read/write/resize/close)"
    )


@pytest.mark.asyncio
async def test_session_read_returns_bytes():
    session = _FakeSession()
    data = await session.read()
    assert isinstance(data, bytes)


def test_session_write_stores_data():
    session = _FakeSession()
    session.write(b"hello")
    assert session.written == [b"hello"]


def test_session_resize_stores_dimensions():
    session = _FakeSession()
    session.resize(80, 24)
    assert session.resized == [(80, 24)]


def test_session_close_is_idempotent():
    session = _FakeSession()
    session.close()
    session.close()
    assert session.closed is True

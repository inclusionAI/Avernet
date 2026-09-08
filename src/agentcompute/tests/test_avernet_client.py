"""Tests for AvernetClient HTTP integration via mocked urlopen.

No real network calls — all HTTP is intercepted via ``monkeypatch.setattr``
on ``urllib.request.urlopen``.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from typing import Any

import pytest

from agentcompute.community.plugins.agents._avernet import AvernetClient


class _FakeSSEResponse:
    """Mimics an HTTPResponse whose body is an iterable of SSE byte-lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _client() -> AvernetClient:
    return AvernetClient(
        gateway_base_url="https://gw.example.com",
        principal_token="tok-abc",
        user_id="user-1",
    )


def test_create_bot_with_manifest_returns_bot_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _mock_urlopen(request: Any, timeout: Any) -> BytesIO:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        payload = {"code": 0, "data": {"bot_id": "bot-123"}}
        return BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    bot_id = client.create_bot_with_manifest("name: test\nrole: tester")
    assert bot_id == "bot-123"
    assert "Bearer" in captured["headers"]["Authorization"]
    assert "user_id=" in captured["url"]


def test_create_bot_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_urlopen(request: Any, timeout: Any) -> Any:
        raise urllib.error.HTTPError("url", 422, "Unprocessable", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    with pytest.raises(RuntimeError, match="HTTP 422"):
        client.create_bot_with_manifest("name: test")


def test_poll_status_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    states = iter(["CREATING", "CREATING", "READY"])
    captured: dict[str, Any] = {}

    def _mock_urlopen(request: Any, timeout: Any) -> BytesIO:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        state = next(states)
        payload = {"code": 0, "data": {"state": state}}
        return BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    result = client.poll_status("bot-1", timeout=10.0, interval=0.001)
    assert result == "READY"
    assert "Bearer" in captured["headers"]["Authorization"]
    assert "user_id=" in captured["url"]


def test_poll_status_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *a: None)

    def _mock_urlopen(request: Any, timeout: Any) -> BytesIO:
        payload = {"code": 0, "data": {"state": "CREATING"}}
        return BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    with pytest.raises(RuntimeError, match="not READY"):
        client.poll_status("bot-1", timeout=0.01, interval=0.001)


def test_poll_status_terminal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_urlopen(request: Any, timeout: Any) -> BytesIO:
        payload = {"code": 0, "data": {"state": "APPLY_FAILED"}}
        return BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    with pytest.raises(RuntimeError, match="APPLY_FAILED"):
        client.poll_status("bot-1", timeout=10.0, interval=0.001)


def test_stream_chat_parses_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n',
        b'data: {"choices": [{"delta": {"content": " world"}}]}\n',
        b"data: [DONE]\n",
    ]
    captured: dict[str, Any] = {}

    def _mock_urlopen(request: Any, timeout: Any) -> _FakeSSEResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        return _FakeSSEResponse(lines)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    chunks = list(client.stream_chat("bot-1", "hi"))
    assert chunks == ["Hello", " world"]
    assert "Bearer" in captured["headers"]["Authorization"]
    assert "user_id=" in captured["url"]


def test_stream_chat_skips_non_data_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b": ping\n",
        b"event: message\n",
        b'data: {"choices": [{"delta": {"content": "ok"}}]}\n',
        b"\n",
        b"data: [DONE]\n",
    ]

    def _mock_urlopen(request: Any, timeout: Any) -> _FakeSSEResponse:
        return _FakeSSEResponse(lines)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    chunks = list(client.stream_chat("bot-1", "hi"))
    assert chunks == ["ok"]


def test_stream_chat_falls_back_to_content_field(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b'data: {"content": "fallback"}\n',
        b"data: [DONE]\n",
    ]

    def _mock_urlopen(request: Any, timeout: Any) -> _FakeSSEResponse:
        return _FakeSSEResponse(lines)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    chunks = list(client.stream_chat("bot-1", "hi"))
    assert chunks == ["fallback"]


def test_delete_bot_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _mock_urlopen(request: Any, timeout: Any) -> BytesIO:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        return BytesIO(b'{"code": 0}')

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    assert client.delete_bot("bot-1") is None
    assert "Bearer" in captured["headers"]["Authorization"]
    assert "user_id=" in captured["url"]


def test_delete_bot_404_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_urlopen(request: Any, timeout: Any) -> Any:
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    assert client.delete_bot("bot-1") is None


def test_delete_bot_other_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_urlopen(request: Any, timeout: Any) -> Any:
        raise urllib.error.HTTPError("url", 500, "Internal", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    client = _client()
    with pytest.raises(RuntimeError, match="HTTP 500"):
        client.delete_bot("bot-1")


def test_client_init_missing_required_raises() -> None:
    with pytest.raises(ValueError):
        AvernetClient(gateway_base_url="", principal_token="tok", user_id="u")

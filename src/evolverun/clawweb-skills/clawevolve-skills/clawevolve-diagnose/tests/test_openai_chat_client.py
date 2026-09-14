from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from clawevolve_diagnose.judge import openai_chat_client as client_module


class _FakeResponse:
    def __init__(
        self,
        lines: list[bytes] | None = None,
        *,
        body: bytes = b"",
        content_type: str = "text/event-stream",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._lines = lines or []
        self._body = body
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def __iter__(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _FakeOpener:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def open(self, request: urllib.request.Request, timeout: float):
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _sse_response(
    *fragments: dict[str, Any],
    include_done: bool = True,
    headers: dict[str, str] | None = None,
) -> _FakeResponse:
    lines: list[bytes] = []
    for fragment in fragments:
        lines.append(f"data: {json.dumps(fragment, ensure_ascii=False)}\n".encode("utf-8"))
        lines.append(b"\n")
    if include_done:
        lines.extend([b"data: [DONE]\n", b"\n"])
    return _FakeResponse(lines, headers=headers)


def _chunk(
    content: str = "",
    *,
    reasoning_content: str = "",
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if reasoning_content:
        delta["reasoning_content"] = reasoning_content
    result: dict[str, Any] = {
        "id": "chatcmpl-test",
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        result["usage"] = usage
    return result


def _http_error(status: int, body: str, headers: dict[str, str] | None = None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions",
        status,
        "provider failure",
        message,
        io.BytesIO(body.encode("utf-8")),
    )


def test_payload_matches_streaming_curl_contract() -> None:
    payload = client_module._chat_completion_payload(
        "DeepSeek-R1", "You are a helpful assistant.", "你是谁？"
    )
    assert payload == {
        "model": "DeepSeek-R1",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "你是谁？"},
        ],
        "temperature": 0.1,
        "stream": True,
    }


def test_streaming_request_uses_bearer_header_and_assembles_utf8_content() -> None:
    opener = _FakeOpener([
        _sse_response(
            _chunk('{"status":'),
            _chunk('"ok","说明":"完成"}'),
            _chunk(finish_reason="stop", usage={"total_tokens": 8}),
            headers={"X-Request-Id": "provider-request"},
        )
    ])
    result = client_module._post_json_stream(
        "test-key",
        "https://example.invalid/v1/chat/completions",
        client_module._chat_completion_payload("test-model", "system", "用户请求"),
        timeout=600,
        opener=opener,
        sleep_fn=lambda _: None,
        random_fn=lambda: 0,
    )
    assert result["choices"][0]["message"]["content"] == '{"status":"ok","说明":"完成"}'
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"] == {"total_tokens": 8}
    assert result["_stream_chunks"] == 3
    request = opener.requests[0]
    assert request.get_header("Authorization") == "Bearer test-key"
    assert "text/event-stream" in request.get_header("Accept")
    request_payload = json.loads(request.data.decode("utf-8"))
    assert request_payload["stream"] is True
    assert request_payload["messages"][1]["content"] == "用户请求"


def test_chat_json_parses_streamed_content_and_normalizes_bearer_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(api_key: str, endpoint: str, payload: dict[str, Any], **kwargs: Any):
        captured.update(api_key=api_key, endpoint=endpoint, payload=payload, kwargs=kwargs)
        return {
            "choices": [{"message": {"role": "assistant", "content": '{"status":"ok"}'}, "finish_reason": "stop"}],
            "_stream_chunks": 2,
        }

    monkeypatch.setattr(client_module, "_post_json_stream", fake_post)
    result = client_module.chat_json(
        "Bearer test-key",
        "https://example.invalid/v1",
        "test-model",
        "system",
        "用户请求",
        call_name="direct_native_session:session-1",
    )
    assert result == {"status": "ok"}
    assert captured["api_key"] == "test-key"
    assert captured["endpoint"] == "https://example.invalid/v1/chat/completions"
    assert captured["payload"]["stream"] is True


def test_reasoning_content_is_preserved_as_json_fallback() -> None:
    response = _sse_response(
        _chunk(reasoning_content='{"diagnosis":"provider-specific"}'),
        _chunk(finish_reason="stop"),
    )
    data = client_module._read_streaming_response(response)
    message = data["choices"][0]["message"]
    assert message["content"] == ""
    assert message["reasoning_content"] == '{"diagnosis":"provider-specific"}'
    content, field = client_module._parseable_assistant_content(message)
    assert field == "reasoning_content"
    assert client_module.loads_json_object(content) == {"diagnosis": "provider-specific"}


def test_retryable_429_respects_retry_after_then_succeeds() -> None:
    opener = _FakeOpener([
        _http_error(429, '{"error":{"message":"rate limited"}}', {"Retry-After": "2"}),
        _sse_response(_chunk('{"status":"ok"}', finish_reason="stop")),
    ])
    sleeps: list[float] = []
    result = client_module._post_json_stream(
        "test-key",
        "https://example.invalid/v1/chat/completions",
        client_module._chat_completion_payload("test-model", "system", "user"),
        timeout=600,
        opener=opener,
        sleep_fn=sleeps.append,
        random_fn=lambda: 0,
    )
    assert len(opener.requests) == 2
    assert sleeps == [2.0]
    assert result["choices"][0]["message"]["content"] == '{"status":"ok"}'


def test_authentication_error_is_not_retried() -> None:
    opener = _FakeOpener([_http_error(401, '{"error":{"message":"invalid key"}}')])
    with pytest.raises(client_module.LlmHttpError) as raised:
        client_module._post_json_stream(
            "test-key",
            "https://example.invalid/v1/chat/completions",
            client_module._chat_completion_payload("test-model", "system", "user"),
            timeout=600,
            opener=opener,
            sleep_fn=lambda _: None,
        )
    assert raised.value.status_code == 401
    assert "invalid key" in str(raised.value)
    assert len(opener.requests) == 1


def test_transport_failure_retries_at_most_four_times() -> None:
    opener = _FakeOpener([urllib.error.URLError("upstream disconnected")] * 5)
    sleeps: list[float] = []
    with pytest.raises(client_module.LlmTransportError):
        client_module._post_json_stream(
            "test-key",
            "https://example.invalid/v1/chat/completions",
            client_module._chat_completion_payload("test-model", "system", "user"),
            timeout=600,
            opener=opener,
            sleep_fn=sleeps.append,
            random_fn=lambda: 0,
        )
    assert len(opener.requests) == 5
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_invalid_sse_event_is_not_retried() -> None:
    opener = _FakeOpener([_FakeResponse([b"data: not-json\n", b"\n"])])
    with pytest.raises(client_module.LlmResponseError, match="Invalid SSE JSON"):
        client_module._post_json_stream(
            "test-key",
            "https://example.invalid/v1/chat/completions",
            client_module._chat_completion_payload("test-model", "system", "user"),
            timeout=600,
            opener=opener,
            sleep_fn=lambda _: None,
        )
    assert len(opener.requests) == 1


def test_non_stream_json_response_remains_compatible() -> None:
    completion = {"choices": [{"message": {"role": "assistant", "content": '{"status":"ok"}'}, "finish_reason": "stop"}]}
    response = _FakeResponse(body=json.dumps(completion).encode("utf-8"), content_type="application/json")
    assert client_module._read_streaming_response(response)["choices"] == completion["choices"]


def test_runtime_validation_rejects_redacted_and_unexpanded_keys() -> None:
    with pytest.raises(ValueError, match="redacted/truncated"):
        client_module.validate_chat_runtime("abc…xyz", "https://example.invalid/v1", "model")
    with pytest.raises(ValueError, match="unexpanded shell variable"):
        client_module.validate_chat_runtime("$OPENAI_API_KEY", "https://example.invalid/v1", "model")

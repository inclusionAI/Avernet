"""Retry classification for ``LLM._request_with_retry``.

The deployed ``general`` HttpClient runs under a send-hook wrapper (outside
this repo) that re-wraps httpx transport failures into an exception type we
can't subclass-match. So the classifier must branch by *symptom* — does the
exception carry a response object? — not by type:

- 4xx response  → client error, do NOT retry (return ``[llm disabled]``).
- 5xx response  → retry up to ``_MAX_RETRIES`` (transient server fault).
- exception with no 4xx response (connection dropped at send/read, or the
  send-hook-wrapped transport error) → light retry at most
  ``_TIMEOUT_MAX_RETRIES`` with a shrunken ``max_tokens``, then exhaust.
- 200 OK        → return content immediately.

Diagnositc-vs-patch ``max_tokens`` is also asserted: ``chat()`` forwards the
caller's ``max_tokens`` into the posted request body.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

import agentclaw.community.core.harness.services.llm as llm_mod
from agentclaw.community.core.harness.services.llm import (
    DIAGNOSTIC_MAX_TOKENS,
    LLM,
)


@dataclass
class _Secret:
    secret_user: str
    secret_value: str


class _StubResolver:
    def __init__(self, secret):
        self._secret = secret

    def get_secret(self, name):
        return self._secret


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )

    def json(self):
        return self._payload

    def iter_lines(self):
        # llm._do_request streams; encode message.content as SSE delta + [DONE].
        choices = self._payload.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                yield "data: " + json.dumps(
                    {"choices": [{"delta": {"content": content}}]}, ensure_ascii=False
                )
        yield "data: [DONE]"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _ScriptedHttpClient:
    """Plays a scripted sequence of ``post()`` outcomes.

    Each entry is either an ``httpx.Response``-shaped ``_FakeResponse`` or an
    exception to raise. Records every posted body so tests can assert retry
    behaviour and ``max_tokens`` shrinkage.
    """

    def __init__(self, outcomes: list[Any]):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def post(self, path, *, json=None, headers=None, timeout=None, **kwargs):
        self.calls.append({"url": path, "json": json, "timeout": timeout})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def stream(self, method, path, *, json=None, headers=None, timeout=None, **kwargs):
        # llm._do_request now calls stream(); mirror post() and return the
        # outcome as a context manager (_FakeResponse supports __enter__).
        self.calls.append({"url": path, "json": json, "timeout": timeout})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _fresh_semaphore(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "_SEMAPHORE", asyncio.Semaphore(llm_mod._MAX_CONCURRENT_LLM_CALLS)
    )


def _llm(http: _ScriptedHttpClient) -> LLM:
    return LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=_StubResolver(_Secret(secret_user="u", secret_value="tok")),
        http_client=http,
    )


def _ok(content: str = "fixed") -> _FakeResponse:
    return _FakeResponse({"choices": [{"message": {"content": content}}]})


def _status(status_code: int) -> _FakeResponse:
    # status code only matters for raise_for_status; body unused once it raises.
    return _FakeResponse({}, status_code=status_code)


@pytest.mark.asyncio
async def test_200_returns_content_immediately():
    http = _ScriptedHttpClient([_ok("hello")])
    out = await _llm(http).chat(system="s", user="u")
    assert out == "hello"
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_5xx_retries_then_succeeds():
    http = _ScriptedHttpClient([_status(503), _status(502), _ok("recovered")])
    out = await _llm(http).chat(system="s", user="u")
    assert out == "recovered"
    assert len(http.calls) == 3


@pytest.mark.asyncio
async def test_4xx_does_not_retry():
    http = _ScriptedHttpClient([_status(401)])
    out = await _llm(http).chat(system="s", user="u")
    assert out == "[llm disabled]"
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_4xx_status_only_no_break_retry():
    """A 4xx read via ``raise_for_status`` → HTTPStatusError → no retry.

    httpx.Response is constructed internally by ``_do_request`` (via the
    injected client), so this exercises the explicit ``HTTPStatusError`` arm
    rather than the symptom-based wrapper arm."""
    http = _ScriptedHttpClient([_status(404)])
    out = await _llm(http).chat(system="s", user="u")
    assert out == "[llm disabled]"
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_5xx_exhausts_then_disabled():
    outcomes = [_status(500) for _ in range(llm_mod._MAX_RETRIES)]
    http = _ScriptedHttpClient(outcomes)
    out = await _llm(http).chat(system="s", user="u")
    assert out == "[llm disabled]"
    assert len(http.calls) == llm_mod._MAX_RETRIES


@pytest.mark.asyncio
async def test_httpx_timeout_light_retries_with_shrunk_budget():
    """httpx.TimeoutException falls in ``_TIMEOUT_EXCEPTIONS`` → light retry
    with ``max_tokens`` dropped to ``_TIMEOUT_RETRY_MAX_TOKENS``, bounded by
    ``_TIMEOUT_MAX_RETRIES``."""
    outcomes = [
        httpx.ReadTimeout("timed out", request=None),
        _ok("recovered-after-shrink"),
    ]
    http = _ScriptedHttpClient(outcomes)
    out = await _llm(http).chat(system="s", user="u")

    assert out == "recovered-after-shrink"
    assert len(http.calls) == 2
    assert http.calls[0]["json"]["max_tokens"] == llm_mod._DEFAULT_MAX_TOKENS
    assert http.calls[1]["json"]["max_tokens"] == llm_mod._TIMEOUT_RETRY_MAX_TOKENS


@pytest.mark.asyncio
async def test_httpx_timeout_exhausts_after_light_retries():
    """More connection failures than ``_TIMEOUT_MAX_RETRIES`` allows → exhaust,
    return ``[llm disabled]`` (not the full ``_MAX_RETRIES`` verbatim budget)."""
    outcomes = [
        httpx.ReadTimeout("t1", request=None),
        httpx.ReadTimeout("t2", request=None),
        httpx.ReadTimeout("t3", request=None),
        _ok("never-reached"),
    ]
    http = _ScriptedHttpClient(outcomes)
    out = await _llm(http).chat(system="s", user="u")

    assert out == "[llm disabled]"
    # 1 initial attempt + light retries bounded by _TIMEOUT_MAX_RETRIES.
    assert len(http.calls) == 1 + llm_mod._TIMEOUT_MAX_RETRIES
    # Every retry used the shrunken budget, never the heavy default.
    assert all(
        c["json"]["max_tokens"] == llm_mod._TIMEOUT_RETRY_MAX_TOKENS
        for c in http.calls[1:]
    )


@pytest.mark.asyncio
async def test_wrapped_send_hook_failure_routes_to_light_retry():
    """The send-hook wrapper raises an opaque ``RuntimeError`` (no ``response``
    attribute) — exactly the production ``Error in httpx send hook`` shape.
    Because it carries no 4xx response it must be treated as connection-level
    and routed to the light, shrunken-budget retry — NOT verbatim full-body
    retry (which is what caused the 280s exhaustion in production)."""
    outcomes = [
        RuntimeError("Error in httpx send hook"),
        _ok("recovered-after-light-retry"),
    ]
    http = _ScriptedHttpClient(outcomes)
    out = await _llm(http).chat(system="s", user="u")

    assert out == "recovered-after-light-retry"
    assert len(http.calls) == 2
    assert http.calls[0]["json"]["max_tokens"] == llm_mod._DEFAULT_MAX_TOKENS
    assert http.calls[1]["json"]["max_tokens"] == llm_mod._TIMEOUT_RETRY_MAX_TOKENS


@pytest.mark.asyncio
async def test_wrapped_send_hook_failure_exhausts_quickly():
    """A sustained send-hook failure still exhausts after the light-retry
    budget, not the full ``_MAX_RETRIES`` verbatim budget."""
    outcomes = [
        RuntimeError("Error in httpx send hook"),
        RuntimeError("Error in httpx send hook"),
        RuntimeError("Error in httpx send hook"),
    ]
    http = _ScriptedHttpClient(outcomes)
    out = await _llm(http).chat(system="s", user="u")

    assert out == "[llm disabled]"
    assert len(http.calls) == 1 + llm_mod._TIMEOUT_MAX_RETRIES


@pytest.mark.asyncio
async def test_wrapper_with_4xx_response_does_not_retry():
    """If the send-hook wrapper DOES attach a 4xx response (auth/usage error
    propagated through the layer), we must not retry — repeated verbatim posts
    won't help."""
    wrapped = RuntimeError("auth refused")
    wrapped.response = _FakeResponse({}, status_code=403)  # type: ignore[attr-defined]
    http = _ScriptedHttpClient([wrapped])
    out = await _llm(http).chat(system="s", user="u")

    assert out == "[llm disabled]"
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_send_hook_failure_recoverable_on_second_light_retry():
    """A transient gateway drop can persist past the first light retry and only
    clear on the second. With ``_TIMEOUT_MAX_RETRIES >= 2`` the call must still
    recover on the second shrunken-budget retry instead of giving up after one.
    Locks in the bump from one light retry (which gave up prematurely on blips
    that lasted >1 retry) to two."""
    if llm_mod._TIMEOUT_MAX_RETRIES < 2:
        pytest.skip("light-retry budget is < 2; second-retry recovery not supported")
    outcomes = [
        RuntimeError("Error in httpx send hook"),
        RuntimeError("Error in httpx send hook"),
        _ok("recovered-on-second-light-retry"),
    ]
    http = _ScriptedHttpClient(outcomes)
    out = await _llm(http).chat(system="s", user="u")

    assert out == "recovered-on-second-light-retry"
    assert len(http.calls) == 3
    assert http.calls[0]["json"]["max_tokens"] == llm_mod._DEFAULT_MAX_TOKENS
    # Both light retries use the shrunken budget.
    assert all(
        c["json"]["max_tokens"] == llm_mod._TIMEOUT_RETRY_MAX_TOKENS
        for c in http.calls[1:]
    )


@pytest.mark.asyncio
async def test_diagnostic_max_tokens_forwarded_into_body():
    http = _ScriptedHttpClient([_ok("ok")])
    await _llm(http).chat(system="s", user="u", max_tokens=DIAGNOSTIC_MAX_TOKENS)
    assert http.calls[0]["json"]["max_tokens"] == DIAGNOSTIC_MAX_TOKENS


@pytest.mark.asyncio
async def test_default_max_tokens_forwarded_into_body():
    http = _ScriptedHttpClient([_ok("ok")])
    await _llm(http).chat(system="s", user="u")
    assert http.calls[0]["json"]["max_tokens"] == llm_mod._DEFAULT_MAX_TOKENS
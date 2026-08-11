"""Coverage for the harness-LLM secret-resolver seam.

The harness ``LLM`` reads its API token through an injected ``SecretResolver``
(corp=mist, community=env seam) keyed by ``secret_name``, and issues HTTP through
an injected ``HttpClient`` (the shared ``general`` sync client). It reads no
process environment and bakes in no fallback credential: the token is resolved
once, at construction. When it does not resolve (``None``), ``chat()`` returns
``[llm disabled]`` and never re-resolves.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

import agentclaw.community.core.harness.services.llm as llm_mod
from agentclaw.community.core.harness.services.llm import LLM


@dataclass
class _Secret:
    secret_user: str
    secret_value: str


class _StubResolver:
    def __init__(self, secret):
        self._secret = secret
        self.calls = 0

    def get_secret(self, name):
        self.calls += 1
        return self._secret


class _RaisingResolver:
    """``get_secret`` always raises — the secret backend is unreachable."""

    def __init__(self):
        self.calls = 0

    def get_secret(self, name):
        self.calls += 1
        raise RuntimeError("mist parse error")


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

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


class _RecordingHttpClient:
    """Minimal ``HttpClient`` double: records ``post()`` calls and returns a
    canned OpenAI-shaped body."""

    def __init__(self, content="ok-response"):
        self._content = content
        self.calls: list[dict] = []

    def post(self, path, *, json=None, headers=None, timeout=None, **kwargs):
        self.calls.append(
            {"url": path, "json": json, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse({"choices": [{"message": {"content": self._content}}]})

    def stream(self, method, path, *, json=None, headers=None, timeout=None, **kwargs):
        # llm._do_request now calls stream(); mirror post(), return a
        # context-manager _FakeResponse.
        self.calls.append(
            {"url": path, "json": json, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse({"choices": [{"message": {"content": self._content}}]})


@pytest.fixture(autouse=True)
def _fresh_semaphore(monkeypatch):
    """Bind a fresh module semaphore per test so the concurrency limiter is never
    reused across pytest-asyncio's per-test event loops."""
    monkeypatch.setattr(
        llm_mod, "_SEMAPHORE", asyncio.Semaphore(llm_mod._MAX_CONCURRENT_LLM_CALLS)
    )


def _make_llm(resolver, http_client=None, base_url="http://llm.local", secret_name="llm-key"):
    return LLM(
        base_url=base_url,
        secret_name=secret_name,
        secret_resolver=resolver,
        http_client=http_client or _RecordingHttpClient(),
    )


def test_llm_loads_token_from_secret_resolver():
    llm = _make_llm(_StubResolver(_Secret(secret_user="u", secret_value="tok-xyz")))
    assert llm._token == "tok-xyz"


def test_llm_token_none_when_resolver_absent_no_baked_fallback():
    """Secret absent and shipped source bakes no fallback → ``None`` token,
    sourced solely from the resolver (one lookup, no env read)."""
    resolver = _StubResolver(None)
    llm = _make_llm(resolver)
    assert llm._token is None
    assert resolver.calls == 1


def test_llm_token_none_when_resolver_raises():
    """A resolver failure at construction disables the LLM without crashing and
    without any baked fallback."""
    resolver = _RaisingResolver()
    llm = _make_llm(resolver)
    assert llm._token is None
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_llm_disabled_returns_sentinel_and_never_re_resolves():
    """No token → chat() returns the sentinel, makes no HTTP call, and does NOT
    consult the resolver again (resolution happens once, at construction)."""
    resolver = _StubResolver(None)
    http = _RecordingHttpClient()
    llm = _make_llm(resolver, http)
    assert resolver.calls == 1

    out = await llm.chat(system=None, user="hi")

    assert out == "[llm disabled]"
    assert http.calls == []          # no HTTP attempted
    assert resolver.calls == 1       # no re-resolution


@pytest.mark.asyncio
async def test_llm_sends_request_body_and_timeout():
    """Happy path: token resolved at construction → chat() posts the
    OpenAI-shaped body with the configured model and timeout, through the injected
    HttpClient, to the absolute URL."""
    resolver = _StubResolver(_Secret(secret_user="u", secret_value="tok"))
    http = _RecordingHttpClient(content="hello")
    llm = LLM(
        base_url="http://llm.local/",  # trailing slash normalized
        secret_name="llm-key",
        secret_resolver=resolver,
        http_client=http,
        model="GLM-5.1",
        timeout_ms=5_000,
    )

    out = await llm.chat(system="sys", user="hi")

    assert out == "hello"
    call = http.calls[0]
    assert call["url"] == "http://llm.local/v1/chat/completions"
    assert call["timeout"] == 5.0
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["json"]["model"] == "GLM-5.1"
    assert call["json"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]

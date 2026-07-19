"""Coverage for the harness-LLM secret-resolver seam and self-healing token.

The harness ``LLM`` reads its API token through an injected ``SecretResolver``
(corp=mist, community=env seam) keyed by ``secret_name``, and issues HTTP through
an injected ``HttpClient`` (the shared ``general`` sync client) — it reads no
process environment. These tests exercise the resolved-secret path, the absent
fallback, and — for #201 — the lazy resolution that keeps a transient
secret-backend failure from latching the LLM off for the worker's lifetime.
"""
from __future__ import annotations

import asyncio
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


class _FlakyResolver:
    """Raises on the first ``get_secret`` (backend not ready), then succeeds —
    the prod SpawnProcess symptom in #201."""

    def __init__(self, secret):
        self._secret = secret
        self.calls = 0

    def get_secret(self, name):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("mist parse error")
        return self._secret


class _TogglingResolver:
    """Returns ``None`` (secret absent) until ``available`` is flipped on — the
    backend comes up after boot."""

    def __init__(self, secret):
        self._secret = secret
        self.available = False
        self.calls = 0

    def get_secret(self, name):
        self.calls += 1
        return self._secret if self.available else None


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


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


def test_llm_token_empty_when_resolver_absent_no_baked_fallback():
    """Resolver reports the secret absent and shipped source bakes no fallback →
    empty token, sourced solely from the resolver (no env read)."""
    resolver = _StubResolver(None)
    llm = _make_llm(resolver)
    assert llm._token == ""
    assert resolver.calls == 1  # consulted the resolver, nothing else


@pytest.mark.asyncio
async def test_llm_recovers_when_resolver_becomes_available():
    """A transient resolver failure at init must not latch the LLM off: the next
    chat() re-resolves and the request carries the real token (#201)."""
    resolver = _FlakyResolver(_Secret(secret_user="u", secret_value="real-tok"))
    http = _RecordingHttpClient()
    llm = _make_llm(resolver, http)

    # Eager resolve raised → no token yet, but this is recoverable, not latched.
    assert llm._token == ""

    out = await llm.chat(system=None, user="hi")

    assert out == "ok-response"
    assert llm._token == "real-tok"
    assert resolver.calls == 2  # once eager (raised), once on chat()
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == "http://llm.local/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer real-tok"


@pytest.mark.asyncio
async def test_llm_missing_token_retries_not_latched():
    """Secret absent at boot → [llm disabled] with no HTTP, but NOT latched: once
    the backend serves the secret the next chat() resolves it and sends."""
    resolver = _TogglingResolver(_Secret(secret_user="u", secret_value="late-tok"))
    http = _RecordingHttpClient()
    llm = _make_llm(resolver, http)
    assert llm._token == ""

    first = await llm.chat(system=None, user="hi")
    assert first == "[llm disabled]"
    assert http.calls == []  # no token → no HTTP

    # Backend becomes reachable after boot.
    resolver.available = True
    second = await llm.chat(system=None, user="hi")

    assert second == "ok-response"
    assert llm._token == "late-tok"
    assert len(http.calls) == 1
    assert http.calls[0]["headers"]["Authorization"] == "Bearer late-tok"


@pytest.mark.asyncio
async def test_llm_sends_request_body_and_timeout():
    """Happy path: token resolved at init → chat() posts the OpenAI-shaped body
    with the configured model and timeout, through the injected HttpClient."""
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
    assert call["json"]["model"] == "GLM-5.1"
    assert call["json"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]

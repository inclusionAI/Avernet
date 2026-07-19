"""Coverage for the harness-LLM secret-resolver seam.

The harness ``LLM`` reads its API token through an injected ``SecretResolver``
(corp=mist, community=env seam) keyed by ``secret_name`` — it reads no process
environment; endpoint and secret key arrive from the DI provider. These tests
exercise the resolved-secret path, the None fallback, and — for #201 — the
lazy, self-healing resolution that keeps a transient secret-backend failure
from latching the LLM off for the worker's lifetime.
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


@pytest.fixture(autouse=True)
def _fresh_semaphore(monkeypatch):
    """Bind a fresh module semaphore per test so the concurrency limiter is never
    reused across pytest-asyncio's per-test event loops."""
    monkeypatch.setattr(
        llm_mod, "_SEMAPHORE", asyncio.Semaphore(llm_mod._MAX_CONCURRENT_LLM_CALLS)
    )


def _capture_request(llm: LLM) -> dict:
    """Stub the HTTP layer so ``chat()`` can be driven without a network, and
    record the headers/body it would have sent."""
    captured: dict = {}

    async def _fake_do_request(body, headers, use_original_send=False):
        captured["headers"] = headers
        captured["body"] = body
        return "ok-response"

    llm._do_request = _fake_do_request  # type: ignore[assignment]
    return captured


def test_llm_loads_token_from_secret_resolver():
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=_StubResolver(_Secret(secret_user="u", secret_value="tok-xyz")),
    )
    assert llm._token == "tok-xyz"


def test_llm_token_empty_when_resolver_absent_no_baked_fallback():
    """Resolver reports the secret absent and shipped source bakes no fallback →
    empty token (disabled), sourced solely from the resolver (no env read)."""
    resolver = _StubResolver(None)
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=resolver,
    )
    assert llm._token == ""
    assert llm._disabled is True
    assert resolver.calls == 1  # consulted the resolver, nothing else


@pytest.mark.asyncio
async def test_llm_recovers_when_resolver_becomes_available():
    """A transient resolver failure at init must not latch the LLM off: the next
    chat() re-resolves and the request carries the real token (#201)."""
    resolver = _FlakyResolver(_Secret(secret_user="u", secret_value="real-tok"))
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=resolver,
    )

    # Eager resolve raised → no token yet, but the endpoint is configured, so this
    # is recoverable rather than a permanent feature-off.
    assert llm._token == ""
    assert llm._config_disabled is False
    assert llm._disabled is True  # unresolvable *right now*

    captured = _capture_request(llm)
    out = await llm.chat(system=None, user="hi")

    assert out == "ok-response"
    assert llm._token == "real-tok"
    assert captured["headers"]["Authorization"] == "Bearer real-tok"
    assert llm._disabled is False  # self-healed
    assert resolver.calls == 2  # once eager (raised), once on chat()


@pytest.mark.asyncio
async def test_llm_config_off_stays_disabled():
    """Empty base_url → permanently disabled; the resolver is never consulted."""
    resolver = _StubResolver(_Secret(secret_user="u", secret_value="tok"))
    llm = LLM(base_url="", secret_name="llm-key", secret_resolver=resolver)

    assert llm._config_disabled is True
    captured = _capture_request(llm)
    out = await llm.chat(system=None, user="hi")

    assert out == "[llm disabled]"
    assert "headers" not in captured  # no HTTP attempted
    assert resolver.calls == 0  # no base_url → resolver branch skipped


@pytest.mark.asyncio
async def test_llm_missing_token_retries_not_latched():
    """Secret absent at boot → disabled sentinel, but NOT latched: once the
    backend serves the secret the next chat() resolves it and sends."""
    resolver = _TogglingResolver(_Secret(secret_user="u", secret_value="late-tok"))
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=resolver,
    )
    assert llm._token == ""

    captured = _capture_request(llm)
    first = await llm.chat(system=None, user="hi")
    assert first == "[llm disabled]"
    assert "headers" not in captured  # no token → no HTTP

    # Backend becomes reachable after boot.
    resolver.available = True
    second = await llm.chat(system=None, user="hi")

    assert second == "ok-response"
    assert llm._token == "late-tok"
    assert captured["headers"]["Authorization"] == "Bearer late-tok"

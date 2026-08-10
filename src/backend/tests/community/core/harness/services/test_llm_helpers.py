"""Coverage for the pure helpers and request-shaping edge cases in ``llm.py``.

The retry classifier (``test_llm_request_retry.py``) and the secret-resolver /
happy-path body shape (``test_llm_secret_resolver.py``) are covered elsewhere;
this file fills the remaining gaps:

- :func:`_retry_delay` — escalating base delay + jitter, clamped at the last
  slot, spread across bounds rather than a single wall-clock tick.
- :func:`_client_error_status` — symptom-based 4xx classification (the
  send-hook wrapper can't be subclass-matched, so we read ``response``).
- :func:`_exc_detail` — surfaces the underlying cause chain + request URL so an
  opaque ``HttpxCallingException('Error in httpx send hook')`` becomes
  actionable in logs.
- ``chat()`` message construction when ``system is None`` (single user message).
- ``_do_request`` response parsing — empty/absent ``choices`` yields ``""``.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest

import agentclaw.community.core.harness.services.llm as llm_mod
from agentclaw.community.core.harness.services.llm import LLM


# ── doubles ─────────────────────────────────────────────────────────────────
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
        # llm._do_request calls raise_for_status() unconditionally; keep it green
        # for the response-parsing tests (status never matters here).
        if self.status_code >= 400:  # pragma: no cover - not exercised here
            raise AssertionError("parsing tests only use 200 bodies")

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
    """Returns a canned OpenAI-shaped body and records each ``post()`` call."""

    def __init__(self, payload):
        self._payload = payload
        self.calls: list[dict] = []

    def post(self, path, *, json=None, headers=None, timeout=None, **kwargs):
        self.calls.append(
            {"url": path, "json": json, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse(self._payload)

    def stream(self, method, path, *, json=None, headers=None, timeout=None, **kwargs):
        # llm._do_request now calls stream(); mirror post(), return a
        # context-manager _FakeResponse.
        self.calls.append(
            {"url": path, "json": json, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse(self._payload)


@pytest.fixture(autouse=True)
def _fresh_semaphore(monkeypatch):
    """Per-test module semaphore (pytest-asyncio rotates event loops)."""
    monkeypatch.setattr(
        llm_mod, "_SEMAPHORE", asyncio.Semaphore(llm_mod._MAX_CONCURRENT_LLM_CALLS)
    )


def _make_llm(http_client, token="tok", base_url="http://llm.local") -> LLM:
    resolver = _StubResolver(
        _Secret(secret_user="u", secret_value=token) if token else None
    )
    return LLM(
        base_url=base_url,
        secret_name="llm-key",
        secret_resolver=resolver,
        http_client=http_client,
    )


# ── _retry_delay ────────────────────────────────────────────────────────────
def test_retry_delay_within_jittered_bounds():
    """Base delay escalates with ``_RETRY_DELAYS``; the result is always
    ``[base, base + base*_RETRY_JITTER]`` so retries stay staggered."""
    for attempt, expected_base in enumerate(llm_mod._RETRY_DELAYS):
        delay = llm_mod._retry_delay(attempt)
        lo = expected_base
        hi = expected_base * (1.0 + llm_mod._RETRY_JITTER)
        assert lo <= delay <= hi, (attempt, delay, lo, hi)


def test_retry_delay_clamps_beyond_last_slot():
    """Attempts past the last ``_RETRY_DELAYS`` entry reuse the largest base —
    backoff stops escalating once every named delay has been tried."""
    last_base = llm_mod._RETRY_DELAYS[-1]
    for attempt in (len(llm_mod._RETRY_DELAYS), len(llm_mod._RETRY_DELAYS) + 5):
        delay = llm_mod._retry_delay(attempt)
        hi = last_base * (1.0 + llm_mod._RETRY_JITTER)
        assert last_base <= delay <= hi


def test_retry_delay_deterministic_when_jitter_pinned(monkeypatch):
    """With ``random.uniform`` pinned, the delay equals ``base + pinned`` so the
    jitter contribution is exactly ``base * jitter_fraction``."""
    monkeypatch.setattr(llm_mod.random, "uniform", lambda _lo, _hi: 0.0)
    assert llm_mod._retry_delay(0) == llm_mod._RETRY_DELAYS[0]

    # high bound == base * jitter_fraction → delay is base + base*jitter.
    monkeypatch.setattr(llm_mod.random, "uniform", lambda _lo, hi: hi)
    for attempt, base in enumerate(llm_mod._RETRY_DELAYS):
        assert llm_mod._retry_delay(attempt) == base + base * llm_mod._RETRY_JITTER


# ── _client_error_status ────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Exc(Exception):
    """Bare exception with an optional ``response`` attribute (the send-hook
    wrapper shape — we can't subclass-match it, only inspect symptoms)."""

    def __init__(self, msg, response=None):
        super().__init__(msg)
        self.response = response


def test_client_error_status_returns_4xx():
    assert llm_mod._client_error_status(_Exc("no", response=_Resp(401))) == 401
    assert llm_mod._client_error_status(_Exc("no", response=_Resp(499))) == 499


def test_client_error_status_5xx_is_not_client_error():
    assert llm_mod._client_error_status(_Exc("no", response=_Resp(500))) is None
    assert llm_mod._client_error_status(_Exc("no", response=_Resp(503))) is None


def test_client_error_status_non_4xx_is_none():
    assert llm_mod._client_error_status(_Exc("ok", response=_Resp(200))) is None
    assert llm_mod._client_error_status(_Exc("ok", response=_Resp(399))) is None


def test_client_error_status_no_response_is_connection_level():
    """No ``response`` attached → connection-level failure → ``None`` (caller
    routes to the light, shrunken-budget retry, not a verbatim repeat)."""
    assert llm_mod._client_error_status(_Exc("dropped")) is None


def test_client_error_status_missing_or_non_int_status_is_none():
    class _NoStatusResp:
        pass

    assert llm_mod._client_error_status(_Exc("x", response=_NoStatusResp())) is None
    assert llm_mod._client_error_status(_Exc("x", response=_Resp("not-an-int"))) is None


# ── _exc_detail ─────────────────────────────────────────────────────────────
def test_exc_detail_bare_exception():
    detail = llm_mod._exc_detail(RuntimeError("boom"))
    assert detail == "RuntimeError: boom"


def test_exc_detail_includes_cause_chain():
    """The deployed ``general`` client wraps the real transport error on
    ``__cause__``; ``_exc_detail`` must surface it (not just the opaque wrapper)."""
    try:
        raise httpx_readerror_cause()
    except Exception as cause:
        try:
            raise RuntimeError("Error in httpx send hook") from cause
        except RuntimeError as wrapper:
            detail = llm_mod._exc_detail(wrapper)

    assert "RuntimeError: Error in httpx send hook" in detail
    assert "caused by" in detail


def test_exc_detail_includes_context_when_no_cause():
    """Without an explicit ``__cause__`` the ``__context__`` chain is surfaced."""
    try:
        raise ValueError("underlying transport blip")
    except ValueError:
        try:
            raise RuntimeError("wrapper")
        except RuntimeError as wrapper:
            detail = llm_mod._exc_detail(wrapper)

    assert "caused by ValueError: underlying transport blip" in detail


def test_exc_detail_includes_request_url():
    class _Req:
        url = "https://llm.local/v1/chat/completions"

    class _ExcWithReq(Exception):
        def __init__(self, msg):
            super().__init__(msg)
            self.request = _Req()

    detail = llm_mod._exc_detail(_ExcWithReq("send failed"))
    assert "request=https://llm.local/v1/chat/completions" in detail


def test_exc_detail_omits_request_when_url_missing():
    class _ReqNoUrl:
        pass

    class _ExcWithReq(Exception):
        def __init__(self, msg):
            super().__init__(msg)
            self.request = _ReqNoUrl()

    detail = llm_mod._exc_detail(_ExcWithReq("send failed"))
    assert "request=" not in detail
    assert detail == "_ExcWithReq: send failed"


# ── chat() message construction ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_chat_without_system_builds_single_user_message():
    """``system is None`` → the body carries only the user message (no empty
    system content injected)."""
    http = _RecordingHttpClient({"choices": [{"message": {"content": "ok"}}]})
    await _make_llm(http).chat(system=None, user="just user")

    body = http.calls[0]["json"]
    assert body["messages"] == [{"role": "user", "content": "just user"}]
    assert all(m["role"] != "system" for m in body["messages"])


@pytest.mark.asyncio
async def test_chat_headers_carry_bearer_token_and_content_type():
    http = _RecordingHttpClient({"choices": [{"message": {"content": "ok"}}]})
    await _make_llm(http, token="tok-abc").chat(system="s", user="u")

    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer tok-abc"
    assert headers["Content-Type"] == "application/json"


# ── _do_request response parsing ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_do_request_empty_choices_returns_empty_string():
    http = _RecordingHttpClient({"choices": []})
    out = await _make_llm(http).chat(system="s", user="u")
    assert out == ""


@pytest.mark.asyncio
async def test_do_request_missing_choices_key_returns_empty_string():
    http = _RecordingHttpClient({})  # no "choices" key at all
    out = await _make_llm(http).chat(system="s", user="u")
    assert out == ""


@pytest.mark.asyncio
async def test_do_request_choice_without_content_returns_empty_string():
    http = _RecordingHttpClient({"choices": [{"message": {}}]})
    out = await _make_llm(http).chat(system="s", user="u")
    assert out == ""


@pytest.mark.asyncio
async def test_do_request_returns_first_choice_content():
    """When multiple choices are present, only the first one's content is used
    (the harness prompt contract is a single completion)."""
    http = _RecordingHttpClient(
        {
            "choices": [
                {"message": {"content": "first"}},
                {"message": {"content": "second"}},
            ]
        }
    )
    out = await _make_llm(http).chat(system="s", user="u")
    assert out == "first"


# ── construction defaults ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_chat_uses_default_model_and_timeout():
    """Omitting ``model`` / ``timeout_ms`` yields the documented defaults in the
    posted body and the per-call timeout (``timeout_ms / 1000`` seconds)."""
    http = _RecordingHttpClient({"choices": [{"message": {"content": "ok"}}]})
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=_StubResolver(_Secret(secret_user="u", secret_value="tok")),
        http_client=http,
    )
    await llm.chat(system="s", user="u")

    body = http.calls[0]["json"]
    assert body["model"] == "GLM-5.1"
    assert http.calls[0]["timeout"] == 180_000 / 1000.0


@pytest.mark.asyncio
async def test_chat_posts_to_absolute_url_under_base_url():
    http = _RecordingHttpClient({"choices": [{"message": {"content": "ok"}}]})
    await _make_llm(http, base_url="https://api.example.com").chat(system="s", user="u")
    assert http.calls[0]["url"] == "https://api.example.com/v1/chat/completions"


# ── helper for the cause-chain test ─────────────────────────────────────────
def httpx_readerror_cause() -> Exception:
    """Build a representative underlying transport error to chain as a cause."""
    return httpx.ReadError("server closed connection", request=None)
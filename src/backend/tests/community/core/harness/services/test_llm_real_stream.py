"""F1 regression — ``LLM.chat()`` drives a REAL ``HttpxClient.stream``.

``LLM.chat()`` reads its completion off an SSE stream through the injected
``HttpClient`` seam (``llm.py`` → ``HttpxClient.stream``), accumulating
``delta.content`` into the returned text. The regression this pins is that the
seam actually *has* a working ``stream`` the LLM path can drive — the original
failure was an ``AttributeError`` on a missing method, which no test using a
hand-rolled mock client would have caught.

This replaces the ``HttpxClient`` half of the former
``test_http_client_stream.py``, which injected an ``httpx.MockTransport`` through
a constructor parameter that no longer exists (it was production surface that
only tests used). The same coverage needs no such seam: seeding the pooled client
directly gives a real ``httpx.Client`` over a mock transport, so every line of
``HttpxClient.stream`` runs for real while the responses stay in-process.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.plugins.http_client import _DiscardingCookieJar, HttpxClient


def _sse_handler(content: str):
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "data: " + json.dumps({"choices": [{"delta": {"content": content}}]}) + "\n"
            "data: [DONE]\n"
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    return handler


def _seeded_client(handler, base_url: str = "http://llm.local") -> HttpxClient:
    """A real pooled client over ``MockTransport`` — no constructor seam."""
    client = HttpxClient(base_url=base_url)
    client._client = httpx.Client(
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        cookies=_DiscardingCookieJar(),
    )
    return client


class _Secret:
    def __init__(self, value):
        self.secret_value = value


class _StubResolver:
    def __init__(self, value):
        self._value = value

    def get_secret(self, name):
        return _Secret(self._value)


def test_llm_chats_via_real_httpx_client_stream():
    """``LLM.chat()`` accumulates SSE ``delta.content`` through a real
    ``HttpxClient.stream`` — no ``AttributeError`` on a missing method."""
    http = _seeded_client(_sse_handler("hello-from-real-client"))
    try:
        llm = LLM(
            base_url="http://llm.local",
            secret_name="k",
            secret_resolver=_StubResolver("tok"),
            http_client=http,
        )
        out = asyncio.run(llm.chat(system="s", user="u", max_tokens=8))
    finally:
        http.close()
    assert out == "hello-from-real-client"


def test_llm_stream_reuses_the_pooled_client_across_calls():
    """Two chats must ride the same pooled client — the stream path must not
    close it, or the second call would fail on a closed client."""
    http = _seeded_client(_sse_handler("again"))
    try:
        llm = LLM(
            base_url="http://llm.local",
            secret_name="k",
            secret_resolver=_StubResolver("tok"),
            http_client=http,
        )
        pooled = http._pooled_client()
        first = asyncio.run(llm.chat(system="s", user="u", max_tokens=8))
        second = asyncio.run(llm.chat(system="s", user="u", max_tokens=8))
        assert first == second == "again"
        assert http._pooled_client() is pooled, "stream must not replace the pool"
        assert not pooled.is_closed, "stream must not close the pooled client"
    finally:
        http.close()

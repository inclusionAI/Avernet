"""Conformance tests for ``HttpClient.stream`` — the streaming seam llm.py uses.

These exercise the REAL ``HttpxClient`` (via an injected ``httpx.MockTransport``,
not a hand-rolled mock client) and ``LocalHttpClient``, covering both
implementations of the ``HttpClient.stream`` contract (Rule 25). The
``test_llm_chats_via_real_httpx_client_stream`` case pins the F1 regression:
``LLM.chat()`` must drive a real ``HttpxClient.stream`` end-to-end, accumulating
SSE ``delta.content`` — with no ``AttributeError`` on a missing method.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.plugins.http_client import HttpxClient
from agentclaw.community.plugins.local.http_client import (
    HttpNotConfiguredError,
    LocalHttpClient,
)


def _sse_handler(content: str = "hello"):
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "data: " + json.dumps({"choices": [{"delta": {"content": content}}]}) + "\n"
            "data: [DONE]\n"
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    return handler


def test_httpx_client_stream_yields_streaming_response():
    transport = httpx.MockTransport(_sse_handler("hello"))
    client = HttpxClient("http://llm.local", transport=transport)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "x"},
        headers={"k": "v"},
        timeout=5.0,
    ) as resp:
        assert resp.status_code == 200
        resp.raise_for_status()
        lines = [ln for ln in resp.iter_lines() if ln]
    assert any('data: {"choices"' in ln for ln in lines)
    assert any(ln.startswith("data: [DONE]") for ln in lines)


def test_local_http_client_stream_raises():
    client = LocalHttpClient("http://local.invalid")
    with pytest.raises(HttpNotConfiguredError):
        with client.stream("POST", "/v1/chat/completions", json={}, timeout=5.0):
            pass


class _Secret:
    def __init__(self, value):
        self.secret_value = value


class _StubResolver:
    def __init__(self, value):
        self._value = value

    def get_secret(self, name):
        return _Secret(self._value)


def test_llm_chats_via_real_httpx_client_stream():
    """F1 regression: ``LLM.chat()`` drives a REAL ``HttpxClient.stream``
    end-to-end (no ``AttributeError`` on a missing method), accumulating SSE
    ``delta.content`` into the returned text."""
    transport = httpx.MockTransport(_sse_handler("hello-from-real-client"))
    http = HttpxClient("http://llm.local", transport=transport)
    llm = LLM(
        base_url="http://llm.local",
        secret_name="k",
        secret_resolver=_StubResolver("tok"),
        http_client=http,
    )
    out = asyncio.run(llm.chat(system="s", user="u", max_tokens=8))
    assert out == "hello-from-real-client"
"""The BaaS ws-info mock composes ``ws_url`` the way BaaS itself does.

The mock stands in for an upstream this suite cannot call, so what it is
allowed to *invent* is the whole question. A hardcoded well-formed ``ws_url``
answers every request identically — including one whose ``path`` the caller
mangled — which is how a client that stripped the socket path's leading slash
shipped: BaaS joins target and path with no separator of its own
(``build_proxypass_url`` → ``…/proxypass/{target}{path}``), so the real
deployment published ``…@0:20003api/openclaw/ws`` while every test stayed green.

These tests pin the echo instead: the mock's URL is a function of the request,
so a caller that sends a bad path gets a bad URL back, here rather than in
production.
"""
from __future__ import annotations

import httpx

WS_INFO_URL = "http://localhost:8890/api/v1/bots/BOT-1/ws-info"


def _ws_url(**params: str) -> str:
    """``ws_url`` the mocked ws-info endpoint answers for ``params``.

    Goes over httpx on purpose: the autouse ``_no_real_baas_calls`` fixture
    installs the mock as a *transport* route, so calling the composing function
    directly would test something no caller reaches.
    """
    response = httpx.get(WS_INFO_URL, params=params)
    response.raise_for_status()
    return response.json()["data"]["ws_url"]


def test_the_requested_path_is_appended_to_the_target():
    assert (
        _ws_url(port="20003", path="/api/openclaw/ws")
        == "ws://localhost:20003/api/openclaw/ws"
    )


def test_the_requested_engine_path_is_honoured_not_a_fixed_one():
    """A relay caller asks for its own engine's socket. A mock that answered
    openclaw's regardless would hide a client that dropped the parameter."""
    assert _ws_url(port="20003", path="/api/claude_code/ws").endswith(
        "/api/claude_code/ws"
    )


def test_a_path_without_its_leading_slash_produces_the_broken_url():
    """The failure mode this mock exists to surface. BaaS inserts no separator,
    so the slash is the caller's to send — and its absence has to be visible
    here, not in a published socket URL."""
    assert _ws_url(port="20003", path="api/openclaw/ws") == (
        "ws://localhost:20003api/openclaw/ws"
    )


def test_the_requested_port_is_honoured():
    assert _ws_url(port="20010", path="/api/openclaw/ws").startswith(
        "ws://localhost:20010/"
    )


def test_an_omitted_path_falls_back_to_the_engine_default():
    """``path`` is always sent by this repo's client, but the mock is shared —
    a caller that omits it gets the openclaw socket rather than a URL with no
    path at all."""
    assert _ws_url(port="20003") == "ws://localhost:20003/api/openclaw/ws"

"""Unit tests for the prod ``HttpxClient``.

``HttpxClient`` is a base_url-scoped wrapper that owns **one pooled**
``httpx.Client`` for the life of the instance: every call goes through the same
client, ``None`` args are omitted so the wire shape matches a hand-written httpx
call, and ``timeout`` rides on the request rather than the constructor.

Two layers here. The first patches ``httpx.Client`` outright and asserts on
construction arguments and delegation — what ``HttpxClient`` *asks httpx to do*.
The second (see "real-client behaviour" below) drives a real ``httpx.Client``
over an ``httpx.MockTransport``, built through the production
``_pooled_client()`` path so production's own arguments are the ones under test.

``HttpxClient`` takes no injectable transport — that seam was production surface
only tests used and was deliberately removed (see
``specs/2026-08-26-http-client-connection-pooling/spec.md``). The second layer
needs none: patching the constructor to add ``transport=`` reaches the same
place without the class knowing about it.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.plugins.http_client import HttpxClient


@contextmanager
def _patched_httpx():
    """Patch ``httpx.Client``; yield (constructor mock, single client mock).

    The constructor returns the *same* client mock every time, so a test that
    expects pooling asserts on ``ctor.call_count`` rather than on identity of
    distinct mocks.
    """
    inner = MagicMock(name="httpx_client")
    inner.request.return_value = MagicMock(name="response")
    with patch("httpx.Client", return_value=inner) as ctor:
        yield ctor, inner


# ── pooling ──────────────────────────────────────────────────────────────────


def test_pool_is_reused_across_calls():
    """Two calls share one underlying client — no client per call."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, inner):
        client.get("/a")
        client.post("/b", json={"x": 1})
        client.get("/c")
    assert ctor.call_count == 1, "expected one pooled client, not one per call"
    assert inner.request.call_count == 3
    # The pooled client is never closed by a request path.
    inner.close.assert_not_called()


def test_client_is_built_with_configured_limits():
    client = HttpxClient(
        base_url="http://svc.test",
        max_connections=37,
        max_keepalive_connections=11,
        keepalive_expiry=2.5,
    )
    with _patched_httpx() as (ctor, _inner):
        client.get("/ping")
    limits = ctor.call_args.kwargs["limits"]
    assert limits.max_connections == 37
    assert limits.max_keepalive_connections == 11
    assert limits.keepalive_expiry == 2.5


def test_client_is_scoped_to_base_url():
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, _inner):
        client.get("/ping")
    assert ctor.call_args.kwargs["base_url"] == "http://svc.test"


def test_concurrent_first_calls_build_exactly_one_client():
    """The double-checked lock must not let a race build two pools.

    Callers reach this seam from ``asyncio.to_thread`` worker threads, so the
    first call genuinely can race.
    """
    client = HttpxClient(base_url="http://svc.test")
    built: list[object] = []
    barrier = threading.Barrier(16)

    def make_client(*_args, **_kwargs):
        made = MagicMock(name="httpx_client")
        built.append(made)
        return made

    def worker():
        barrier.wait()
        client._pooled_client()

    with patch("httpx.Client", side_effect=make_client):
        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(built) == 1, f"race built {len(built)} clients; expected exactly 1"


# ── http2 ────────────────────────────────────────────────────────────────────


def test_http2_defaults_off_and_is_forwarded_when_enabled():
    with _patched_httpx() as (ctor, _inner):
        HttpxClient(base_url="http://svc.test").get("/ping")
    assert ctor.call_args.kwargs["http2"] is False

    with _patched_httpx() as (ctor, _inner):
        HttpxClient(base_url="http://svc.test", http2=True).get("/ping")
    assert ctor.call_args.kwargs["http2"] is True


def test_http2_client_constructs_for_real():
    """``h2`` must actually be installed — httpx does ``if http2: import h2`` in
    its constructor, so a missing extra fails at pool construction, not at
    request time. No request is issued, so no network is touched."""
    client = HttpxClient(base_url="http://svc.test", http2=True)
    try:
        assert client._pooled_client() is not None
    finally:
        client.close()


# ── request shape ────────────────────────────────────────────────────────────


def test_post_passes_all_args_through():
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        out = client.post(
            "/api/v1/bots",
            params={"tenant": "t"},
            json={"a": 1},
            headers={"h": "v"},
            timeout=12.0,
        )
    inner.request.assert_called_once_with(
        "POST",
        "/api/v1/bots",
        timeout=12.0,
        params={"tenant": "t"},
        json={"a": 1},
        headers={"h": "v"},
    )
    assert out is inner.request.return_value


def test_post_with_files_and_data_passes_multipart_kwargs():
    """post(files=, data=) must forward files/data (multipart), so write_file's
    multipart shape can route through invoke_http."""
    client = HttpxClient(base_url="http://svc.test")
    files = {"file": ("foo.py", b"print(1)")}
    data = {"path": "/tmp/foo.py"}
    with _patched_httpx() as (_ctor, inner):
        client.post("/upload", files=files, data=data, headers={"h": "v"})
    inner.request.assert_called_once_with(
        "POST", "/upload", timeout=30.0, files=files, data=data, headers={"h": "v"}
    )


def test_verbs_dispatch_correct_methods():
    # A fresh client per verb: one instance caches its pool after the first
    # call, so it would keep using the previous iteration's patched mock.
    for verb, call in [
        ("GET", lambda c: c.get("/g", params={"q": "1"})),
        ("PUT", lambda c: c.put("/p", json={"b": 2})),
        ("PATCH", lambda c: c.patch("/p", json={"b": 3})),
        ("DELETE", lambda c: c.delete("/d")),
    ]:
        client = HttpxClient(base_url="http://svc.test")
        with _patched_httpx() as (_ctor, inner):
            call(client)
        assert inner.request.call_args.args[0] == verb


def test_none_args_are_omitted_from_the_request():
    """A call with no params/json/headers must not pass those kwargs, so the wire
    shape matches a bare ``client.get(path)``."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        client.get("/ping")
    inner.request.assert_called_once_with("GET", "/ping", timeout=30.0)


def test_timeout_is_passed_per_request_not_per_client():
    """The pooled client outlives the call, so ``timeout`` must ride on the
    request — passing it to the constructor would apply one call's budget to
    every later call."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, inner):
        client.get("/a", timeout=3.0)
        client.get("/b", timeout=17.0)
    assert "timeout" not in ctor.call_args.kwargs
    assert [c.kwargs["timeout"] for c in inner.request.call_args_list] == [3.0, 17.0]


def test_absolute_url_is_passed_through_unaltered():
    """The ``general`` binding is constructed with ``base_url=""`` and callers
    pass absolute URLs; the absolute URL must reach ``Client.request`` as-is."""
    client = HttpxClient(base_url="")
    with _patched_httpx() as (_ctor, inner):
        client.get("http://container.test:20010/api/file/list")
    inner.request.assert_called_once_with(
        "GET", "http://container.test:20010/api/file/list", timeout=30.0
    )


def test_response_and_transport_errors_propagate():
    """The wrapper swallows nothing: raise_for_status errors and transport
    errors surface to the caller unchanged."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        inner.request.side_effect = httpx.ConnectError("down")
        with pytest.raises(httpx.ConnectError):
            client.post("/x", json={})


def test_pool_timeout_classifies_as_a_boundary_timeout():
    """Pool exhaustion is the one new failure mode pooling introduces, and it
    must surface as an existing boundary error rather than a new type.

    Scope: this pins that a raised ``PoolTimeout`` is caught by the
    ``HttpClientTimeoutError`` alias and propagates through ``_request``
    unwrapped. It does NOT exercise a real pool running out of connections —
    ``MockTransport`` bypasses httpcore's pool entirely, so provoking a genuine
    ``PoolTimeout`` would need a real server and a timing-sensitive test."""
    from agentclaw.community.plugin_api.http_client import HttpClientTimeoutError

    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        inner.request.side_effect = httpx.PoolTimeout("no free connection")
        with pytest.raises(HttpClientTimeoutError):
            client.get("/x")


# ── streaming ────────────────────────────────────────────────────────────────


def test_stream_shares_the_pool_and_leaves_it_open():
    """``stream`` must use the pooled client and must not close it; the
    connection returns to the pool when the inner block exits."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, inner):
        with client.stream("POST", "/v1/chat", json={"m": "x"}, timeout=5.0) as resp:
            assert resp is inner.stream.return_value.__enter__.return_value
        client.get("/after")

    inner.stream.assert_called_once_with("POST", "/v1/chat", timeout=5.0, json={"m": "x"})
    assert ctor.call_count == 1, "stream must reuse the pooled client"
    # Not closed: the connection returns to the pool, the client stays usable.
    inner.close.assert_not_called()


# ── lifecycle ────────────────────────────────────────────────────────────────


def test_close_is_idempotent_and_rebuilds_on_next_use():
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, inner):
        client.get("/a")
        client.close()
        client.close()  # idempotent — must not raise or double-close
        client.get("/b")
    inner.close.assert_called_once()
    assert ctor.call_count == 2, "a call after close should build a fresh pool"


def test_teardown_closes_the_pool():
    """Lifecycle phase 2 releases the connections."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        client.get("/a")
        asyncio.run(client.teardown())
    inner.close.assert_called_once()
    assert client._client is None


def test_teardown_without_a_pool_is_a_noop():
    """Discovery resolves every binding at boot, so teardown can run on an
    instance that never served a request."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, _inner):
        asyncio.run(client.teardown())
    ctor.assert_not_called()


# ── real-client behaviour ────────────────────────────────────────────────────
#
# The tests above patch ``httpx.Client`` and so pin only what ``HttpxClient``
# asks httpx to do. These drive a REAL ``httpx.Client`` over an
# ``httpx.MockTransport`` by seeding the pool directly, which needs no
# constructor seam — the production class keeps exactly the arguments the
# composition root passes. That matters most for ``stream()``, which the pooling
# rewrite changed and which nothing else exercises end to end.


def _seeded(handler, base_url: str = "http://svc.test", **kwargs) -> HttpxClient:
    """An ``HttpxClient`` whose pool is a real client over ``MockTransport``.

    The pool is built through the production ``_pooled_client()`` path, with only
    ``transport=`` added to whatever kwargs production passes. Hand-assembling
    the client here instead would silently drop production's own arguments —
    notably ``cookies=_DiscardingCookieJar()`` — and leave the tests below
    asserting against a client the production code never builds.
    """
    real_ctor = httpx.Client

    def ctor(**client_kwargs):
        return real_ctor(**client_kwargs, transport=httpx.MockTransport(handler))

    client = HttpxClient(base_url=base_url, **kwargs)
    with patch("httpx.Client", side_effect=ctor):
        client._pooled_client()
    return client


def test_real_client_stream_yields_a_streaming_response():
    """``stream`` drives a real client end to end: SSE lines arrive and
    ``raise_for_status`` works inside the block."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"choices": [{"delta": {"content": "hi"}}]}\ndata: [DONE]\n'
        return httpx.Response(200, content=body.encode())

    client = _seeded(handler, "http://llm.local")
    try:
        with client.stream("POST", "/v1/chat/completions", json={"model": "x"}) as resp:
            assert resp.status_code == 200
            resp.raise_for_status()
            lines = [ln for ln in resp.iter_lines() if ln]
        assert any('"content": "hi"' in ln for ln in lines)
        assert any(ln.startswith("data: [DONE]") for ln in lines)
        # The pool survives the stream and still serves ordinary calls.
        assert client.get("/after").status_code == 200
    finally:
        client.close()


def test_real_client_stream_propagates_status_errors():
    """``raise_for_status`` inside the block surfaces unchanged."""
    client = _seeded(lambda _r: httpx.Response(500, text="boom"), "http://llm.local")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            with client.stream("POST", "/v1/chat/completions", json={}) as resp:
                resp.raise_for_status()
    finally:
        client.close()


def test_real_client_does_not_replay_cookies_between_calls():
    """Regression: a pooled client keeps a cookie jar for its whole lifetime, so
    without an explicit discard one caller's ``Set-Cookie`` rides along on every
    later caller's request. The ``general`` binding carries per-user credentials
    to a shared upstream, so that would leak one user's session to the next."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"set-cookie": "session=USER_A_SECRET; Path=/"},
            json={"cookie_sent": request.headers.get("cookie", "")},
        )

    client = _seeded(handler)
    try:
        assert client.get("/first").json()["cookie_sent"] == ""
        assert client.get("/second").json()["cookie_sent"] == "", (
            "a Set-Cookie from an earlier call leaked onto a later one"
        )
        assert dict(client._client.cookies) == {}
    finally:
        client.close()


def test_real_client_sends_the_documented_wire_shape():
    """End-to-end guard that the kwargs forwarded to httpx are actually valid —
    the patched-client tests would not catch a misspelled kwarg."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["header"] = request.headers.get("h")
        seen["body"] = request.content
        return httpx.Response(200, json={})

    client = _seeded(handler)
    try:
        client.post("/api/v1/bots", params={"t": "1"}, json={"a": 1},
                    headers={"h": "v"}, timeout=9.0)
    finally:
        client.close()
    assert seen["method"] == "POST"
    assert seen["url"] == "http://svc.test/api/v1/bots?t=1"
    assert seen["header"] == "v"
    assert b'"a"' in seen["body"]


def test_real_client_absolute_url_ignores_base_url():
    """The ``general`` binding's contract, against a real client rather than a
    mock: an absolute URL must not be prefixed with ``base_url``."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    client = _seeded(handler, base_url="")
    try:
        client.get("http://container.test:20010/api/file/list")
    finally:
        client.close()
    assert seen["url"] == "http://container.test:20010/api/file/list"

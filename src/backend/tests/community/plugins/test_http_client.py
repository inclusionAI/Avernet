"""Unit tests for the prod ``HttpxClient``.

``HttpxClient`` is a thin, base_url-scoped wrapper over ``httpx.Client``: it
builds one client at construction and reuses it for every call, issuing requests
against a relative path and omitting ``None`` args so the wire shape matches a
hand-written httpx call. We patch ``httpx.Client`` to assert the construction +
request shape without any network.

Because the client is now built in ``__init__``, each test constructs
``HttpxClient`` *inside* the patch context — otherwise the real ``httpx.Client``
would be captured before the patch applies.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.plugins.http_client import HttpxClient


@contextmanager
def _patched_httpx():
    """Patch ``httpx.Client`` and yield ``(ctor, client_instance)``."""
    inner = MagicMock(name="httpx_client")
    inner.request.return_value = MagicMock(name="response")
    with patch("httpx.Client", return_value=inner) as ctor:
        yield ctor, inner


def test_post_builds_base_url_client_and_passes_all_args():
    with _patched_httpx() as (ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        out = client.post(
            "/api/v1/bots",
            params={"tenant": "t"},
            json={"a": 1},
            headers={"h": "v"},
            timeout=12.0,
        )
    # Client is constructed base_url-scoped. The timeout is NOT set here — it is
    # per-call, and baking it in would freeze every caller's deadline.
    ctor.assert_called_once_with(base_url="http://svc.test")
    # Request issued against the relative path with every provided arg plus the
    # per-call timeout.
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
    """post(files=, data=) must forward files/data to httpx request (multipart),
    so write_file's multipart shape can route through invoke_http later."""
    files = {"file": ("foo.py", b"print(1)")}
    data = {"path": "/tmp/foo.py"}
    with _patched_httpx() as (_ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        client.post("/upload", files=files, data=data, headers={"h": "v"})
    inner.request.assert_called_once_with(
        "POST", "/upload", timeout=30.0, files=files, data=data, headers={"h": "v"}
    )


def test_get_and_put_dispatch_correct_methods():
    with _patched_httpx() as (_ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        client.get("/g", params={"q": "1"})
    inner.request.assert_called_once_with("GET", "/g", timeout=30.0, params={"q": "1"})

    with _patched_httpx() as (_ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        client.put("/p", json={"b": 2})
    inner.request.assert_called_once_with("PUT", "/p", timeout=30.0, json={"b": 2})


def test_none_args_are_omitted_from_the_request():
    """A call with no params/json/headers must not pass those kwargs (so the wire
    shape matches a bare ``client.get(path)``)."""
    with _patched_httpx() as (ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        client.get("/ping")
    ctor.assert_called_once_with(base_url="http://svc.test")
    inner.request.assert_called_once_with("GET", "/ping", timeout=30.0)


def test_response_and_transport_errors_propagate():
    """The wrapper swallows nothing: raise_for_status errors and transport errors
    surface to the caller unchanged."""
    import httpx

    with _patched_httpx() as (_ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        inner.request.side_effect = httpx.ConnectError("down")
        with pytest.raises(httpx.ConnectError):
            client.post("/x", json={})


def test_client_is_constructed_once_and_reused_across_calls():
    """The actual fix: two calls share one client instead of opening two.

    Without reuse each request paid a fresh TCP + TLS handshake against a host
    it had just disconnected from.
    """
    with _patched_httpx() as (ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        client.get("/one")
        client.get("/two")

    ctor.assert_called_once_with(base_url="http://svc.test")
    assert inner.request.call_count == 2


def test_per_call_timeouts_are_independent():
    """Two calls on the same client carry their own deadlines."""
    with _patched_httpx() as (_ctor, inner):
        client = HttpxClient(base_url="http://svc.test")
        client.get("/fast", timeout=1.0)
        client.get("/slow", timeout=60.0)

    assert [c.kwargs["timeout"] for c in inner.request.call_args_list] == [1.0, 60.0]

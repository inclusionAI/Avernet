"""Unit tests for the prod ``HttpxClient``.

``HttpxClient`` is a thin, base_url-scoped wrapper over ``httpx.Client``: each
call opens a short-lived client and issues the request against a relative path,
omitting ``None`` args so the wire shape matches a hand-written httpx call. We
patch ``httpx.Client`` to assert the construction + request shape without any
network.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.plugins.http_client import HttpxClient


@contextmanager
def _patched_httpx():
    """Patch ``httpx.Client`` and yield the inner (context-managed) client mock."""
    inner = MagicMock(name="httpx_client")
    inner.request.return_value = MagicMock(name="response")
    cm = MagicMock()
    cm.__enter__.return_value = inner
    cm.__exit__.return_value = False
    with patch("httpx.Client", return_value=cm) as ctor:
        yield ctor, inner


def test_post_builds_base_url_client_and_passes_all_args():
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, inner):
        out = client.post(
            "/api/v1/bots",
            params={"tenant": "t"},
            json={"a": 1},
            headers={"h": "v"},
            timeout=12.0,
        )
    # Client is constructed base_url-scoped with the per-call timeout.
    ctor.assert_called_once_with(base_url="http://svc.test", timeout=12.0)
    # Request issued against the relative path with every provided arg.
    inner.request.assert_called_once_with(
        "POST", "/api/v1/bots", params={"tenant": "t"}, json={"a": 1}, headers={"h": "v"}
    )
    assert out is inner.request.return_value


def test_post_with_files_and_data_passes_multipart_kwargs():
    """post(files=, data=) must forward files/data to httpx request (multipart),
    so write_file's multipart shape can route through invoke_http later."""
    client = HttpxClient(base_url="http://svc.test")
    files = {"file": ("foo.py", b"print(1)")}
    data = {"path": "/tmp/foo.py"}
    with _patched_httpx() as (_ctor, inner):
        client.post("/upload", files=files, data=data, headers={"h": "v"})
    inner.request.assert_called_once_with(
        "POST", "/upload", files=files, data=data, headers={"h": "v"}
    )


def test_get_and_put_dispatch_correct_methods():
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        client.get("/g", params={"q": "1"})
    inner.request.assert_called_once_with("GET", "/g", params={"q": "1"})

    with _patched_httpx() as (_ctor, inner):
        client.put("/p", json={"b": 2})
    inner.request.assert_called_once_with("PUT", "/p", json={"b": 2})


def test_none_args_are_omitted_from_the_request():
    """A call with no params/json/headers must not pass those kwargs (so the wire
    shape matches a bare ``client.get(path)``)."""
    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (ctor, inner):
        client.get("/ping")
    ctor.assert_called_once_with(base_url="http://svc.test", timeout=30.0)  # default timeout
    inner.request.assert_called_once_with("GET", "/ping")


def test_response_and_transport_errors_propagate():
    """The wrapper swallows nothing: raise_for_status errors and transport errors
    surface to the caller unchanged."""
    import httpx

    client = HttpxClient(base_url="http://svc.test")
    with _patched_httpx() as (_ctor, inner):
        inner.request.side_effect = httpx.ConnectError("down")
        with pytest.raises(httpx.ConnectError):
            client.post("/x", json={})

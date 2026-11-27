"""A raw ``httpx.HTTPStatusError`` maps to the 502 upstream family.

The public identity/resources routes traverse ``DeviceFileSystem``
implementations that deliberately re-raise non-device-404 upstream statuses
(``BaasDeviceFileSystem.read_file`` keeps only the device's own 404 as
``None``, per the absence-is-empty-content contract). No service translates
those statuses into domain errors on the way up, so an upstream outage used
to surface as 500 ``[Unhandled exception]`` (CDO noise) while sibling routes
with translated errors answered their mapped 502s.

These tests pin both entry points that share ``ENVELOPE_ERRORS``:

- the app-level catch-all (``_unhandled_exception_handler``), which backs every
  error that escaped the decorator — raised inside a dependency or handler;
- the ``@envelope_errors`` decorator, which maps errors raised inside the
  handler itself (the identity route's actual path).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def _status_error(status: int) -> httpx.HTTPStatusError:
    """A realistic escapee: an upstream HTTP call answered a non-2xx status."""
    request = httpx.Request("POST", "https://baas.internal/api/file/read")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"Server error '{status}' for url 'https://baas.internal/api/file/read'",
        request=request,
        response=response,
    )


def _build_catchall_app(upstream_status: int) -> FastAPI:
    # Mirror the prod handler wiring exactly — import the catch-all from
    # api.app so test and prod stay in sync (same pattern as
    # tests/community/api/test_domain_error_handler.py).
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/openapi/v1/bots/{bot_id}/identity")
    async def raise_status_error(bot_id: str):
        raise _status_error(upstream_status)

    return app


def _catchall_client(upstream_status: int) -> TestClient:
    # raise_server_exceptions=False so the catch-all Exception handler is
    # actually exercised instead of TestClient re-raising the error.
    return TestClient(
        _build_catchall_app(upstream_status), raise_server_exceptions=False
    )


def _build_decorator_app() -> FastAPI:
    from agentclaw.community.adapters.http.openapi_v1.error_decorator import (
        envelope_errors,
    )

    app = FastAPI()

    @app.get("/openapi/v1/bots/{bot_id}/identity")
    @envelope_errors
    async def handler(bot_id: str, request: Request) -> object:
        # The wire type is the envelope's model; the error path never returns.
        raise _status_error(504)

    return app


@pytest.fixture(scope="module")
def decorator_client():
    return TestClient(
        _build_decorator_app(), raise_server_exceptions=False
    )


# The mapping is by exception *type*, not by upstream status: every
# non-2xx answer that no service translated on the way up lands on the same
# fixed 502. A 404 belongs on the list because the device layer consumes only
# *device-level* 404s (absence is empty content); a 404 that still escapes —
# e.g. a desktop proxy routing failure the layer must keep observable — says
# "the bot/device is gone", which is an upstream unavailability too. Upstream
# detail never reaches the wire: the message is fixed by the table entry.
@pytest.mark.parametrize("upstream_status", [401, 403, 404, 502, 503, 504])
def test_catchall_maps_upstream_status_error_to_502_envelope(upstream_status):
    client = _catchall_client(upstream_status)
    resp = client.get("/openapi/v1/bots/b/identity")
    assert resp.status_code == 502
    assert resp.json() == {
        "code": 502000,
        "message": "Upstream service error",
        "data": None,
        "request_id": "",
    }


def test_catchall_logs_mapped_502_with_traceback(caplog):
    client = _catchall_client(502)
    with caplog.at_level("ERROR"):
        resp = client.get("/openapi/v1/bots/b/identity")
    assert resp.status_code == 502
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "[Public 502]" in message and "HTTPStatusError" in message
        for message in messages
    )


def test_decorator_maps_upstream_status_error_to_502_envelope(decorator_client):
    resp = decorator_client.get("/openapi/v1/bots/b/identity")
    assert resp.status_code == 502
    assert resp.json() == {
        "code": 502000,
        "message": "Upstream service error",
        "data": None,
        "request_id": "",
    }


def test_decorator_logs_mapped_502_with_traceback(decorator_client, caplog):
    with caplog.at_level("ERROR"):
        resp = decorator_client.get("/openapi/v1/bots/b/identity")
    assert resp.status_code == 502
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "[Public 502]" in message and "HTTPStatusError" in message
        for message in messages
    )


def test_internal_api_keeps_detail_shape_for_untranslated_error():
    # ``_uses_envelope_contract`` narrows to the public surface: an internal
    # ``/api`` route never consults the public table (``_public_mapped_error``
    # returns ``None`` there), so its legacy ``{"detail": ...}`` 500 is
    # untouched by the new entry — internal clients' wire format is stable.
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/api/internal/probe")
    async def raise_status_error():
        raise _status_error(502)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/internal/probe")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal Server Error"}

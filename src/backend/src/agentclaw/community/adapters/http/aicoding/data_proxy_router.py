"""OCB backend → engine data-proxy router (transparent proxy).

Exposes the agentclaw management-backend ``/api/aicoding/data-proxy/*``
surface and forwards each request verbatim to ``{engine_base}/data/*``.

URL contract::

    ANY /api/aicoding/data-proxy/{subpath:path}

Caller note: open-claw (oc-web) does **not** hit this route for its
data plane — the frontend sends ``/data/*`` directly through proxypass
to the engine adapter (``:20003`` ``/data`` router), so this prefix is
bypassed. This route remains for server-side callers that forward to
the engine ``/data/*`` leg, mirroring the engine adapter's contract.

OCB does **one thing only**: forward the request verbatim to
``{engine_base}/data/{subpath}`` and return the response.

Container selection happens at the **upstream proxypass**, not in OCB.
The proxypass picks the right bot's container based on caller identity
/ session / token (e.g. ``x-proxypass-token``). OCB does not look up
any bot binding, does not inject any operator/bot header — anything
the caller sent in headers (including auth) passes through unchanged
(except hop-by-hop framing such as ``host`` / ``content-length``).

**This file is a thin HTTP shell.** Resolution + forwarding logic
lives in :class:`agentclaw.community.core.aicoding.services.data_proxy_service.
DataProxyService`. ``DataProxyError`` subclasses propagate to the
global exception handler in :mod:`agentclaw.community.adapters.http.app`, which
owns the per-class status mapping and the response shape the caller
expects.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from agentclaw.community.api.data_proxy_service import DataProxyServiceProtocol
# ``StreamingForwardResult`` is a pure domain dataclass (a result type,
# like ``SyncResult``), not a service instance — importing it across the
# adapter boundary is the documented R8 exception (see
# ``_CORE_SERVICE_NAMES_OK`` in
# ``tests/architecture/test_http_adapter_layer_is_http_only.py``). The
# router needs the concrete type to branch buffered-vs-streamed; the
# service instance is still resolved via ``Injected(...Protocol)``.
from agentclaw.community.core.aicoding.services.data_proxy_service import (
    StreamingForwardResult,
)
from agentclaw.community.di import Injected

log = logging.getLogger("aicoding-data-proxy")


router = APIRouter(prefix="/api/aicoding/data-proxy", tags=["aicoding-data-proxy"])


@router.api_route(
    "/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    summary="把请求透传到 engine /data/* (proxypass 自动选容器)",
)
async def proxy_to_engine_data(
    subpath: str,
    request: Request,
    service: DataProxyServiceProtocol = Injected(DataProxyServiceProtocol),
) -> Response:
    """Forward ``request`` to ``{engine_base}/data/{subpath}``.

    No service-token check: OCB is a transparent proxy. Auth on the
    data plane is owned by harness-data on the receiving side and by
    the proxypass on the routing side. ``DataProxyError`` raised by
    the service is translated to HTTP by the global handler.
    """
    body = await request.body()
    result = await service.forward(
        subpath=subpath,
        method=request.method,
        headers=request.headers,
        query_string=request.url.query,
        body=body,
    )

    # SSE / long-lived upstreams stream through chunk-by-chunk; the
    # service's body iterator owns closing the upstream connection.
    # Without this branch a ``text/event-stream`` upstream 500s on the
    # missing ``.content`` and, even if it didn't, would be buffered
    # until the stream ends — defeating SSE.
    if isinstance(result, StreamingForwardResult):
        return StreamingResponse(
            result.body,
            status_code=result.status_code,
            headers=result.headers,
            media_type=result.media_type,
        )

    return Response(
        content=result.content,
        status_code=result.status_code,
        headers=result.headers,
        media_type=result.media_type,
    )

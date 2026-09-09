"""Session file-transfer proxy routes — the second leg of the aliyun deployment.

A wildcard GET/PUT route under ``/api/v1/file-transfer-proxy/{oss_path:path}``
streams client requests to the configured OSS endpoint through the injected
``OssStreamingProxy``.  The signature trinity (raw path, raw query string,
Content-Type) is read from the ASGI scope byte surfaces and never decoded —
the decoded URL surface is forbidden here (decode-then-forward breaks OSS V1
signatures).

The router stays thin (Rule 7): it slices the raw scope, delegates to the
proxy, and maps failures onto the structured error ladder.
"""

import anyio
import httpx
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from secbaas.community.api.session_file_sharing import (
    SessionFileTransferProxyUnavailableError,
)
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger
from secbaas.community.plugins.file_transfer import OssStreamingProxy

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/file-transfer-proxy", tags=["Session文件共享"])

_PREFIX = b"/api/v1/file-transfer-proxy"


@router.api_route("/{oss_path:path}", methods=["GET", "PUT"])
@inject
async def proxy_oss(
    request: Request,
    oss_path: str,
    proxy: OssStreamingProxy = Depends(
        Provide[ApplicationContainer.services.oss_streaming_proxy]
    ),
) -> StreamingResponse:
    """Stream the client request to the OSS upstream and relay the reply.

    Only the raw ``scope["raw_path"]`` / ``scope["query_string"]`` byte
    surfaces are read — never the decoded URL surface (its decoded path
    would break the OSS V1 signature).  Non-GET/PUT methods are rejected by
    the route's method whitelist (405) before this handler runs.
    """
    raw_path: bytes = request.scope["raw_path"]
    query: bytes = request.scope["query_string"]
    if not raw_path.startswith(_PREFIX + b"/"):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "FILE_PROXY_BAD_PATH",
                "message": "unexpected proxy path prefix",
            },
        )
    tail = raw_path[len(_PREFIX) :]
    headers = dict(request.headers)
    content_length = request.headers.get("content-length")
    body_stream = request.stream() if request.method == "PUT" else None

    try:
        status_code, resp_headers, body = await proxy.forward(
            method=request.method,
            path=tail,
            query=query,
            headers=headers,
            body_stream=body_stream,
            content_length=content_length,
        )
    except SessionFileTransferProxyUnavailableError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": e.error_code,
                "message": str(e),
                "reason": e.reason,
            },
        )
    except (ClientDisconnect, anyio.ClosedResourceError) as e:
        # A client aborting mid-upload (mobile clients, flaky networks in the
        # aliyun deployment where PUT is the primary path) is not a server
        # fault: log at info instead of letting the generic handler record a
        # critical 500 for a client that is already gone.
        logger.info("file-transfer proxy: client aborted mid-upload: %s", e)
        raise HTTPException(
            status_code=499,
            detail={
                "error_code": "FILE_PROXY_CLIENT_ABORT",
                "message": "client closed the upload connection",
            },
        )
    except httpx.HTTPError:
        logger.exception("file-transfer proxy: upstream OSS endpoint unreachable")
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "FILE_PROXY_UPSTREAM_ERROR",
                "message": "upstream OSS endpoint unreachable",
            },
        )
    return StreamingResponse(
        body,
        status_code=status_code,
        headers={**resp_headers, "X-Accel-Buffering": "no"},
    )

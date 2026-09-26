"""``/data/*`` reverse-proxy router (api 层).

把引擎适配器（:20003）上的 ``/data/*`` 请求交给当前引擎的
:class:`DataProxyService` 处理。具体转发逻辑由 plugin 独立实现——本文件
只做三件事：

1. ``check_capability(DATA_PROXY_FORWARD)``——当前引擎没声明该能力时直接 501。
2. 从 :class:`engine.community.manager.EngineManager` 拿到激活引擎，再从引擎实例上
   取得 :class:`DataProxyService`。``data_proxy`` 是 **engine-specific**
   的扩展属性，不在 :class:`engine.community.core.engine.protocol.Engine` Protocol
   里——只有声明 ``DATA_PROXY_FORWARD`` capability 的引擎才会暴露它。
3. 把请求字段（method / headers / body / query）原样交给 ``plugin.forward``，
   把 :class:`ForwardResult` 渲染成 ``Response``。

aicoding 引擎暴露 :class:`AiCodingDataProxyService` 转发到 harness-data；
其它引擎若没有 ``/data/*`` 上游就别声明这个 capability，
``check_capability`` 会把请求统一 501 掉，不需要 router 里硬编码引擎名。

错误模型
--------
``DataProxyService.forward`` 抛 :class:`DataProxyError` 子类（语义异常，
不带 HTTP 状态）。本文件把每个子类映射到合适的 HTTP 状态——目前
:class:`UpstreamUnreachable` → 502，其它子类回落 500。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from engine.community.api.caps import check_capability
from engine.community.core.data_proxy.protocol import (
    DataProxyError,
    DataProxyService,
    StreamingForwardResult,
    UpstreamUnreachable,
)
from engine.community.core.engine.capability import Capability
from engine.community.manager import EngineManager

log = logging.getLogger("aicoding-data-proxy")


router = APIRouter(prefix="/data", tags=["aicoding-data"])


# DataProxyError 子类 → HTTP 状态。未映射的子类回落 500。
_ERROR_STATUS: dict[type[DataProxyError], int] = {
    UpstreamUnreachable: 502,
}


def _to_http_exception(exc: DataProxyError) -> HTTPException:
    return HTTPException(
        status_code=_ERROR_STATUS.get(type(exc), 500),
        detail={"error": exc.message},
    )


def _resolve_plugin() -> DataProxyService:
    """Return the active engine's data_proxy plugin.

    ``data_proxy`` is engine-specific (only the aicoding engine ships
    one), so we read it off the active engine instance directly rather
    than promoting it to a generic ``EngineManager`` property. The
    capability gate is the contract; this lookup is the implementation.

    Raises 500 if the gate let a request through but the active engine
    has no ``data_proxy`` attribute — that means the engine declared the
    capability without wiring the plugin, which is a setup bug.
    """
    engine = EngineManager.get_instance()._require_engine()
    plugin = getattr(engine, "data_proxy", None)
    if plugin is None:
        log.error(
            "[data-proxy] engine %r declares DATA_PROXY_FORWARD but did "
            "not expose a `data_proxy` attribute — engine setup bug",
            getattr(engine, "name", "<unknown>"),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": (
                    "active engine declares DATA_PROXY_FORWARD but did "
                    "not wire a data_proxy plugin"
                ),
            },
        )
    return plugin


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    summary="转发 /data/* 到当前引擎的 DataProxyService",
)
async def proxy_to_harness_data(path: str, request: Request) -> Response:
    """把 ``/data/<path>`` 交给当前引擎的 :class:`DataProxyService`."""
    # Capability gate — engines that don't declare DATA_PROXY_FORWARD
    # short-circuit to 501 here. No engine-name hardcoding.
    check_capability(Capability.DATA_PROXY_FORWARD)

    plugin = _resolve_plugin()
    body = await request.body()
    try:
        result = await plugin.forward(
            path=path,
            method=request.method,
            headers=request.headers,
            query_string=request.url.query,
            body=body,
        )
    except DataProxyError as exc:
        raise _to_http_exception(exc) from exc

    # SSE / long-lived upstreams stream through chunk-by-chunk; the
    # plugin's body iterator owns closing the upstream connection.
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

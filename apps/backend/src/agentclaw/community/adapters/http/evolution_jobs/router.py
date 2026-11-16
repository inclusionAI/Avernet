"""作业协议路由：`/openapi/v1/evolution/jobs`（06 §7、§10）。

FastAPI 承载：解析 DI 通道、映射请求体、把 `JobUnauthorized` 变成 401。
线协议与 worker 客户端由 `core.evolution_run.job_protocol` 与
`core.evolution_run.job_wire` 定义；这里只负责 HTTP 语义。

沙箱内 worker 只经此路由与平台交互；`build_job_app` 是独立子应用，
供 worker 测试与本地 simulator 挂载，主应用以 `Injected(JobChannel)`
解析器直接挂载此路由。
"""

from __future__ import annotations

import base64
import functools
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.responses import Response as StarletteResponse

from agentclaw.community.core.evolution_run.job_protocol import (
    JobChannel,
    JobUnauthorized,
)
from agentclaw.community.core.evolution_run.job_wire import encode_files
from agentclaw.community.di import Injected

PREFIX = "/openapi/v1/evolution/jobs"


def build_job_router(get_channel: Callable[[], Any], *,
                     prefix: str = "") -> APIRouter:
    """按通道解析器构造路由；主应用传 DI 解析器，测试与独立应用传闭包。"""

    router = APIRouter(prefix=prefix, tags=["evolution-jobs"])

    def channel_dep(channel: Any = Depends(get_channel)) -> Any:
        return channel

    def _guarded(fn):
        """把 `JobUnauthorized` 变成 401；保留签名以便依赖注入照常工作。"""

        @functools.wraps(fn)
        async def wrapper(*a: Any, **kw: Any) -> Any:
            try:
                return await fn(*a, **kw)
            except JobUnauthorized:  # noqa: BLE001
                return JSONResponse({"error": {"code": "job_unauthorized"}},
                                    status_code=401)

        return wrapper

    @router.post("/jobs:claim")
    @_guarded
    async def claim(payload: dict[str, Any], channel: Any = Depends(channel_dep)) -> Response:
        grant = await channel.claim(payload["worker_id"], payload["token"],
                                    list(payload.get("strategy_refs") or []))
        if grant is None:
            return Response(status_code=204)
        return JSONResponse(grant)

    @router.post("/jobs/{job_id}:heartbeat")
    @_guarded
    async def heartbeat(job_id: str, payload: dict[str, Any],
                        channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.heartbeat(job_id, payload["fencing_token"]))

    @router.post("/jobs/{job_id}/files")
    @_guarded
    async def files(job_id: str, payload: dict[str, Any],
                    channel: Any = Depends(channel_dep)) -> JSONResponse:
        contents = await channel.files(job_id, payload["fencing_token"])
        return JSONResponse({"files": encode_files(contents)})

    @router.post("/jobs/{job_id}/candidates")
    @_guarded
    async def submit(job_id: str, payload: dict[str, Any],
                     channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(
            await channel.submit(job_id, payload["fencing_token"], payload["patch"]))

    @router.get("/jobs/{job_id}/candidates/{candidate_id}/verdict")
    @_guarded
    async def verdict(job_id: str, candidate_id: str, fencing_token: str,
                      channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.verdict(job_id, fencing_token, candidate_id))

    @router.post("/jobs/{job_id}:complete")
    @_guarded
    async def complete(job_id: str, payload: dict[str, Any],
                       channel: Any = Depends(channel_dep)) -> StarletteResponse:
        await channel.complete(job_id, payload["fencing_token"],
                               payload.get("summary") or {})
        return StarletteResponse(status_code=204)

    @router.post("/jobs/{job_id}:fail")
    @_guarded
    async def fail(job_id: str, payload: dict[str, Any],
                   channel: Any = Depends(channel_dep)) -> StarletteResponse:
        await channel.fail(job_id, payload["fencing_token"], payload.get("reason") or "")
        return StarletteResponse(status_code=204)

    @router.post("/jobs/{job_id}/experience/sessions")
    @_guarded
    async def experience_sessions(job_id: str, payload: dict[str, Any],
                                  channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.experience_sessions(
            job_id, payload["fencing_token"],
            days=int(payload.get("days", 7)),
            limit=int(payload.get("limit", 500)),
            revision=payload.get("revision"),
        ))

    @router.post("/jobs/{job_id}/experience/feedback")
    @_guarded
    async def experience_feedback(job_id: str, payload: dict[str, Any],
                                  channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.experience_feedback(
            job_id, payload["fencing_token"],
            days=int(payload.get("days", 7)),
            kinds=payload.get("kinds"),
            limit=int(payload.get("limit", 500)),
        ))

    @router.post("/jobs/{job_id}/models:complete")
    @_guarded
    async def models_complete(job_id: str, payload: dict[str, Any],
                              channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.models_complete(
            job_id, payload["fencing_token"],
            messages=payload.get("messages") or [],
            model=payload.get("model"),
            max_tokens=int(payload.get("max_tokens", 4096)),
        ))

    @router.post("/jobs/{job_id}/agents:start")
    @_guarded
    async def agents_start(job_id: str, payload: dict[str, Any],
                           channel: Any = Depends(channel_dep)) -> JSONResponse:
        op_id = await channel.agents_start(
            job_id, payload["fencing_token"],
            definition=payload["definition"],
            workspace_id=payload["workspace_id"],
            prompt=payload.get("prompt") or "",
            idempotency_key=payload["idempotency_key"],
            timeout_s=int(payload.get("timeout_s", 1800)),
        )
        return JSONResponse({"operation_id": op_id})

    @router.get("/jobs/{job_id}/operations/{operation_id}")
    @_guarded
    async def operation_get(job_id: str, operation_id: str, fencing_token: str,
                            channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.operation_get(job_id, fencing_token, operation_id))

    @router.post("/jobs/{job_id}/operations/{operation_id}:cancel")
    @_guarded
    async def operation_cancel(job_id: str, operation_id: str,
                               payload: dict[str, Any],
                               channel: Any = Depends(channel_dep)) -> JSONResponse:
        return JSONResponse(await channel.operation_cancel(
            job_id, payload["fencing_token"], operation_id))

    @router.post("/jobs/{job_id}/log")
    @_guarded
    async def log(job_id: str, payload: dict[str, Any],
                  channel: Any = Depends(channel_dep)) -> StarletteResponse:
        await channel.log(job_id, payload["fencing_token"],
                          payload.get("lines") or [])
        return StarletteResponse(status_code=204)

    @router.put("/jobs/{job_id}/artifacts/{name}")
    @_guarded
    async def artifacts_put(job_id: str, name: str, fencing_token: str,
                            request: Request,
                            channel: Any = Depends(channel_dep)) -> JSONResponse:
        content = await request.body()
        artifact_id = await channel.artifacts_put(
            job_id, fencing_token, name, content)
        return JSONResponse({"artifact_id": artifact_id})

    return router


def resolve_channel(channel: Any = Injected(JobChannel)) -> Any:
    """每条请求解析一次通道：单例，取的是同一个作业面。"""

    return channel


router = build_job_router(resolve_channel, prefix=PREFIX)


def build_job_app(channel: Any, *, prefix: str = "") -> FastAPI:
    """独立子应用（worker 测试与本地 simulator 用）。"""

    app = FastAPI(title="evolution job protocol")
    app.include_router(build_job_router(lambda: channel, prefix=prefix))
    return app

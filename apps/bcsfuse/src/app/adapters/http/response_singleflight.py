"""Coalesce an in-flight HTTP response burst before capacity admission."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

ExecuteDownstream = Callable[[Send], Awaitable[None]]
OnSharedResponse = Callable[["CapturedHTTPResponse"], None]


@dataclass(frozen=True, slots=True)
class CapturedHTTPResponse:
    status_code: int
    body: bytes


@dataclass(slots=True)
class _InflightHTTPResponse:
    future: asyncio.Future[CapturedHTTPResponse]


class ResponseBurstSingleflight:
    """Share one non-cacheable response with requests already waiting for it.

    Long-lived result caching must remain selective: degraded responses are not
    written to L1/L2. An outage burst still needs one expensive retrieval call,
    so requests that arrive before that first response completes replay the
    captured response without promoting it to cache.
    """

    def __init__(self) -> None:
        self._inflight: dict[str, _InflightHTTPResponse] = {}

    @property
    def in_flight_keys(self) -> int:
        return len(self._inflight)

    async def coalesce(
        self,
        *,
        key: str,
        scope: Scope,
        receive: Receive,
        send: Send,
        execute_downstream: ExecuteDownstream,
        on_shared_response: OnSharedResponse | None = None,
    ) -> None:
        if not key:
            raise ValueError("response singleflight key is required")

        entry = self._inflight.get(key)
        if entry is None:
            entry = _InflightHTTPResponse(asyncio.get_running_loop().create_future())
            self._inflight[key] = entry
            try:
                captured = await self._execute_and_capture(
                    send,
                    execute_downstream,
                )
            except BaseException as error:
                if not entry.future.done():
                    entry.future.set_exception(error)
                raise
            finally:
                if self._inflight.get(key) is entry:
                    self._inflight.pop(key, None)
            entry.future.set_result(captured)
            return

        captured = await entry.future
        if on_shared_response is not None:
            on_shared_response(captured)
        await Response(
            captured.body,
            status_code=captured.status_code,
            media_type="application/json",
        )(scope, receive, send)

    async def _execute_and_capture(
        self,
        send: Send,
        execute_downstream: ExecuteDownstream,
    ) -> CapturedHTTPResponse:
        status_code: int | None = None
        body_chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 0))
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
            await send(message)

        await execute_downstream(capture)
        if status_code is None:
            raise RuntimeError("response stream did not provide a status")

        return CapturedHTTPResponse(
            status_code=status_code,
            body=b"".join(body_chunks),
        )


__all__ = ["CapturedHTTPResponse", "ResponseBurstSingleflight"]

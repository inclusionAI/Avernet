"""The subset of the pooled async HTTP library required by content delivery."""

from typing import Any, AsyncContextManager, Protocol

import httpx


class AsyncContentTransport(Protocol):
    def stream(
        self, method: str, url: str, **kwargs: Any
    ) -> AsyncContextManager[httpx.Response]: ...
    def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request: ...
    async def send(
        self, request: httpx.Request, *, stream: bool = False
    ) -> httpx.Response: ...

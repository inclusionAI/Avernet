"""Forwarder Protocol — stream a request to an upstream and back."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from ._models import ForwardRequest, ForwardResponse


class Forwarder(Protocol):
    """Forwarder Plugin API contract version 2.

    Forwards a request to an upstream, streaming both the request and response.
    Version 2 replaced the version 1 buffered ``ForwardRequest.content`` field
    with ``ForwardRequest.body`` to keep large uploads bounded by transport
    backpressure. The public Python symbols retain their existing names.

    ``forward`` is an async context manager so the upstream connection is held
    open while the caller streams the body and released on exit::

        async with forwarder.forward(request) as response:
            async for chunk in response.body:
                ...

    ``ForwardRequest.body`` is one-shot. Once this context manager is entered,
    the implementation owns any body stream and closes it after the send
    completes, fails, or is cancelled. Implementations must not retry by
    replaying it. A caller-supplied ``Content-Length`` is preserved; when no
    length is known, the transport may select chunked transfer encoding.

    The current implementation is ``HttpxForwarder``, the httpx-backed default.
    """

    def forward(
        self, request: ForwardRequest
    ) -> AbstractAsyncContextManager[ForwardResponse]:
        """Open a streaming forward and take ownership of its request body."""
        ...

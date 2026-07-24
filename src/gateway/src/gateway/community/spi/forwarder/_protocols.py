"""Forwarder Protocol — stream a request to an upstream and back."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from ._models import ForwardRequest, ForwardResponse


class Forwarder(Protocol):
    """Forwards a request to an upstream, streaming the response.

    ``forward`` is an async context manager so the upstream connection is held
    open while the caller streams the body and released on exit::

        async with forwarder.forward(request) as response:
            async for chunk in response.body:
                ...

    Implementations:
    - BareForwarder: httpx-backed, streams raw bytes (open-source default).
    - a sofa flavor (enterprise) may add pooling/observability at this seam;
      the auth workstream attaches the signed principal here.
    """

    def forward(
        self, request: ForwardRequest
    ) -> AbstractAsyncContextManager[ForwardResponse]:
        """Open a streaming forward to the upstream named by ``request.url``."""
        ...

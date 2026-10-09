"""HTTP header plugin Protocol — outbound request header injection.

Implementations mutate the outbound ``headers`` dict, e.g. injecting
environment-specific authentication cookies. The community default is a
no-op plugin; deployments provide their own implementation via
``secbaas.community.http_header.set_http_header_plugin``.
"""

from __future__ import annotations

from typing import Protocol


class HttpHeaderPlugin(Protocol):
    """Plugin protocol for outbound HTTP header injection.

    Called before each outbound HTTP/WebSocket request with the headers
    assembled so far. Implementations mutate ``headers`` in place.
    """

    def inject_header(self, headers: dict[str, str]) -> None:
        """Inject extra headers into *headers* (in place).

        Args:
            headers: Outbound headers collected so far; mutated in place.
        """
        ...

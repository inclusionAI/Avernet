"""Default HTTP header plugin — injects nothing.

The community edition ships no outbound header injection. Deployments
register their own ``HttpHeaderPlugin`` via
``secbaas.community.http_header.set_http_header_plugin``.
"""

from __future__ import annotations

from secbaas.community.spi.http_header import HttpHeaderPlugin

__all__ = [
    "NoOpHttpHeaderPlugin",
]


class NoOpHttpHeaderPlugin(HttpHeaderPlugin):
    """No-op implementation: leaves outbound headers unchanged."""

    def inject_header(self, headers: dict[str, str]) -> None:
        return None

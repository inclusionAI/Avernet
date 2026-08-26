"""SPI protocol contracts for the sandbox-proxy.

These ports define the seams the community package exposes for enterprise
extension and for plugin selection via ``application.yaml``:

- ``TargetResolver`` — resolve an ``ARCA_``/``TECLAW_``/``LOCAL_`` proxypass
  target into an upstream host plus injected headers / path prefix.
- ``RelayApiClient`` — read/write relay-session state against the upstream BaaS.
- ``ForwardingProxy`` — reverse-proxy an HTTP request to a resolved upstream.
- ``JwtVerifier`` — verify a bearer JWT at the proxy edge.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TargetResolver(Protocol):
    """Resolve a proxypass target string into an upstream destination."""

    prefix: str

    async def resolve(self, target_host: str) -> dict[str, str]:
        """Return a dict describing the resolved upstream.

        Expected keys vary by resolver:
        - ``ARCA_``  → ``pod_ip`` + ``pod_port`` + ``provider_device_id``
          (direct pod-IP upstream)
        - ``TECLAW_`` → ``teclaw_host`` + ``x-target-bot-id`` + extra headers
        - ``LOCAL_`` → ``baas_host`` + ``local_path_prefix``

        Raises ``ValueError`` on malformed target, ``RuntimeError`` on
        missing configuration.
        """
        ...


@runtime_checkable
class RelayApiClient(Protocol):
    """HTTP client for the upstream BaaS ``relay-sessions`` API."""

    async def start(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def upsert_route_active(self, session_id: str) -> bool: ...

    async def get_route_info(self, session_id: str) -> dict[str, Any] | None: ...

    async def mark_route_closed(self, session_id: str) -> bool: ...


@runtime_checkable
class ForwardingProxy(Protocol):
    """Reverse-proxy an HTTP request to a resolved upstream."""

    async def forward(
        self,
        request: Any,
        upstream_url: str,
        target_path: str,
    ) -> Any:
        """Stream ``request`` to ``upstream_url + target_path`` and return the response."""
        ...


@runtime_checkable
class JwtVerifier(Protocol):
    """Verify a bearer JWT token."""

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the decoded payload dict on success, ``None`` on failure."""
        ...

"""OpenClaw gateway HTTP management service (port Protocol).

Decouples the openclaw HTTP router (``/api/openclaw/{test-connection,
disconnect,config}``) from the gateway transport client
(``engine.community.openclaw.client.gateway_client``, shipped in both community and corp
export trees). Both profiles bind the same shared real wrapper over that client; they differ
only by profile selection, not by capability.
"""
from __future__ import annotations

from typing import Protocol


class OpenClawGatewayService(Protocol):
    """HTTP management surface for the OpenClaw gateway connection."""

    async def test_connection(self) -> dict:
        """Return ``{success, connected, gateway_url, server, protocol, features}``.

        Mirrors the corp ``openclaw/router.py:test_connection`` response shape
        so the router's response is byte-identical under corp.
        """
        ...

    async def disconnect(self) -> None:
        """Close the shared gateway client connection."""
        ...

    def get_config(self) -> dict:
        """Return ``{gateway_url, connection_timeout}`` (no secrets)."""
        ...

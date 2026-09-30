"""OpenClaw web_shell ACL adapter.

Implements the core ``WebShellService`` by delegating to an injected
``OpenClawWebShellPort``.  Its two roles:

1. **Auth-signature bridge**: the core ``WebShellService.open_session``
   carries ``auth: AuthContext | None``; the port's ``open_session()``
   takes nothing (legacy ignores auth).  The adapter drops the auth
   argument before forwarding to the port.

2. **Passthrough**: the native PTY session object returned by the port
   carries no core types and structurally satisfies the core
   ``WebShellSession`` Protocol (async read / write / resize / close), so
   it passes through without wrapping.  ``check_token`` is a pure
   passthrough.
"""
from __future__ import annotations

from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.web_shell.protocol import WebShellService
from engine.community.plugin_api.openclaw.web_shell import OpenClawWebShellPort


class OpenClawWebShellAdapter(WebShellService):
    """`WebShellService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawWebShellPort) -> None:
        self._port = port

    def check_token(self, token: str) -> bool:
        """Validate the access token — pure passthrough to the port."""
        return self._port.check_token(token)

    async def open_session(self, auth: AuthContext | None = None) -> Any:
        """Fork a PTY session; auth is accepted but ignored (legacy behaviour).

        Returns the native ``OpenClawWebShellSession`` which structurally
        satisfies ``WebShellSession`` (read / write / resize / close).
        """
        return await self._port.open_session()


__all__ = ["OpenClawWebShellAdapter"]

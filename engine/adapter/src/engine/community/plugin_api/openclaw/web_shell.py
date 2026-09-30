"""OpenClawWebShellPort — native port for PTY debug-terminal operations.

web_shell is local-infra: it forks a shell child directly — no gateway,
no pool, no token.  The PTY session object returned by ``open_session``
lives entirely in the plugins leaf (``plugins/prod/openclaw/web_shell.py``)
and carries no core types; it structurally satisfies the core
``WebShellSession`` Protocol (read / write / resize / close).

``check_token`` is synchronous (HMAC compare or always-True when no token
is configured); ``open_session`` takes no auth argument — the adapter is
the auth-signature bridge that drops the ``auth: AuthContext`` parameter
present on the core ``WebShellService.open_session``.
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawWebShellPort(Protocol):
    """Native PTY terminal operations for the OpenClaw engine."""

    def check_token(self, token_str: str) -> bool:
        """Validate the debug-terminal access token.

        Returns ``True`` when the token matches (HMAC compare) or when no
        token is configured (in-cluster, always allow).  Synchronous.
        """
        ...

    async def open_session(self) -> Any:
        """Fork a new PTY shell session.

        Returns the native ``OpenClawWebShellSession`` object (type ``Any``
        here — the concrete type lives in ``plugins/prod/openclaw/`` so
        importing it would violate the layering).  The object structurally
        satisfies ``core/web_shell/protocol.WebShellSession``
        (async read / write / resize / close).
        """
        ...


__all__ = ["OpenClawWebShellPort"]

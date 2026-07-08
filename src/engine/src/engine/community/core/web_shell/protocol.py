"""
WebShellService Protocol — backs the in-pod /terminal debug shell.

The plugin owns the PTY child-process lifecycle (fork + setuid +
exec(shell)) and the file descriptor; the router only marshals
WebSocket frames to/from a :class:`WebShellSession` returned by
:meth:`WebShellService.open_session`.

The router still owns:

* the static ``/terminal`` HTML page (xterm.js bootstrap, totally
  engine-agnostic)
* the ``/terminal/health`` health endpoint
* WebSocket accept + the gather() of pump tasks
* JSON resize-message parsing

Engines without a debug terminal (or whose terminal is served outside
the OCB process) return ``None`` for ``Engine.web_shell``; the route
501s in that case.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext


@runtime_checkable
class WebShellSession(Protocol):
    """Open PTY session — yielded by :meth:`WebShellService.open_session`.

    Lifetime is tied to the WebSocket connection: the router is
    responsible for calling :meth:`close` when the WS disconnects.
    """

    async def read(self) -> bytes:
        """Block (in an executor) until the PTY has output, then return
        the next chunk. Returns empty bytes on EOF / closed fd."""
        ...

    def write(self, data: bytes) -> None:
        """Write `data` to the PTY's stdin."""
        ...

    def resize(self, cols: int, rows: int) -> None:
        """Set the PTY window size."""
        ...

    def close(self) -> None:
        """Kill the child process and close the fd. Idempotent."""
        ...


@runtime_checkable
class WebShellService(Protocol):
    """Backend opens debug-terminal sessions through this Protocol."""

    def check_token(self, token: str) -> bool:
        """Validate the access token. Engines decide their own policy
        (env-var compare, no auth in dev, etc.)."""
        ...

    async def open_session(
        self, auth: AuthContext | None = None,
    ) -> WebShellSession:
        """Fork a new PTY session and return its session handle."""
        ...


__all__ = ["WebShellService", "WebShellSession"]

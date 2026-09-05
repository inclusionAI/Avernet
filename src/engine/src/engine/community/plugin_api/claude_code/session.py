"""ClaudeCodeSessionPort — native port for session operations.

Sessions are pooled (client + relay), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
dicts / list[dict] / bool / None — the adapter builds the core
``Session`` / ``Message`` DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``sessions_list``           ``sessions.list``
``session_create``          ``sessions.patch``
``session_delete``          ``sessions.delete``
``session_reset``           ``sessions.reset``
``session_get_history``     ``chat.history``
``session_clear``           ``sessions.reset`` (alias semantics)
==========================  ================================================

In-band error convention for ``session_reset`` / ``session_clear``:

  success  -> ``{"success": True,  "payload": <dict>}``
  failure  -> ``{"success": False, "error":   {"code": ..., "message": ...}}``
"""
from __future__ import annotations

from typing import Literal, Protocol


class ClaudeCodeSessionPort(Protocol):
    """Native session operations over the claude_code gateway (vendored Node relay)."""

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
        session_key: str | None = None,
        source: Literal["all_but_others"] | None = None,
        actor_user_id: str | None = None,
    ) -> list[dict]:
        """Call ``sessions.list`` and return raw session dicts.

        ``agent_id`` filtering and exact, non-blank ``session_key`` filtering
        are applied locally before pagination. Returns ``[]`` on gateway error.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
            offset: Pagination offset (0-based).
            limit: Page size (default 50).
            agent_id: Optional agent filter.
            session_key: Optional exact session-key filter; blank values are ignored.
            source: Optional caller-relative visibility filter.
            actor_user_id: Trusted authenticated actor used by ``source``.

        Returns:
            List of raw session dicts after local filtering and pagination.
            A missing actor with ``source`` returns an empty list.
        """
        ...

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Call ``sessions.patch`` to create a new session.

        Args:
            key: Pre-composed session key.
            label: Optional session label.
            model: Optional model id override.
            cwd: Optional working directory for the Claude subprocess.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw session dict as created by the relay.

        Raises:
            RuntimeError: On gateway error.
        """
        ...

    async def session_delete(
        self,
        key: str,
        token: str | None = None,
    ) -> bool:
        """Call ``sessions.delete``; return True on success, False on error.

        Args:
            key: The session key to delete.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def session_reset(
        self,
        key: str,
        token: str | None = None,
    ) -> dict:
        """Call ``sessions.reset`` to reset a session; return the in-band dict.

        Always returns a dict — never raises:

          ``{"success": True,  "payload": <dict>}``
          ``{"success": False, "error":   {"code": ..., "message": ...}}``

        Args:
            key: The session key to reset.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def session_get_history(
        self,
        key: str,
        limit: int = 100,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``chat.history``; return raw message dicts.

        Returns ``[]`` on gateway error or unexpected payload shape.

        Args:
            key: The session key whose history to fetch.
            limit: Maximum number of messages (default 100).
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def session_clear(
        self,
        key: str,
        token: str | None = None,
    ) -> dict:
        """Alias of reset/clear semantics via ``sessions.reset``.

        Uses the same in-band ``{success, error, payload}`` shape as
        ``session_reset`` but is a separate method so the adapter can keep
        distinct call sites for "clear" vs "reset" semantics if they diverge.

        Args:
            key: The session key to clear.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...


__all__ = ["ClaudeCodeSessionPort"]

"""OpenClawSessionPort — native port for session operations.

Session is pooled (client+pool), so port methods take `token: str | None = None`
for per-token routing.  Returns raw dicts/list[dict]/bool/None — the adapter
builds the core `Session` / `Message` / `SessionResetResult` DTOs.

Model normalisation (`_normalize_model_id` + `_build_model_provider_map`) is
impl-side: `sessions_list` returns dicts with already-normalised model strings
so the adapter never needs the provider map (design note S1).

In-band error convention for `session_reset`:
  success  → {"success": True,  "payload": <dict>}
  failure  → {"success": False, "error":   {"code": ..., "message": ...}}
"""
from __future__ import annotations

from typing import Protocol


class OpenClawSessionPort(Protocol):
    """Native session operations over the OpenClaw gateway."""

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
        session_key: str | None = None,
        user_id: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """Orchestrate `sessions.list` + bcs filter + paginate + chat.history + init filter.

        Exact ordering (matches legacy `engines/openclaw/session.py:list`):
          1. Fetch a sufficient newest-session prefix via `sessions.list(limit)`
          2. Filter internal `agent:main:bcs_grp_` sessions and `bcs:group`
             sessions (keep namespaced `bcs_grp_*_dm_*` and bcs-cli)
          3. Apply the optional caller-relative `source` filter
          4. Filter by `agent_id` if provided (request-param-driven but primitive)
          5. Filter by exact non-blank `session_key` if provided
          6. Paginate: slice `[offset : offset+limit]`
          7. Fetch `chat.history` ONLY for the paginated page sessions
          8. Filter out single-message "Bot 初始化配置" sessions from the page
          9. Normalise model strings via `providers.available` (cached)
          10. Return the final page dicts (with `_messages` and `_message_count` populated)

        Pagination is performed BEFORE the per-session `chat.history` RPCs so
        that history is fetched only for the visible page — matching legacy
        behaviour exactly. The gateway prefix is expanded when local filters
        would otherwise leave the requested page incomplete. A page may return
        fewer than `limit` items when
        "Bot 初始化配置" sessions fall within it.

        Returns `[]` on gateway error.
        """
        ...

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Call `sessions.patch` to create a new session and return the raw params dict.

        Raises `RuntimeError` on gateway error.
        """
        ...

    async def session_delete(
        self,
        key: str,
        token: str | None = None,
    ) -> bool:
        """Call `sessions.delete`; return True on success, False on error."""
        ...

    async def session_clear(
        self,
        key: str,
        token: str | None = None,
    ) -> None:
        """Call `sessions.reset` to clear a session's history.

        Raises `RuntimeError` on gateway error.
        """
        ...

    async def chat_history(
        self,
        session_key: str,
        limit: int | None = None,
        token: str | None = None,
    ) -> list[dict]:
        """Call `chat.history`; return raw message dicts.

        Returns `[]` on gateway error or unexpected payload shape.
        """
        ...

    async def session_patch_then_get(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Patch a session via `sessions.patch`, then find it in a full-list scan.

        Relocates the update body intact (patch → sessions.list → find-by-id).
        Returns the raw session dict from the list.  Raises `RuntimeError` when
        the patch fails or the session cannot be located after patching.
        """
        ...

    async def session_reset(
        self,
        session_key: str,
        token: str | None = None,
    ) -> dict:
        """Call `sessions.reset` via the gateway helper; return the in-band dict.

        Always returns a dict — never raises:
          {"success": True,  "payload": <dict>}
          {"success": False, "error":   {"code": ..., "message": ...}}
        """
        ...


__all__ = ["OpenClawSessionPort"]

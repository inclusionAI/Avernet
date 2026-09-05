"""_SessionPortMixin — session CRUD + history (relay RPC, single-tenant)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Literal

log = logging.getLogger("claude-code-community-port")

_USER_SUFFIX = re.compile(r"(?:^|:)user:([^:]+)$")
_SOURCE_ALL_BUT_OTHERS = "all_but_others"


def _session_matches_source(
    session: dict[str, Any], actor_user_id: str,
) -> bool:
    key = session.get("key")
    match = _USER_SUFFIX.search(key) if isinstance(key, str) else None
    return match is None or match.group(1).casefold() == actor_user_id.casefold()


def _session_matches_agent_id(session: dict, agent_id: str) -> bool:
    """Match an explicit relay agentId or the supported session-key forms."""
    if "agentId" in session:
        return session["agentId"] == agent_id

    key = session.get("key")
    if not isinstance(key, str):
        return False
    if key.startswith("agent:"):
        try:
            key_agent_id, remainder = key.removeprefix("agent:").split(
                ":session:", 1)
            session_id, user_id = remainder.split(":user:", 1)
        except ValueError:
            return False
        return bool(key_agent_id and session_id and user_id) and key_agent_id == agent_id
    if key.startswith("user:"):
        try:
            user_id, remainder = key.removeprefix("user:").split(":session:", 1)
            session_id, key_agent_id = remainder.split(":agent:", 1)
        except ValueError:
            return False
        return bool(user_id and session_id and key_agent_id) and key_agent_id == agent_id
    return False


class _SessionPortMixin:
    """Domain mixin: sessions.{list,patch,delete,reset} + chat.history."""

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
        started_at = time.monotonic()
        if source not in (None, _SOURCE_ALL_BUT_OTHERS):
            log.warning(
                "event=claude_code.sessions.list.denied operation=sessions.list "
                "reason=invalid_source status=422 result=empty duration_ms=%.3f",
                (time.monotonic() - started_at) * 1000,
            )
            return []
        log.info(
            "event=claude_code.sessions.list.request operation=sessions.list "
            "source=%s actor_present=%s agent_filter_present=%s "
            "session_key_filter_present=%s offset=%s limit=%s duration_ms=%.3f",
            source,
            bool(actor_user_id),
            agent_id is not None,
            bool(session_key and session_key.strip()),
            offset,
            limit,
            (time.monotonic() - started_at) * 1000,
        )
        if source == _SOURCE_ALL_BUT_OTHERS and (
            not isinstance(actor_user_id, str) or not actor_user_id.strip()
        ):
            log.warning(
                "event=claude_code.sessions.list.denied operation=sessions.list "
                "reason=actor_unavailable status=401 result=empty duration_ms=%.3f",
                (time.monotonic() - started_at) * 1000,
            )
            return []
        try:
            client = await self._relay()
            resp = await client.send_request("sessions.list", {})
        except Exception as e:  # noqa: BLE001
            log.error(
                "event=claude_code.sessions.list.failure operation=sessions.list "
                "reason=rpc_error status=503 error_type=%s duration_ms=%.3f",
                type(e).__name__,
                (time.monotonic() - started_at) * 1000,
            )
            return []
        if not resp.ok:
            log.error(
                "event=claude_code.sessions.list.failure operation=sessions.list "
                "reason=rpc_error status=502 error_type=relay_error "
                "duration_ms=%.3f",
                (time.monotonic() - started_at) * 1000,
            )
            return []
        payload = resp.payload or []
        if isinstance(payload, dict):
            sessions = payload.get("sessions", [])
            if not isinstance(sessions, list):
                log.warning(
                    "[sessions_list] malformed nested sessions shape type=%s",
                    type(sessions).__name__,
                )
                return []
        elif isinstance(payload, list):
            sessions = payload
        else:
            return []
        sessions = [s for s in sessions if isinstance(s, dict)]
        raw_count = len(sessions)
        # agent_id filter (relay doesn't filter server-side for this field).
        if agent_id is not None:
            before_count = len(sessions)
            sessions = [s for s in sessions if _session_matches_agent_id(s, agent_id)]
            log.info(
                "[sessions_list] agent_filter has_agent_id=%s before=%s matched=%s",
                True,
                before_count,
                len(sessions),
            )
        requested_session_key = session_key if session_key and session_key.strip() else None
        if requested_session_key is not None:
            before_count = len(sessions)
            # Filter before pagination so an exact key beyond the current page is found.
            sessions = [item for item in sessions if item.get("key") == requested_session_key]
            log.info(
                "[sessions_list] session_key_filter has_session_key=%s before=%s matched=%s",
                True,
                before_count,
                len(sessions),
            )
        if source == _SOURCE_ALL_BUT_OTHERS:
            before_count = len(sessions)
            sessions = [
                item for item in sessions
                if _session_matches_source(item, actor_user_id)
            ]
            log.info(
                "[sessions_list] source_filter source=%s before=%s matched=%s",
                source,
                before_count,
                len(sessions),
            )
        page = sessions[offset: offset + limit]
        log.info(
            "event=claude_code.sessions.list.success operation=sessions.list status=200 "
            "raw_count=%s matched_count=%s returned_count=%s source=%s "
            "duration_ms=%.3f",
            raw_count,
            len(sessions),
            len(page),
            source,
            (time.monotonic() - started_at) * 1000,
        )
        return page

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        token: str | None = None,
    ) -> dict:
        client = await self._relay()
        params: dict[str, Any] = {
            "key": key,
            "permissionMode": "bypassPermissions",
        }
        if label is not None:
            params["label"] = label
        if model is not None:
            params["model"] = model
        if cwd is not None:
            params["cwd"] = cwd
        resp = await client.send_request("sessions.patch", params, timeout=60.0)
        if not resp.ok:
            raise RuntimeError(
                f"Failed to create session: "
                f"{resp.error.message if resp.error else 'unknown'}"
            )
        return resp.payload if isinstance(resp.payload, dict) else {"key": key}

    async def session_delete(self, key: str, token: str | None = None) -> bool:
        client = await self._relay()
        try:
            resp = await client.send_request(
                "sessions.delete", {"key": key, "force": True})
        except Exception as e:  # noqa: BLE001
            log.error("[session_delete] RPC failed: %s", e)
            return False
        return bool(resp.ok)

    async def session_reset(self, key: str, token: str | None = None) -> dict:
        return await self._reset_inband(key, "sessionKey")

    async def session_clear(self, key: str, token: str | None = None) -> dict:
        return await self._reset_inband(key, "key")

    async def _reset_inband(self, key: str, param_name: str) -> dict:
        """``sessions.reset`` — mirror the corp key-name inconsistency:
        ``session_reset`` uses ``sessionKey``, ``session_clear`` uses ``key``."""
        try:
            client = await self._relay()
        except Exception as e:  # noqa: BLE001
            return {"success": False,
                    "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        try:
            resp = await client.send_request(
                "sessions.reset", {param_name: key}, timeout=15.0)
        except Exception as e:  # noqa: BLE001
            log.exception("[sessions.reset] RPC failed: %s", e)
            return {"success": False,
                    "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        if resp.ok:
            return {"success": True, "payload": resp.payload or {}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}

    async def session_get_history(
        self,
        key: str,
        limit: int = 100,
        token: str | None = None,
    ) -> list[dict]:
        client = await self._relay()
        try:
            resp = await client.send_request(
                "chat.history", {"sessionKey": key, "limit": limit})
        except Exception as e:  # noqa: BLE001
            log.error("[session_get_history] RPC failed: %s", e)
            return []
        if not resp.ok:
            return []
        payload = resp.payload or []
        if isinstance(payload, dict):
            messages = payload.get("messages", [])
        elif isinstance(payload, list):
            messages = payload
        else:
            return []
        return [m for m in messages if isinstance(m, dict)]

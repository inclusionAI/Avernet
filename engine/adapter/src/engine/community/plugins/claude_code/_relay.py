"""_RelayPortMixin — transparent relay forwarding."""
from __future__ import annotations

import logging

log = logging.getLogger("claude-code-community-port")


class _RelayPortMixin:
    """Domain mixin: relay_forward_request / relay_forward_raw_frame."""

    async def relay_forward_request(
        self,
        method: str,
        params: dict | None = None,
        request_id: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Forward an arbitrary ``req`` frame upstream; return the raw dict."""
        client = await self._relay()
        if request_id is not None:
            resp = await client.send_request_with_id(
                request_id=request_id, method=method,
                params=params, timeout=30.0)
        else:
            resp = await client.send_request(method, params, timeout=30.0)
        if resp.ok:
            return {"success": True, "payload": resp.payload or {}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}

    async def relay_forward_raw_frame(self, frame: dict,
                                      token: str | None = None) -> dict:
        """Forward a raw (non-req) frame upstream.

        The vendored relay client only supports req/res frames over its public
        surface today, so raw frames (ping/notify) are a near-no-op: we ack
        success. This mirrors the corp ``ClaudeCodeRelayService.forward_raw_frame``
        which is also a no-op.
        """
        log.debug("[relay_forward_raw_frame] type=%s (no-op)", frame.get("type"))
        return {"success": True}

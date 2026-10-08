"""_RelayPortMixin — forward_request and forward_raw_frame port methods."""
from __future__ import annotations

import logging
from typing import Any

from engine.community.kernel.frames import ResponseFrame

log = logging.getLogger("openclaw-port")


class _RelayPortMixin:
    """Domain mixin: relay / frame forwarding (pooled, per-token)."""

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Forward an unknown `req` frame upstream and return the raw ResponseFrame.

        Relocated intact from `engines/openclaw/relay.py:forward_request` up to
        the raw ResponseFrame return; no DTO build needed (ResponseFrame is a
        kernel type).
        """
        client = await self._pooled_client(token)
        log.debug(f"[forward_request] method={method} id={request_id}")
        return await client.send_request_with_id(
            request_id=request_id,
            method=method,
            params=params,
            timeout=timeout,
        )

    async def forward_raw_frame(
        self,
        frame: dict[str, Any],
        token: str | None = None,
    ) -> None:
        """Forward a raw (non-req) frame upstream.

        Relocated intact from `engines/openclaw/relay.py:forward_raw_frame`.
        """
        client = await self._pooled_client(token)
        log.debug(f"[forward_raw_frame] type={frame.get('type')}")
        await client.send_raw_frame(frame)

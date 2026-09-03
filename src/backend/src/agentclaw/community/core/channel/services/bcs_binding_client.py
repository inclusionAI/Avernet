"""BCS channel-binding client for ``bcn_gateway`` channels.

``ac_channel_config`` stays the configuration source of truth; this client
projects a row into the BCS collaboration surface, which owns runtime message
routing (per-sender sessions, multi-instance affinity) for those channels.

Wire contract mirrors
``src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/`` (routes/channel.rs,
dto/channel.rs); the BCS envelope is ``{code, message, data, request_id}``.

NOTE (integration blocker, spec §7): the BCS bindings routes currently
authenticate a human session (``require_authenticated_user``). Live calls need
BCS-side service-token support; until then this client is exercised through
fakes/mocked transport only.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelSyncError,
)
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.log import get_logger

logger = get_logger()

_BINDING_PATH = "/openapi/v1/collaboration/channels/bindings"


@runtime_checkable
class BcsChannelBindingClientProtocol(Protocol):
    """Port used by ``ChannelService`` to orchestrate BCS bindings."""

    async def ensure_active(self, channel: ChannelRecord) -> str:
        """Create (or reactivate) the binding; returns the BCS binding id.

        Recovers a lost ``bcs_binding_id`` via the by-target lookup when BCS
        answers 409 on create, so activation stays idempotent.
        """
        ...

    async def push_config(
        self, channel: ChannelRecord, *, binding_id: str
    ) -> None:
        """Full-replace the binding config (agentclaw is the source of truth)."""
        ...

    async def set_active(self, binding_id: str, *, active: bool) -> None: ...

    async def delete_binding(self, binding_id: str) -> None: ...


def _binding_payload(channel: ChannelRecord) -> dict[str, Any]:
    """Map a stored bcn_gateway channel row to the BCS create-body shape."""
    config = channel.config
    if config.get("enable_streaming_cards", False):
        send_mode: dict[str, Any] = {
            "mode": "streaming_card",
            "card_template_id": config.get("card_template_id") or "",
            "fallback_message_type": "markdown",
        }
    else:
        send_mode = {"mode": "normal", "message_type": "markdown"}
    return {
        "channel_type": "dingtalk",
        "account_ref": config.get("client_id") or "",
        "target": {"bot": {"bot_id": channel.bind_bot_id}},
        "group_chat_scope": config.get("group_chat_scope", "per_sender"),
        "outbound_visibility": config.get("outbound_visibility", "full_transcript"),
        "config": {
            "robot_code": config.get("robot_code") or config.get("client_id") or "",
            "client_id": config.get("client_id") or "",
            "client_secret": config.get("client_secret") or "",
            "send_mode": send_mode,
        },
    }


class HttpBcsChannelBindingClient:
    """HTTP implementation against the BCS collaboration OpenAPI."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._transport = transport

    def _require_configured(self) -> None:
        if not self._base_url:
            raise ChannelSyncError("BCS binding client is not configured")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        return headers

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._require_configured()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json_body,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise ChannelSyncError("BCS binding request failed") from exc
        if response.status_code == 409:
            raise ChannelBindingConflictError(
                "BCS binding conflicts with an existing binding"
            )
        if response.status_code >= 400:
            raise ChannelSyncError(
                f"BCS binding request failed: {response.status_code}"
            )
        return response.json()

    async def ensure_active(self, channel: ChannelRecord) -> str:
        binding_id = str(channel.config.get("bcs_binding_id") or "")
        if binding_id:
            await self.set_active(binding_id, active=True)
            return binding_id
        try:
            envelope = await self._request(
                "POST", _BINDING_PATH, json_body=_binding_payload(channel)
            )
        except ChannelBindingConflictError:
            recovered = await self._find_binding_id(channel)
            if recovered is None:
                raise
            await self.set_active(recovered, active=True)
            return recovered
        return str(envelope["data"]["id"])

    async def _find_binding_id(self, channel: ChannelRecord) -> str | None:
        """by-target lookup to recover a binding id after a lost writeback."""
        query = (
            "?target_type=bot"
            f"&target_id={quote(channel.bind_bot_id)}"
            "&channel_type=dingtalk"
        )
        envelope = await self._request("GET", f"{_BINDING_PATH}/by-target{query}")
        account_ref = channel.config.get("client_id") or ""
        for item in envelope.get("data", {}).get("items", []):
            if item.get("account_ref") == account_ref:
                recovered = str(item.get("id") or "")
                return recovered or None
        return None

    async def push_config(
        self, channel: ChannelRecord, *, binding_id: str
    ) -> None:
        if not binding_id:
            raise ChannelSyncError("channel has no bcs_binding_id to update")
        config = _binding_payload(channel)["config"]
        await self._request(
            "PATCH", f"{_BINDING_PATH}/{binding_id}", json_body={"config": config}
        )

    async def set_active(self, binding_id: str, *, active: bool) -> None:
        if not binding_id:
            raise ChannelSyncError("channel has no bcs_binding_id to update")
        await self._request(
            "PATCH",
            f"{_BINDING_PATH}/{binding_id}",
            json_body={"active": active},
        )

    async def delete_binding(self, binding_id: str) -> None:
        await self._request("DELETE", f"{_BINDING_PATH}/{binding_id}")

"""Response models for the connection endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import SocketKind


class Socket(BaseModel):
    """One WebSocket you may open against the bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "kind": "chat",
                "url": (
                    "wss://gateway.example/openapi/v1/bots/messages/abc/api/openclaw/ws"
                    "?x-proxypass-token=…"
                ),
            }
        }
    )

    kind: SocketKind = Field(description="Which socket this is.")
    url: str = Field(
        description="Complete WebSocket URL, credential included. Open it as "
        "given — do not append to it, rebuild it, or move any part of it into "
        "a header; a browser cannot set headers on a WebSocket handshake, "
        "which is why everything needed is in the URL."
    )


class Connection(BaseModel):
    """Ready-to-use socket connections for a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "engine": "openclaw",
                "expires_at": "2026-07-30T14:30:00+00:00",
                "sockets": [
                    {
                        "kind": "chat",
                        "url": (
                            "wss://gateway.example/openapi/v1/bots/messages/abc/api/openclaw/ws"
                            "?x-proxypass-token=…"
                        ),
                    }
                ],
            }
        }
    )

    engine: str = Field(description="The bot's active engine.")
    expires_at: str = Field(
        description="Deadline for *opening* the sockets here (ISO 8601). It "
        "bounds the handshake only: a socket you already opened stays open "
        "past this instant, because the credential is checked once at connect "
        "time. Request this endpoint again before connecting or reconnecting — "
        "not on a timer to keep a live socket alive. An expired credential "
        "fails the handshake rather than reporting why."
    )
    sockets: list[Socket] = Field(
        description="Exactly the sockets this bot offers. A kind absent from "
        "the list is not available for this bot."
    )


__all__ = ["Connection", "Socket"]

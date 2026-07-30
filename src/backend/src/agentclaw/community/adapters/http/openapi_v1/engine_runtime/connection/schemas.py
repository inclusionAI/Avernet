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
                "url": "wss://gateway.example/route/abc/api/openclaw/ws",
                "headers": {"x-proxypass-token": "…"},
            }
        }
    )

    kind: SocketKind = Field(description="Which socket this is.")
    url: str = Field(
        description="Complete WebSocket URL. Open it as given — do not append "
        "to it or rebuild it."
    )
    headers: dict[str, str] = Field(
        description="Headers to send on the upgrade request."
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
                        "url": "wss://gateway.example/route/abc/api/openclaw/ws",
                        "headers": {"x-proxypass-token": "…"},
                    }
                ],
            }
        }
    )

    engine: str = Field(description="The bot's active engine.")
    expires_at: str = Field(
        description="When every URL and credential here stops working (ISO "
        "8601). Request this endpoint again before then; an expired socket "
        "fails to connect rather than reporting why."
    )
    sockets: list[Socket] = Field(
        description="Exactly the sockets this bot offers. A kind absent from "
        "the list is not available for this bot."
    )


__all__ = ["Connection", "Socket"]

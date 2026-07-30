"""Request/response models for the engine (read-only) group."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EngineStatus(BaseModel):
    """Runtime state of the bot's active engine."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "engine": "openclaw",
                "active_connections": 2,
                "running": True,
            }
        }
    )

    engine: str = Field(
        description="The bot's active engine. A free string, not an enum: the "
        "supported set is deployment configuration (the ENGINE_TYPES "
        "environment variable), not a closed vocabulary."
    )
    active_connections: int = Field(
        description="Number of live connections the engine is currently serving."
    )
    running: bool = Field(
        description="Whether the engine process is up. False while the engine "
        "is starting, restarting, or has failed."
    )


class EngineCapabilities(BaseModel):
    """What the bot's engine can do — the discovery endpoint for this surface.

    Capability names are strings, not an enum. The engine's own ``Capability``
    enum is closed but explicitly documented as "adding new entries is safe", so
    a strict enum on this response would turn a backward-compatible engine
    release into a public 500.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "supported": ["session.list", "session.create", "model.list"],
                "limited": ["session.create"],
                "unavailable": ["mcp.start"],
            }
        }
    )

    supported: list[str] = Field(
        description="Capabilities the engine serves as documented."
    )
    limited: list[str] = Field(
        description="Capabilities the engine serves with a documented "
        "limitation; results may be incomplete. Names only — the engine's own "
        "explanation is internal engineering text and is not published."
    )
    unavailable: list[str] = Field(
        description="Capabilities the engine does not serve. Calling an endpoint "
        "that needs one returns 501."
    )


class EngineInfo(BaseModel):
    """One engine available on the bot's device."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"engine": "openclaw", "version": "1.0.0", "active": True}
        }
    )

    engine: str = Field(description="Engine name.")
    version: str = Field(description="Engine version; empty if not reported.")
    active: bool = Field(description="Whether this is the bot's active engine.")


__all__ = ["EngineCapabilities", "EngineInfo", "EngineStatus"]

"""Request/response models for the engine (read-only) group."""

from __future__ import annotations

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

    # Typed `str`, not an enum: the supported set is deployment configuration
    # (ENGINE_TYPES), not a closed vocabulary.
    engine: str = Field(description="The bot's active engine.")
    active_connections: int = Field(
        description="Number of live connections the engine is currently serving."
    )
    running: bool = Field(
        description="Whether the engine process is up. False while the engine "
        "is starting, restarting, or has failed."
    )


class EngineCapabilities(BaseModel):
    """What the bot's engine can do.

    Call this before relying on any other endpoint in this group: what a bot
    supports depends on its engine, so the same request can succeed for one of
    your bots and be refused for another.
    """

    # Capability names are strings, not an enum: the engine's own Capability
    # enum is closed but documented as "adding new entries is safe", so a strict
    # enum here would break on a backward-compatible engine release.

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "supported": ["session.list", "session.create", "model.list"],
                "limited": ["session.create"],
                "unavailable": ["mcp.start"],
            }
        }
    )

    supported: list[str] = Field(description="Capabilities the bot serves fully.")
    # Names only: the engine's own explanation of each limitation is internal
    # engineering text and is not published.
    limited: list[str] = Field(
        description="Capabilities the bot serves with a limitation; results for "
        "these may be incomplete."
    )
    # Sourced from the engine's `fallback` map, which it populates only for
    # capabilities that have a declared alternative route. A capability the
    # engine simply does not declare appears in none of the three lists — hence
    # the "not exhaustive" wording, which callers must not read past.
    unavailable: list[str] = Field(
        description="Capabilities this bot explicitly reports as not offered. "
        "**Not exhaustive** — treat `supported` and `limited` as the "
        "authoritative test: a capability absent from all three lists is also "
        "unavailable, and an endpoint needing it answers 501."
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
